"""Admin endpoints — events, athlete management, Garmin ops, invites."""

import logging

from flask import Blueprint, jsonify, request, session

from running_coach_ai.coach import admin_ops
from running_coach_ai.database.models import Athlete, InviteToken, WebEvent
from running_coach_ai.database.session import get_session
from running_coach_ai.garmin import admin as garmin_admin
from running_coach_ai.web.auth import admin_required, issue_invite_token
from running_coach_ai.web.events import cleanup_old_events

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__)

_MAX_LIMIT = 200


@bp.route("/api/admin/events")
@admin_required
def admin_events():
    category = request.args.get("category", "all")
    severity = request.args.get("severity", "all")
    try:
        limit = min(int(request.args.get("limit", 50)), _MAX_LIMIT)
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        limit = 50
        offset = 0

    with get_session() as db:
        cleanup_old_events(db)

        q = db.query(WebEvent)
        if category and category != "all":
            q = q.filter(WebEvent.category == category)
        if severity and severity != "all":
            q = q.filter(WebEvent.severity == severity)

        q = q.order_by(WebEvent.timestamp.desc())
        total = q.count()
        events = q.offset(offset).limit(limit).all()

        athlete_names: dict[int, str] = {}
        athlete_ids = {e.athlete_id for e in events if e.athlete_id is not None}
        for aid in athlete_ids:
            a = db.get(Athlete, aid)
            if a:
                athlete_names[aid] = a.name or str(aid)

        return jsonify({
            "events": [
                {
                    "id": e.id,
                    "timestamp": e.timestamp.isoformat() + "Z",
                    "severity": e.severity,
                    "category": e.category,
                    "message": e.message,
                    "athlete_name": athlete_names.get(e.athlete_id),
                    "details": e.details_json,
                }
                for e in events
            ],
            "total": total,
        })


# ── Athlete management ──────────────────────────────────────────────────────

@bp.route("/api/admin/athletes")
@admin_required
def admin_list_athletes():
    with get_session() as db:
        return jsonify({"athletes": admin_ops.list_athletes(db)})


@bp.route("/api/admin/athletes/<int:athlete_id>/allow", methods=["POST"])
@admin_required
def admin_allow_athlete(athlete_id: int):
    with get_session() as db:
        result = admin_ops.set_allowed(athlete_id, True, db)
    if result is None:
        return jsonify({"error": "Athlete not found"}), 404
    return jsonify(result)


@bp.route("/api/admin/athletes/<int:athlete_id>/disable", methods=["POST"])
@admin_required
def admin_disable_athlete(athlete_id: int):
    with get_session() as db:
        result = admin_ops.set_allowed(athlete_id, False, db)
    if result is None:
        return jsonify({"error": "Athlete not found"}), 404
    return jsonify(result)


@bp.route("/api/admin/athletes/<int:athlete_id>/reset-onboarding", methods=["POST"])
@admin_required
def admin_reset_onboarding(athlete_id: int):
    with get_session() as db:
        result = admin_ops.reset_onboarding(athlete_id, db)
    if result is None:
        return jsonify({"error": "Athlete not found"}), 404
    return jsonify(result)


@bp.route("/api/admin/athletes/<int:athlete_id>/refresh-garmin-data", methods=["POST"])
@admin_required
def admin_refresh_garmin_data(athlete_id: int):
    body = request.get_json(silent=True) or {}
    try:
        days_back = min(int(body.get("days_back", 7)), 30)
    except (TypeError, ValueError):
        days_back = 7
    result = admin_ops.refresh_garmin_data(athlete_id, days_back=days_back)
    return jsonify(result), (200 if result.get("ok") else 400)


@bp.route("/api/admin/athletes/<int:athlete_id>/morning-checkin", methods=["POST"])
@admin_required
def admin_morning_checkin(athlete_id: int):
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))
    result = admin_ops.trigger_morning_checkin(athlete_id, force=force)
    return jsonify(result), (200 if result["ok"] else 400)


# ── Garmin admin ────────────────────────────────────────────────────────────

@bp.route("/api/admin/athletes/<int:athlete_id>/resync-garmin", methods=["POST"])
@admin_required
def admin_resync_garmin(athlete_id: int):
    with get_session() as db:
        a = db.get(Athlete, athlete_id)
        if not a:
            return jsonify({"error": "Athlete not found"}), 404
        result = garmin_admin.resync_garmin_for_athlete(a, db)
    return jsonify(result), (200 if result["ok"] else 400)


@bp.route("/api/admin/athletes/<int:athlete_id>/clean-garmin", methods=["POST"])
@admin_required
def admin_clean_garmin(athlete_id: int):
    with get_session() as db:
        a = db.get(Athlete, athlete_id)
        if not a:
            return jsonify({"error": "Athlete not found"}), 404
        result = garmin_admin.clean_garmin_for_athlete(a, db)
    return jsonify(result), (200 if result["ok"] else 400)


@bp.route("/api/admin/athletes/<int:athlete_id>/verify-garmin")
@admin_required
def admin_verify_garmin(athlete_id: int):
    with get_session() as db:
        a = db.get(Athlete, athlete_id)
        if not a:
            return jsonify({"error": "Athlete not found"}), 404
        result = garmin_admin.verify_garmin_for_athlete(a, db)
    return jsonify(result), (200 if result["ok"] else 400)


# ── Invites ─────────────────────────────────────────────────────────────────

@bp.route("/api/admin/invites", methods=["GET"])
@admin_required
def admin_list_invites():
    with get_session() as db:
        rows = (
            db.query(InviteToken)
            .order_by(InviteToken.created_at.desc())
            .limit(100)
            .all()
        )
        return jsonify({"invites": [
            {
                "id": i.id,
                "token": i.token,
                "email_hint": i.email_hint,
                "created_at": i.created_at.isoformat() + "Z" if i.created_at else None,
                "expires_at": i.expires_at.isoformat() + "Z" if i.expires_at else None,
                "used_by_athlete_id": i.used_by_athlete_id,
                "used_at": i.used_at.isoformat() + "Z" if i.used_at else None,
            }
            for i in rows
        ]})


@bp.route("/api/admin/invites", methods=["POST"])
@admin_required
def admin_create_invite():
    body = request.get_json(silent=True) or {}
    email_hint = (body.get("email_hint") or "").strip() or None
    try:
        ttl_days = int(body.get("ttl_days") or 30)
    except (TypeError, ValueError):
        ttl_days = 30
    invite = issue_invite_token(
        created_by_athlete_id=session.get("athlete_id"),
        email_hint=email_hint,
        ttl_days=ttl_days,
    )
    base = request.host_url.rstrip("/")
    return jsonify({
        "id": invite.id,
        "token": invite.token,
        "email_hint": invite.email_hint,
        "expires_at": invite.expires_at.isoformat() + "Z" if invite.expires_at else None,
        "signup_url": f"{base}/login?invite={invite.token}",
    })


# ---------------------------------------------------------------------------
# Admin story tooling — start interview, generate, render, hard-delete, list templates
# ---------------------------------------------------------------------------

@bp.route("/api/admin/athletes/<int:athlete_id>/start-interview", methods=["POST"])
@admin_required
def admin_start_interview(athlete_id: int):
    from running_coach_ai.coach.story import (
        INTERVIEW_TRIGGERS, fire_trigger_if_eligible,
    )
    from running_coach_ai.database.models import StoryQuestion

    trigger = (request.args.get("trigger") or "").strip()
    if trigger not in INTERVIEW_TRIGGERS:
        return jsonify({
            "error": f"Unknown trigger kind: {trigger}",
            "valid": list(INTERVIEW_TRIGGERS),
        }), 400

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if athlete is None:
            return jsonify({"error": "Athlete not found"}), 404

        # Block if there's already an active session
        from running_coach_ai.database.models import StoryInterviewSession
        active = (
            db.query(StoryInterviewSession)
            .filter(
                StoryInterviewSession.athlete_id == athlete_id,
                StoryInterviewSession.completed_at.is_(None),
            )
            .first()
        )
        if active is not None:
            return jsonify({
                "error": f"Athlete has an active session (id={active.id}); close it before starting another",
            }), 400

        body = request.get_json(silent=True) or {}
        context = body.get("trigger_context_json") or {}
        sess = fire_trigger_if_eligible(athlete, trigger, context, db, bypass_intro=True)
        if sess is None:
            return jsonify({"error": "Could not open session (athlete may not be opted in?)"}), 400

        first_q = (
            db.query(StoryQuestion)
            .filter(StoryQuestion.session_id == sess.id, StoryQuestion.question_index == 1)
            .first()
        )
        return jsonify({
            "session_id": sess.id,
            "first_question": {
                "question_id": first_q.id,
                "question": first_q.question,
                "options": first_q.options_json,
            } if first_q else None,
        })


@bp.route("/api/admin/athletes/<int:athlete_id>/generate-story", methods=["POST"])
@admin_required
def admin_generate_story(athlete_id: int):
    from running_coach_ai.coach.story import generate_story, StoryGenerationError

    milestone = (request.args.get("milestone") or "").strip()
    if milestone != "race_complete":
        return jsonify({
            "error": f"Unknown milestone type: {milestone}",
            "valid": ["race_complete"],
        }), 400

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if athlete is None:
            return jsonify({"error": "Athlete not found"}), 404
        try:
            story = generate_story(athlete, milestone, db)
        except StoryGenerationError as e:
            return jsonify({"error": str(e)}), 504
        return jsonify({
            "story_id": story.id,
            "preview_url": f"/story/{story.share_token}?preview=1",
        })


@bp.route("/api/admin/stories/<int:story_id>/render", methods=["POST"])
@admin_required
def admin_render_story(story_id: int):
    from running_coach_ai.coach.story_templates import is_registered, list_templates
    from running_coach_ai.database.models import AthleteStory

    template_key = (request.args.get("template") or "").strip()
    if not is_registered(template_key):
        return jsonify({
            "error": f"Unknown template key: {template_key}",
            "available": [t.key for t in list_templates()],
        }), 400

    with get_session() as db:
        story = db.get(AthleteStory, story_id)
        if story is None or story.deleted_at is not None:
            return jsonify({"error": "Story not found"}), 404
        story.template_key = template_key
        # Admin override: don't lock against future auto-selection per FR-S038
        story.template_locked_by_athlete = False
        db.commit()
        return jsonify({
            "ok": True,
            "story_id": story.id,
            "template_key": story.template_key,
            "preview_url": f"/story/{story.share_token}?preview=1",
        })


@bp.route("/api/admin/stories/<int:story_id>", methods=["DELETE"])
@admin_required
def admin_hard_delete_story(story_id: int):
    import os as _os
    from pathlib import Path
    from running_coach_ai.config import settings
    from running_coach_ai.database.models import (
        AthleteStory, StoryImage, StoryInterviewSession, StoryQuestion,
    )

    if request.args.get("hard") != "1":
        return jsonify({"error": "Hard-delete requires ?hard=1 query parameter"}), 400

    with get_session() as db:
        story = db.get(AthleteStory, story_id)
        if story is None:
            return jsonify({"error": "Story not found"}), 404

        image_files_removed = 0
        image_rows = list(story.images)
        for img in image_rows:
            path = Path(settings.STORY_IMAGE_DIR) / str(story.id) / img.filename
            try:
                if path.is_file():
                    path.unlink()
                    image_files_removed += 1
            except OSError as e:
                logger.warning("Failed to remove image file %s: %s", path, e)

        # Drop the story directory if it's empty
        story_dir = Path(settings.STORY_IMAGE_DIR) / str(story.id)
        try:
            if story_dir.is_dir() and not any(story_dir.iterdir()):
                story_dir.rmdir()
        except OSError:
            pass

        # Cascade
        question_count = (
            db.query(StoryQuestion).filter(StoryQuestion.story_id == story_id).count()
        )
        session_count = (
            db.query(StoryInterviewSession)
            .filter(StoryInterviewSession.story_id == story_id).count()
        )
        # Detach sessions (set story_id back to NULL so FK doesn't block)
        db.query(StoryInterviewSession).filter(
            StoryInterviewSession.story_id == story_id
        ).update({"story_id": None})
        db.query(StoryQuestion).filter(StoryQuestion.story_id == story_id).update(
            {"story_id": None}
        )

        db.query(StoryImage).filter(StoryImage.story_id == story_id).delete()
        db.delete(story)
        db.commit()
        return jsonify({
            "ok": True,
            "story_id": story_id,
            "deleted": {
                "story_rows": 1,
                "image_rows": len(image_rows),
                "image_files": image_files_removed,
                "session_rows": session_count,
                "question_rows": question_count,
            },
        })


@bp.route("/api/admin/stories/templates", methods=["GET"])
@admin_required
def admin_list_story_templates():
    from running_coach_ai.coach.story_templates import list_templates
    return jsonify({
        "templates": [tpl.to_summary_dict() for tpl in list_templates()],
    })
