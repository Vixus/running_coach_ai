"""Athlete management admin operations — surface-agnostic.

Web admin endpoints in `web/api/admin.py` call these. Slack admin
commands in `slack/admin.py` keep their own slack_user_id-based
implementations until Phase 6.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from running_coach_ai.database.models import Athlete, ConversationMessage

logger = logging.getLogger(__name__)


def list_athletes(db: Session) -> list[dict]:
    """Return all athletes with status fields useful for the admin UI."""
    rows = db.query(Athlete).order_by(Athlete.created_at).all()
    return [
        {
            "id": a.id,
            "email": a.email,
            "web_username": a.web_username,
            "slack_user_id": a.slack_user_id,
            "name": a.name,
            "allowed": bool(a.allowed),
            "is_admin": bool(a.is_admin),
            "onboarding_complete": bool(a.onboarding_complete),
            "coach_key": a.coach_key or "classic",
            "timezone": a.timezone,
            "has_garmin": bool(a.garmin_email and a.garmin_password_encrypted),
            "last_morning_checkin_date": (
                a.last_morning_checkin_date.isoformat()
                if a.last_morning_checkin_date else None
            ),
            "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
        }
        for a in rows
    ]


def set_allowed(athlete_id: int, allowed: bool, db: Session) -> Optional[dict]:
    """Allow or revoke an athlete. Data is preserved either way."""
    a = db.get(Athlete, athlete_id)
    if not a:
        return None
    a.allowed = bool(allowed)
    db.commit()
    logger.info("Admin set allowed=%s for athlete %d", allowed, athlete_id)
    return {"id": a.id, "allowed": a.allowed}


def reset_onboarding(athlete_id: int, db: Session) -> Optional[dict]:
    """Wipe conversation history and onboarding state so the athlete can re-onboard.

    Does NOT delete Goal, plans, or completed workouts — only the chat
    state and the `onboarding_complete` flag.
    """
    a = db.get(Athlete, athlete_id)
    if not a:
        return None
    deleted = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.athlete_id == a.id)
        .delete()
    )
    a.pending_onboarding_data = None
    a.pending_onboarding_data_created_at = None
    a.onboarding_complete = False
    a.onboarding_step = 0
    db.commit()
    logger.info("Admin reset onboarding for athlete %d (deleted %d messages)", athlete_id, deleted)
    return {"id": a.id, "deleted_messages": deleted}


def trigger_morning_checkin(athlete_id: int, *, force: bool = False, slack_client=None) -> dict:
    """Manually fire the morning check-in for an athlete.

    `force=True` clears `last_morning_checkin_date` so the dedup gate doesn't
    skip it. `slack_client` may be None — in-app notification still fires.
    """
    from running_coach_ai.database.session import get_session
    from running_coach_ai.scheduler.jobs import _run_morning_checkin_for_athlete

    with get_session() as db:
        a = db.get(Athlete, athlete_id)
        if not a:
            return {"ok": False, "error": "Athlete not found"}
        if not a.allowed or not a.onboarding_complete:
            return {"ok": False, "error": "Athlete is not active or has not completed onboarding"}
        if force and a.last_morning_checkin_date is not None:
            a.last_morning_checkin_date = None
            db.commit()

    try:
        _run_morning_checkin_for_athlete(athlete_id, slack_client)
    except Exception as e:
        logger.error("Admin morning-checkin failed for athlete %d: %s", athlete_id, e)
        return {"ok": False, "error": str(e)}
    return {"ok": True, "error": None}
