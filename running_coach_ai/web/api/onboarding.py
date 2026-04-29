"""Web endpoints for the onboarding flow.

The chat-driven Q&A runs through `web/api/chat.py`; this module handles the
two terminal steps: submitting Garmin credentials (or skipping) once the
profile JSON has been captured.
"""

import logging

from flask import Blueprint, jsonify, request, session

from running_coach_ai.coach.onboarding import complete_onboarding, is_pending_data_expired
from running_coach_ai.database.models import Athlete
from running_coach_ai.database.session import get_session
from running_coach_ai.web.auth import login_required

logger = logging.getLogger(__name__)

bp = Blueprint("onboarding", __name__)


def _validate_garmin_creds(email: str, password: str) -> tuple[bool, str | None]:
    """Try to authenticate against Garmin once. Returns (ok, error_msg)."""
    try:
        import garminconnect
    except Exception as e:  # pragma: no cover — import-time failure is non-fatal in tests
        logger.warning("garminconnect import failed: %s", e)
        return True, None  # let onboarding proceed; sync will retry later

    try:
        client = garminconnect.Garmin(email, password)
        client.login()
        return True, None
    except garminconnect.GarminConnectAuthenticationError:
        return False, "Garmin rejected those credentials. Double-check email and password."
    except Exception as e:
        logger.warning("Garmin pre-auth check failed: %s", e)
        # Don't block onboarding on transient Garmin errors — sync will retry.
        return True, None


@bp.route("/api/onboarding/garmin-creds", methods=["POST"])
@login_required
def submit_garmin_creds():
    athlete_id = session["athlete_id"]
    data = request.get_json(silent=True) or {}
    garmin_email = (data.get("garmin_email") or "").strip()
    garmin_password = data.get("garmin_password") or ""

    if not garmin_email or not garmin_password:
        return jsonify({"error": "Garmin email and password are required"}), 400

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            return jsonify({"error": "Athlete not found"}), 404
        if athlete.onboarding_complete:
            return jsonify({"error": "Onboarding already complete"}), 400
        if not athlete.pending_onboarding_data:
            return jsonify({"error": "No profile data captured yet. Finish the chat first."}), 400
        if is_pending_data_expired(athlete):
            return jsonify({"error": "Your onboarding session expired. Send a chat message to start over."}), 400

        ok, err = _validate_garmin_creds(garmin_email, garmin_password)
        if not ok:
            return jsonify({"error": err}), 400

        result = complete_onboarding(athlete, garmin_email, garmin_password, db)

    return jsonify({
        "success": result["success"],
        "messages": result["messages"],
        "error": result["error"],
    }), (200 if result["success"] else 500)


@bp.route("/api/onboarding/skip-garmin", methods=["POST"])
@login_required
def skip_garmin():
    athlete_id = session["athlete_id"]
    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            return jsonify({"error": "Athlete not found"}), 404
        if athlete.onboarding_complete:
            return jsonify({"error": "Onboarding already complete"}), 400
        if not athlete.pending_onboarding_data:
            return jsonify({"error": "No profile data captured yet."}), 400
        if is_pending_data_expired(athlete):
            return jsonify({"error": "Your onboarding session expired. Send a chat message to start over."}), 400

        result = complete_onboarding(athlete, None, None, db)

    return jsonify({
        "success": result["success"],
        "messages": result["messages"],
        "error": result["error"],
    }), (200 if result["success"] else 500)
