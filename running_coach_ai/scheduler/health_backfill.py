"""Daily health-data backfill scheduler job.

Runs at 14:00 system time. For each athlete with Garmin credentials, checks
whether today's health snapshot has the key fields populated and merges in a
fresh fetch if anything is missing. Catches morning-window misses without
clobbering otherwise-good rows.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def _run_health_backfill() -> None:
    """Afternoon health data backfill — ensures today's health metrics are captured.

    Runs daily at 14:00 system time. For each athlete with Garmin credentials,
    checks whether today's health snapshot has the key fields populated. If any
    are missing, fetches from Garmin and merges into the existing record. This
    guarantees a complete health history for coaching context even when the
    morning check-in window was missed.
    """
    from running_coach_ai.database.models import Athlete, HealthSnapshot
    from running_coach_ai.database.session import get_session
    from running_coach_ai.garmin.client import get_garmin_client, get_health_snapshot
    from running_coach_ai.garmin.parser import parse_health_snapshot

    logger.info("Health data backfill starting")

    with get_session() as db_session:
        athletes = (
            db_session.query(Athlete)
            .filter(
                Athlete.allowed == True,
                Athlete.onboarding_complete == True,
            )
            .all()
        )

        for athlete in athletes:
            if not athlete.garmin_email or not athlete.garmin_password_encrypted:
                continue

            tz = ZoneInfo(athlete.timezone or "America/New_York")
            today = datetime.now(tz).date()
            today_str = today.isoformat()

            # Check if today's snapshot already has key health fields
            existing = (
                db_session.query(HealthSnapshot)
                .filter(
                    HealthSnapshot.athlete_id == athlete.id,
                    HealthSnapshot.date == today,
                )
                .first()
            )

            key_fields = ("sleep_score", "hrv_score", "body_battery_start")
            if existing and all(
                getattr(existing, f) is not None for f in key_fields
            ):
                logger.debug(
                    "Health backfill: athlete %d already has complete data for %s",
                    athlete.id, today_str,
                )
                continue

            try:
                garmin = get_garmin_client(
                    athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted, db_session
                )
                raw = get_health_snapshot(garmin, today_str)
                parse_health_snapshot(raw, athlete.id, today, db_session)
                logger.info("Health backfill: updated snapshot for athlete %d on %s", athlete.id, today_str)
            except Exception as e:
                from running_coach_ai.garmin.client import is_garmin_auth_error
                if is_garmin_auth_error(e):
                    logger.warning("Health backfill: Garmin auth error for athlete %d — skipping", athlete.id)
                else:
                    logger.error("Health backfill failed for athlete %d: %s", athlete.id, e)

    logger.info("Health data backfill complete")
