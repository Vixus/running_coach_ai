"""Athlete-facing story endpoints.

Per `specs/006-athlete-story/contracts/athlete-api.md`. All routes require login
(@login_required) and are scoped to `session["athlete_id"]`.

Endpoints:
- POST /api/stories/opt-in
- POST /api/stories/intro-acknowledged
- GET  /api/stories
- GET  /api/stories/current
- POST /api/stories/<id>/template
- POST /api/stories/<id>/regenerate
- POST /api/stories/<id>/publish
- POST /api/stories/<id>/unpublish
- DELETE /api/stories/<id>
- POST /api/stories/sessions/<id>/respond
- POST /api/stories/sessions/<id>/decline
- POST /api/stories/current/images
- GET  /api/stories/images/<id>/raw
- PATCH /api/stories/images/<id>
- DELETE /api/stories/images/<id>
"""

from __future__ import annotations

import io
import logging
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file, session
from werkzeug.utils import secure_filename

from running_coach_ai.config import settings
from running_coach_ai.coach.story import (
    RegenerationCooldownError,
    StoryGenerationError,
    capture_chat_answer,
    close_session,
    next_question,
    record_option_answer,
    refresh_cover_image_path,
    regenerate_story,
)
from running_coach_ai.coach.story_templates import (
    is_registered,
    list_templates,
)
from running_coach_ai.database.models import (
    Athlete,
    AthleteStory,
    StoryImage,
    StoryInterviewSession,
    StoryQuestion,
)
from running_coach_ai.database.session import get_session, scoped_query
from running_coach_ai.web.auth import login_required

logger = logging.getLogger(__name__)
bp = Blueprint("stories", __name__)

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_IMAGES_PER_STORY = 5


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _serialise_image(img: StoryImage) -> dict:
    return {
        "id": img.id,
        "url": f"/api/stories/images/{img.id}/raw",
        "caption": img.caption,
        "sort_order": img.sort_order,
    }


def _serialise_story_summary(s: AthleteStory, image_count: int = 0) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "milestone_type": s.milestone_type,
        "template_key": s.template_key,
        "template_locked_by_athlete": s.template_locked_by_athlete,
        "created_at": s.created_at.isoformat() + "Z" if s.created_at else None,
        "published_at": (s.published_at.isoformat() + "Z") if s.published_at else None,
        "preview_url": f"/story/{s.share_token}?preview=1",
        "share_url": f"/story/{s.share_token}",
        "regeneration_count": s.regeneration_count,
        "image_count": image_count,
    }


def _serialise_story_full(s: AthleteStory, db) -> dict:
    images = sorted(s.images, key=lambda x: (x.sort_order, x.id))
    available = [
        {"key": tpl.key, "display_name": tpl.display_name, "thumbnail": tpl.thumbnail_url()}
        for tpl in list_templates()
    ]
    can_regen = (
        (s.last_regenerated_at + timedelta(hours=1)).isoformat() + "Z"
        if s.last_regenerated_at else (s.created_at.isoformat() + "Z" if s.created_at else None)
    )
    return {
        "id": s.id,
        "title": s.title,
        "milestone_type": s.milestone_type,
        "editorial_body": s.editorial_body,
        "template_key": s.template_key,
        "template_locked_by_athlete": s.template_locked_by_athlete,
        "available_templates": available,
        "images": [_serialise_image(i) for i in images],
        "share_token": s.share_token,
        "preview_url": f"/story/{s.share_token}?preview=1",
        "share_url": f"/story/{s.share_token}",
        "published_at": (s.published_at.isoformat() + "Z") if s.published_at else None,
        "created_at": s.created_at.isoformat() + "Z" if s.created_at else None,
        "regeneration_count": s.regeneration_count,
        "can_regenerate_at": can_regen,
    }


# ---------------------------------------------------------------------------
# Opt-in & intro modal
# ---------------------------------------------------------------------------


@bp.route("/api/stories/opt-in", methods=["POST"])
@login_required
def opt_in():
    data = request.get_json() or {}
    opted_in = bool(data.get("opted_in"))
    athlete_id = session["athlete_id"]
    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if athlete is None:
            return jsonify({"error": "Athlete not found"}), 404
        athlete.story_opt_in = opted_in
        if not opted_in:
            # Close any active session
            active = (
                scoped_query(db, StoryInterviewSession, athlete_id)
                .filter(StoryInterviewSession.completed_at.is_(None))
                .first()
            )
            if active is not None:
                close_session(active, db, skipped=True)
        db.commit()
        return jsonify({"ok": True, "story_opt_in": athlete.story_opt_in})


@bp.route("/api/stories/intro-acknowledged", methods=["POST"])
@login_required
def intro_acknowledged():
    athlete_id = session["athlete_id"]
    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if athlete is None:
            return jsonify({"error": "Athlete not found"}), 404
        athlete.story_intro_seen_at = datetime.utcnow()
        db.commit()
        return jsonify({
            "ok": True,
            "story_intro_seen_at": athlete.story_intro_seen_at.isoformat() + "Z",
        })


# ---------------------------------------------------------------------------
# Story collection
# ---------------------------------------------------------------------------


@bp.route("/api/stories", methods=["GET"])
@login_required
def list_stories():
    athlete_id = session["athlete_id"]
    with get_session() as db:
        rows = (
            scoped_query(db, AthleteStory, athlete_id)
            .filter(AthleteStory.deleted_at.is_(None))
            .order_by(AthleteStory.created_at.desc())
            .all()
        )
        return jsonify({
            "stories": [
                _serialise_story_summary(s, image_count=len(s.images))
                for s in rows
            ],
        })


@bp.route("/api/stories/current", methods=["GET"])
@login_required
def current_story():
    athlete_id = session["athlete_id"]
    with get_session() as db:
        s = (
            scoped_query(db, AthleteStory, athlete_id)
            .filter(AthleteStory.deleted_at.is_(None))
            .order_by(AthleteStory.created_at.desc())
            .first()
        )
        if s is None:
            return jsonify({"story": None})
        return jsonify({"story": _serialise_story_full(s, db)})


def _scoped_story_or_404(db, story_id: int, athlete_id: int):
    s = (
        scoped_query(db, AthleteStory, athlete_id)
        .filter(AthleteStory.id == story_id, AthleteStory.deleted_at.is_(None))
        .first()
    )
    return s


@bp.route("/api/stories/<int:story_id>/template", methods=["POST"])
@login_required
def swap_template(story_id: int):
    athlete_id = session["athlete_id"]
    data = request.get_json() or {}
    new_key = (data.get("template_key") or "").strip()

    if not is_registered(new_key):
        return jsonify({
            "error": f"Unknown template key: {new_key}",
            "available": [t.key for t in list_templates()],
        }), 400

    with get_session() as db:
        s = _scoped_story_or_404(db, story_id, athlete_id)
        if s is None:
            return jsonify({"error": "Story not found"}), 404
        s.template_key = new_key
        s.template_locked_by_athlete = True
        db.commit()
        return jsonify({
            "ok": True,
            "preview_url": f"/story/{s.share_token}?preview=1",
            "template_key": s.template_key,
        })


@bp.route("/api/stories/<int:story_id>/regenerate", methods=["POST"])
@login_required
def regenerate(story_id: int):
    athlete_id = session["athlete_id"]
    with get_session() as db:
        s = _scoped_story_or_404(db, story_id, athlete_id)
        if s is None:
            return jsonify({"error": "Story not found"}), 404
        try:
            regenerate_story(s, db, by_admin=False)
        except RegenerationCooldownError as e:
            return jsonify({
                "error": "Regeneration cooldown active",
                "retry_after": e.retry_after_seconds,
            }), 429
        except StoryGenerationError:
            return jsonify({"error": "Story generation failed"}), 500
        return jsonify({"ok": True, "story_id": s.id})


@bp.route("/api/stories/<int:story_id>/publish", methods=["POST"])
@login_required
def publish(story_id: int):
    athlete_id = session["athlete_id"]
    with get_session() as db:
        s = _scoped_story_or_404(db, story_id, athlete_id)
        if s is None:
            return jsonify({"error": "Story not found"}), 404
        s.published_at = datetime.utcnow()
        db.commit()
        return jsonify({
            "ok": True,
            "share_url": f"/story/{s.share_token}",
            "published_at": s.published_at.isoformat() + "Z",
        })


@bp.route("/api/stories/<int:story_id>/unpublish", methods=["POST"])
@login_required
def unpublish(story_id: int):
    athlete_id = session["athlete_id"]
    with get_session() as db:
        s = _scoped_story_or_404(db, story_id, athlete_id)
        if s is None:
            return jsonify({"error": "Story not found"}), 404
        s.published_at = None
        db.commit()
        return jsonify({"ok": True, "story_id": s.id})


@bp.route("/api/stories/<int:story_id>", methods=["DELETE"])
@login_required
def soft_delete(story_id: int):
    athlete_id = session["athlete_id"]
    with get_session() as db:
        s = _scoped_story_or_404(db, story_id, athlete_id)
        if s is None:
            return jsonify({"error": "Story not found"}), 404
        s.deleted_at = datetime.utcnow()
        db.commit()
        return jsonify({"ok": True, "story_id": s.id})


# ---------------------------------------------------------------------------
# Interview session interactions
# ---------------------------------------------------------------------------


def _scoped_session_or_404(db, session_id: int, athlete_id: int):
    return (
        scoped_query(db, StoryInterviewSession, athlete_id)
        .filter(StoryInterviewSession.id == session_id)
        .first()
    )


@bp.route("/api/stories/sessions/<int:session_id>/respond", methods=["POST"])
@login_required
def respond_to_session(session_id: int):
    athlete_id = session["athlete_id"]
    data = request.get_json() or {}
    question_id = data.get("question_id")
    option = (data.get("option") or "").strip()

    if not isinstance(question_id, int) or not option:
        return jsonify({"error": "question_id (int) and option (string) required"}), 400

    with get_session() as db:
        sess = _scoped_session_or_404(db, session_id, athlete_id)
        if sess is None:
            return jsonify({"error": "Session not found"}), 404
        question = (
            db.query(StoryQuestion)
            .filter(
                StoryQuestion.id == question_id,
                StoryQuestion.session_id == sess.id,
                StoryQuestion.athlete_id == athlete_id,
                StoryQuestion.answered_at.is_(None),
            )
            .first()
        )
        if question is None:
            return jsonify({"error": "Question not unanswered or not in this session"}), 400

        # Validate the option is in the original list (security: athlete can't inject text)
        if option not in (question.options_json or []):
            # If the athlete picked something other than a registered option, treat
            # as a custom answer instead of rejecting.
            from running_coach_ai.coach.story import capture_chat_answer
            athlete = db.get(Athlete, athlete_id)
            capture_chat_answer(athlete, option, db)
        else:
            record_option_answer(question, option, db)

            # If this was the consent prompt and the athlete declined, close immediately.
            if question.question_index == 1 and option in {"Maybe later", "Skip"}:
                close_session(sess, db, skipped=True)
                return jsonify({"ok": True, "session_complete": True, "next_question": None})

            # Otherwise generate the next question
            new_q = next_question(sess, db)
            if new_q is None:
                return jsonify({"ok": True, "session_complete": True, "next_question": None})
            return jsonify({
                "ok": True,
                "session_complete": False,
                "next_question": {
                    "question_id": new_q.id,
                    "question": new_q.question,
                    "options": new_q.options_json,
                },
            })

        # capture_chat_answer path — return current state
        db.refresh(sess)
        if sess.completed_at is not None:
            return jsonify({"ok": True, "session_complete": True, "next_question": None})
        # Find next pending question (capture_chat_answer queues the next via next_question)
        nq = (
            db.query(StoryQuestion)
            .filter(
                StoryQuestion.session_id == sess.id,
                StoryQuestion.answered_at.is_(None),
            )
            .order_by(StoryQuestion.question_index.asc())
            .first()
        )
        if nq is None:
            return jsonify({"ok": True, "session_complete": True, "next_question": None})
        return jsonify({
            "ok": True,
            "session_complete": False,
            "next_question": {
                "question_id": nq.id,
                "question": nq.question,
                "options": nq.options_json,
            },
        })


@bp.route("/api/stories/sessions/<int:session_id>/decline", methods=["POST"])
@login_required
def decline_session(session_id: int):
    athlete_id = session["athlete_id"]
    with get_session() as db:
        sess = _scoped_session_or_404(db, session_id, athlete_id)
        if sess is None:
            return jsonify({"error": "Session not found"}), 404
        close_session(sess, db, skipped=True)
        return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Image upload / management
# ---------------------------------------------------------------------------


def _validate_image_upload(file_storage):
    """Return (raw_bytes, ext) on success or (None, error_message)."""
    if not file_storage or not file_storage.filename:
        return None, "No file provided"

    raw = file_storage.read()
    if len(raw) > _MAX_IMAGE_BYTES:
        return None, "File exceeds 10 MB"

    mime = (file_storage.mimetype or "").lower()
    if mime not in _ALLOWED_MIME:
        return None, "Unsupported file type"

    try:
        from PIL import Image, UnidentifiedImageError
        Image.open(io.BytesIO(raw)).verify()
    except Exception:
        return None, "Could not decode image"

    ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    return raw, ext_map[mime]


def _current_in_progress_story(db, athlete_id: int) -> AthleteStory | None:
    return (
        scoped_query(db, AthleteStory, athlete_id)
        .filter(AthleteStory.deleted_at.is_(None))
        .order_by(AthleteStory.created_at.desc())
        .first()
    )


@bp.route("/api/stories/current/images", methods=["POST"])
@login_required
def upload_image():
    athlete_id = session["athlete_id"]
    file_storage = request.files.get("file")
    raw, marker = _validate_image_upload(file_storage)
    if raw is None:
        return jsonify({"error": marker}), 400

    caption = (request.form.get("caption") or "").strip() or None

    with get_session() as db:
        story = _current_in_progress_story(db, athlete_id)
        if story is None:
            return jsonify({"error": "No story in progress"}), 400

        existing_count = len(story.images)
        if existing_count >= _MAX_IMAGES_PER_STORY:
            return jsonify({"error": f"Maximum {_MAX_IMAGES_PER_STORY} photos per story"}), 400

        story_dir = Path(settings.STORY_IMAGE_DIR) / str(story.id)
        story_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{secrets.token_urlsafe(8)}.{marker}"
        dest = story_dir / filename
        with open(dest, "wb") as fh:
            fh.write(raw)

        img = StoryImage(
            story_id=story.id,
            filename=filename,
            caption=caption,
            sort_order=existing_count,
        )
        db.add(img)
        db.flush()

        if existing_count == 0:
            story.cover_image_path = filename
        db.commit()
        return jsonify({"image": _serialise_image(img)}), 201


def _scoped_image_or_404(db, image_id: int, athlete_id: int):
    return (
        db.query(StoryImage)
        .join(AthleteStory, AthleteStory.id == StoryImage.story_id)
        .filter(
            StoryImage.id == image_id,
            AthleteStory.athlete_id == athlete_id,
            AthleteStory.deleted_at.is_(None),
        )
        .first()
    )


@bp.route("/api/stories/images/<int:image_id>/raw", methods=["GET"])
@login_required
def get_image_raw(image_id: int):
    athlete_id = session["athlete_id"]
    with get_session() as db:
        img = _scoped_image_or_404(db, image_id, athlete_id)
        if img is None:
            return ("Not found", 404)
        path = Path(settings.STORY_IMAGE_DIR) / str(img.story_id) / img.filename
        if not path.is_file():
            return ("Not found", 404)
        ext = img.filename.rsplit(".", 1)[-1].lower()
        mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
              "png": "image/png", "webp": "image/webp"}.get(ext, "application/octet-stream")
        return send_file(str(path), mimetype=mt)


@bp.route("/api/stories/images/<int:image_id>", methods=["PATCH"])
@login_required
def patch_image(image_id: int):
    athlete_id = session["athlete_id"]
    data = request.get_json() or {}
    with get_session() as db:
        img = _scoped_image_or_404(db, image_id, athlete_id)
        if img is None:
            return jsonify({"error": "Not found"}), 404
        if "caption" in data:
            img.caption = (data.get("caption") or "").strip() or None
        if "sort_order" in data:
            try:
                img.sort_order = int(data["sort_order"])
            except (TypeError, ValueError):
                return jsonify({"error": "sort_order must be an integer"}), 400
        db.commit()

        # If sort_order changed, refresh the cover_image_path
        story = db.get(AthleteStory, img.story_id)
        if story:
            refresh_cover_image_path(story, db)
        return jsonify({"ok": True, "image": _serialise_image(img)})


@bp.route("/api/stories/images/<int:image_id>", methods=["DELETE"])
@login_required
def delete_image(image_id: int):
    athlete_id = session["athlete_id"]
    with get_session() as db:
        img = _scoped_image_or_404(db, image_id, athlete_id)
        if img is None:
            return jsonify({"error": "Not found"}), 404

        story_id = img.story_id
        path = Path(settings.STORY_IMAGE_DIR) / str(story_id) / img.filename
        try:
            if path.is_file():
                path.unlink()
        except OSError as e:
            logger.warning("Failed to remove image file %s: %s", path, e)

        db.delete(img)
        db.commit()

        story = db.get(AthleteStory, story_id)
        if story:
            refresh_cover_image_path(story, db)
        return jsonify({"ok": True})
