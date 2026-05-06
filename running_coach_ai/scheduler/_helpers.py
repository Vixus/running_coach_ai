"""Shared helpers used by multiple scheduler jobs."""

import logging
from datetime import date

logger = logging.getLogger(__name__)

# Rate-limit Garmin auth-error notifications to one per athlete per calendar day.
# Shared between morning check-in and activity poll, both of which can detect a
# stale Garmin session and want to surface the same "reconnect" message.
_garmin_auth_error_notified: dict[int, date] = {}


def _notify_garmin_auth_error(athlete) -> None:
    """Notify the athlete that their Garmin credentials are invalid.

    Rate-limited to one notification per athlete per calendar day. Writes
    a "Garmin reconnect needed" row to the in-app inbox.
    """
    today = date.today()
    if _garmin_auth_error_notified.get(athlete.id) == today:
        return
    _garmin_auth_error_notified[athlete.id] = today

    body = (
        "I'm having trouble connecting to your Garmin account — your credentials may "
        "have changed. Open the app and re-enter them so I can keep your training on track."
    )

    try:
        from running_coach_ai.coach.notify import notify
        from running_coach_ai.database.session import get_session
        with get_session() as db:
            from running_coach_ai.database.models import Athlete as _A
            a = db.get(_A, athlete.id)
            if a is not None:
                notify(db, a, kind="system", title="Garmin reconnect needed",
                       body=body, action_path="/#morning")
                db.commit()
    except Exception as e:
        logger.error("Failed to write Garmin auth notification for athlete %d: %s", athlete.id, e)
