"""GET /api/chat/history and POST /api/chat/message endpoints."""

import logging
import time
from datetime import datetime, timezone

import anthropic
from flask import Blueprint, jsonify, request, session

from running_coach_ai.coach.personas import is_valid_coach_key
from running_coach_ai.database.models import Athlete, ConversationMessage
from running_coach_ai.database.session import get_session
from running_coach_ai.slack.conversation import process_message
from running_coach_ai.web.auth import login_required
from running_coach_ai.web.events import web_event

logger = logging.getLogger(__name__)

bp = Blueprint("chat", __name__)

_VALID_COACH_KEYS = {"classic", "maya", "jordan"}


@bp.route("/api/chat/history")
@login_required
def chat_history():
    athlete_id = session["athlete_id"]

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            return jsonify({"error": "Athlete not found"}), 404

        msgs = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.athlete_id == athlete_id)
            .order_by(ConversationMessage.created_at.asc())
            .all()
        )

        return jsonify({
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.created_at.isoformat() + "Z",
                    "source": m.source or "slack",
                }
                for m in msgs
            ]
        })


@bp.route("/api/chat/message", methods=["POST"])
@login_required
def chat_message():
    athlete_id = session["athlete_id"]
    data = request.get_json(silent=True) or {}

    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    if len(message) > 2000:
        return jsonify({"error": "message must be 2000 characters or fewer"}), 422

    # Optional ephemeral coach key (does not persist to athlete.coach_key)
    raw_coach_key = data.get("coach_key")
    effective_coach_key = None
    if raw_coach_key and is_valid_coach_key(raw_coach_key):
        effective_coach_key = raw_coach_key

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            return jsonify({"error": "Athlete not found"}), 404

        start = time.monotonic()
        try:
            response_text = process_message(
                athlete,
                message,
                db,
                source="web",
                coach_key=effective_coach_key,
            )
        except anthropic.APIError as exc:
            logger.warning("Claude API error for athlete %s: %s", athlete_id, exc)
            web_event(
                severity="error", category="claude",
                message=f"Claude API error: {exc}",
                athlete_id=athlete_id,
            )
            return jsonify({"error": "Coach is unavailable — try again in a moment."}), 503
        except Exception as exc:
            logger.error("Unexpected error in chat for athlete %s: %s", athlete_id, exc)
            return jsonify({"error": "Coach is unavailable — try again in a moment."}), 503

        latency_ms = round((time.monotonic() - start) * 1000)
        web_event(
            severity="info", category="claude",
            message=f"Chat response for athlete {athlete_id} in {latency_ms}ms",
            athlete_id=athlete_id,
            details={"latency_ms": latency_ms},
        )

        return jsonify({
            "response": response_text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
