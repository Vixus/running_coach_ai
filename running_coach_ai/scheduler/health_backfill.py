"""Daily health-data backfill scheduler job.

Runs at 14:00 system time. For each athlete with Garmin credentials, checks
whether today's health snapshot has the key fields populated and merges in a
fresh fetch if anything is missing. Catches morning-window misses without
clobbering otherwise-good rows.
"""

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_

from running_coach_ai.coach.adapter import run_morning_checkin
from running_coach_ai.database.models import Athlete, HealthSnapshot, Notification
from running_coach_ai.database.session import get_session
from running_coach_ai.garmin.client import get_garmin_client, get_health_snapshot, is_garmin_auth_error
from running_coach_ai.garmin.parser import parse_health_snapshot

logger = logging.getLogger(__name__)


def _run_health_backfill() -> None:
    """Afternoon health data backfill — ensures today's health metrics are captured.

    Runs daily at 14:00 system time. For each athlete with Garmin credentials,
    checks whether today's health snapshot has the key fields populated. If any
    are missing, fetches from Garmin and merges into the existing record. This
    guarantees a complete health history for coaching context even when the
    morning check-in window was missed.
    """
    logger.info("Health data backfill starting")

    with get_session() as db_session:
        athletes = (
            db_session.query(Athlete)
            .filter(
                Athlete.allowed.is_(True),
                Athlete.onboarding_complete.is_(True),
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

            key_fields = ("sleep_score", "sleep_duration_seconds", "hrv_score", "body_battery_start")
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

                # If a morning_checkin notification already fired today with
                # stale or null morning_snapshot_date, re-fire so the rationale
                # text matches the now-fresh snapshot. Upsert in coach/notify.py
                # updates the same row in place.
                day_start_local = datetime.combine(today, time.min, tzinfo=tz)
                day_start_utc = day_start_local.astimezone(timezone.utc).replace(tzinfo=None)
                day_end_utc = (day_start_local + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)

                stale_morning = (
                    db_session.query(Notification)
                    .filter(
                        Notification.athlete_id == athlete.id,
                        Notification.kind == "morning_checkin",
                        Notification.created_at >= day_start_utc,
                        Notification.created_at < day_end_utc,
                        or_(
                            Notification.morning_snapshot_date.is_(None),
                            Notification.morning_snapshot_date != today,
                        ),
                    )
                    .first()
                )
                if stale_morning is not None:
                    logger.info(
                        "Health backfill: re-firing morning check-in for athlete %d (stale notif id=%d)",
                        athlete.id, stale_morning.id,
                    )
                    try:
                        run_morning_checkin(athlete, db_session, force=True)
                    except Exception as refire_err:
                        logger.error(
                            "Re-fire of morning_checkin failed for athlete %d: %s",
                            athlete.id, refire_err,
                        )

            except Exception as e:
                if is_garmin_auth_error(e):
                    logger.warning("Health backfill: Garmin auth error for athlete %d — skipping", athlete.id)
                else:
                    logger.error("Health backfill failed for athlete %d: %s", athlete.id, e)

    logger.info("Health data backfill complete")
