"""Public no-auth story page + robots.txt.

Per `specs/006-athlete-story/contracts/public-route.md`. Mounted at the application
root (no url_prefix). Token regex pre-validation, in-memory per-IP rate limit (60s
window, 30 req limit), `noindex` meta on every render, `Cache-Control: no-store`
on preview, `private, max-age=60` on published.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from pathlib import Path

from flask import Blueprint, Response, abort, render_template, request, session

from running_coach_ai.coach.story_templates import DEFAULT_TEMPLATE_KEY, get_template
from running_coach_ai.database.models import Athlete, AthleteStory, StoryImage
from running_coach_ai.database.session import get_session

logger = logging.getLogger(__name__)
bp = Blueprint("public_story", __name__)

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{12}$")

# Per-IP sliding window
_RATE_WINDOW_SECS = 60.0
_RATE_LIMIT = 30
_rate_lock = threading.Lock()
_ip_hits: dict[str, deque] = {}


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return (request.remote_addr or "").strip()


def _check_rate_limit(ip: str) -> bool:
    """Return True if allowed, False if over limit."""
    now = time.monotonic()
    with _rate_lock:
        dq = _ip_hits.setdefault(ip, deque())
        # Prune entries older than the window
        while dq and now - dq[0] > _RATE_WINDOW_SECS:
            dq.popleft()
        if len(dq) >= _RATE_LIMIT:
            return False
        dq.append(now)
        return True


def _build_training_stats(story: AthleteStory) -> dict | None:
    """Return a small dict for the template's stats sidebar.

    For v1 we keep it simple — derive from the editorial title/context. The full
    aggregation lives in `coach.story._gather_training_stats` and is used at
    generation time, not at render time, to keep the page fast.
    """
    # The spec's public-route contract (`SC-S002 < 1.5s`) prefers fast renders, so
    # we expose the same stats Claude saw via the editorial only — no extra DB
    # aggregation here.
    return None


def _athlete_first_last(athlete: Athlete | None) -> tuple[str, str]:
    if athlete is None or not athlete.name:
        return "Athlete", ""
    parts = athlete.name.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _image_payload(images: list[StoryImage]) -> list[dict]:
    return [
        {"url": f"/api/stories/images/{i.id}/raw",
         "caption": i.caption or ""}
        for i in sorted(images, key=lambda x: (x.sort_order, x.id))
    ]


@bp.route("/story/<share_token>", methods=["GET"])
def public_story_page(share_token: str):
    # Token regex pre-validation — never hits the DB on garbage paths
    if not _TOKEN_RE.match(share_token):
        abort(404)

    # Rate limit
    ip = _client_ip()
    if not _check_rate_limit(ip):
        return Response("Too many requests", status=429, mimetype="text/plain")

    is_preview = request.args.get("preview") == "1"

    with get_session() as db:
        story = (
            db.query(AthleteStory)
            .filter(AthleteStory.share_token == share_token)
            .first()
        )
        if story is None or story.deleted_at is not None:
            abort(404)

        # Unpublished story is only visible to the owning athlete or an admin in preview mode
        if story.published_at is None:
            if not is_preview:
                abort(404)
            authed_athlete_id = session.get("athlete_id")
            authed_admin = session.get("is_admin", False)
            if authed_athlete_id != story.athlete_id and not authed_admin:
                abort(404)

        athlete = db.get(Athlete, story.athlete_id)
        first, last = _athlete_first_last(athlete)
        images = _image_payload(story.images)

        # Resolve template (with fallback)
        try:
            tpl = get_template(story.template_key)
        except RuntimeError:
            tpl = None

        if tpl is None:
            # Registry empty — render a barebones page
            template_key = DEFAULT_TEMPLATE_KEY
            cover_html = "<div class='tpl-vogue-cover'><h1>{}</h1></div>".format(athlete.name or "")
            inside_html = "<article>{}</article>".format(story.editorial_body)
            template_css_url = ""
        else:
            template_key = tpl.key
            cover_html = tpl.cover_html_path().read_text(encoding="utf-8")
            inside_html = tpl.inside_html_path().read_text(encoding="utf-8")
            template_css_url = f"/static/story_templates/{tpl.key}/template.css"

        # Cover image URL (first image or None)
        cover_image_url = None
        if images:
            cover_image_url = images[0]["url"]

        # Milestone label for the cover kicker
        milestone_label = "Race Day" if story.milestone_type == "race_complete" \
            else story.milestone_type.replace("_", " ").title()

        # First-paragraph fragment for "deck" / kicker text
        first_para = (story.editorial_body or "").split("\n\n", 1)[0]
        deck_text = first_para[:200] + ("…" if len(first_para) > 200 else "")

        # Pull a quote — find a sentence in the middle of the editorial
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", story.editorial_body or "") if s.strip()]
        pull_quote = sentences[len(sentences) // 2] if sentences else ""

        ctx = {
            "story": story,
            "athlete": athlete,
            "athlete_first_name": first,
            "athlete_last_name": last,
            "title": story.title,
            "editorial_body": story.editorial_body,
            "template_key": template_key,
            "template_css_url": template_css_url,
            "cover_html": cover_html,
            "inside_html": inside_html,
            "cover_image_url": cover_image_url,
            "milestone_label": milestone_label,
            "deck_text": deck_text,
            "pull_quote": pull_quote,
            "images": images,
            "training_stats": _build_training_stats(story),
            "is_preview": is_preview,
            "share_url": f"{request.host_url.rstrip('/')}/story/{share_token}",
        }

        rendered = render_template("story_page.html", **ctx)

    headers = {}
    if is_preview:
        headers["Cache-Control"] = "no-store"
    elif story.published_at:
        headers["Cache-Control"] = "private, max-age=60"

    return Response(rendered, status=200, mimetype="text/html", headers=headers)


@bp.route("/robots.txt", methods=["GET"])
def robots_txt():
    body = "User-agent: *\nDisallow: /story/\n"
    return Response(body, status=200, mimetype="text/plain",
                    headers={"Cache-Control": "public, max-age=86400"})
