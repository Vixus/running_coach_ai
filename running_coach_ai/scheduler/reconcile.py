"""Daily Garmin sync reconciliation scheduler job.

Self-healing fallback for the event-driven Garmin sync path. Scans every
athlete's future planned/modified workouts at 08:30 daily; any row that's
missing a Garmin workout or schedule ID is uploaded via week-grouped
sync_week_to_garmin calls so a single failed event doesn't strand a workout.
"""

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def _run_garmin_reconciliation() -> None:
    """Daily reconciliation job — finds future workouts missing Garmin IDs and re-syncs them.

    Runs at 08:30 (after the morning check-in). Scans all athletes for planned/modified
    future workouts with garmin_workout_id IS NULL or garmin_schedule_id IS NULL and
    uploads/schedules them via sync_week_to_garmin. This is the self-healing guarantee —
    any gap left by a failed event-driven sync is repaired within 24 hours.
    """
    from running_coach_ai.database.models import Athlete, PlannedWorkout
    from running_coach_ai.database.session import get_session
    from running_coach_ai.garmin.workout_builder import sync_week_to_garmin

    logger.info("Garmin reconciliation starting")
    today = date.today()

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
            try:
                # Find all future workouts that need syncing
                unsynced = (
                    db_session.query(PlannedWorkout)
                    .filter(
                        PlannedWorkout.athlete_id == athlete.id,
                        PlannedWorkout.scheduled_date >= today,
                        PlannedWorkout.status.in_(["planned", "modified"]),
                        PlannedWorkout.workout_type != "rest",
                        (
                            PlannedWorkout.garmin_workout_id.is_(None) |
                            PlannedWorkout.garmin_schedule_id.is_(None)
                        ),
                    )
                    .all()
                )

                if not unsynced:
                    logger.debug("Garmin reconciliation: athlete %d is fully in sync", athlete.id)
                    continue

                logger.info(
                    "Garmin reconciliation: athlete %d has %d workout(s) missing Garmin IDs",
                    athlete.id, len(unsynced),
                )

                # Group by week and sync each affected week
                week_starts: set[date] = {
                    w.scheduled_date - timedelta(days=w.scheduled_date.weekday())
                    for w in unsynced
                }
                total_uploaded = 0
                total_failed = 0
                for week_start in sorted(week_starts):
                    try:
                        up, fail, _ = sync_week_to_garmin(
                            athlete.id,
                            athlete.garmin_email,
                            athlete.garmin_password_encrypted,
                            week_start,
                            db_session,
                        )
                        total_uploaded += up
                        total_failed += fail
                    except Exception as e:
                        logger.error(
                            "Reconciliation sync failed for athlete %d week %s: %s",
                            athlete.id, week_start, e,
                        )

                logger.info(
                    "Garmin reconciliation for athlete %d: uploaded=%d, failed=%d",
                    athlete.id, total_uploaded, total_failed,
                )

            except Exception as e:
                logger.error("Garmin reconciliation failed for athlete %d: %s", athlete.id, e)

    logger.info("Garmin reconciliation complete")
