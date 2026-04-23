"""Backfill completed activities from Garmin Connect for a given athlete.

Fetches all activities from the past N days (default 30) that are not yet
in the database and ingests them as CompletedWorkout records. No Slack
feedback is sent — this is a silent data import for the web dashboard.

Usage:
    python scripts/backfill_activities.py --athlete-id 1 [--days 30] [--dry-run]
"""

import argparse
import logging
import sys
from datetime import date, timedelta

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def backfill(athlete_id: int, days: int, dry_run: bool) -> None:
    from running_coach_ai.database.models import Athlete, CompletedWorkout, PlannedWorkout
    from running_coach_ai.database.session import get_session
    from running_coach_ai.garmin.client import get_garmin_client
    from running_coach_ai.garmin.parser import parse_activity_summary

    start_date = (date.today() - timedelta(days=days)).isoformat()
    end_date = date.today().isoformat()

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            logger.error("Athlete %d not found", athlete_id)
            sys.exit(1)
        if not athlete.garmin_email or not athlete.garmin_password_encrypted:
            logger.error("Athlete %d has no Garmin credentials", athlete_id)
            sys.exit(1)

        logger.info("Fetching Garmin activities for athlete %d from %s to %s", athlete_id, start_date, end_date)
        garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)

        try:
            activities = garmin.get_activities_by_date(start_date, end_date)
        except Exception as e:
            logger.error("Failed to fetch activities: %s", e)
            sys.exit(1)

        logger.info("Found %d total activities in date range", len(activities))

        # IDs already fully ingested
        existing_ids = {
            row.garmin_activity_id
            for row in db.query(CompletedWorkout.garmin_activity_id)
            .filter(
                CompletedWorkout.athlete_id == athlete_id,
                CompletedWorkout.duration_seconds.isnot(None),
            )
            .all()
        }

        MIN_DURATION_SECONDS = 600
        ingested = 0
        skipped = 0

        for activity in activities:
            activity_id = str(activity.get("activityId", ""))
            duration = activity.get("duration") or 0

            if not activity_id:
                continue
            if duration < MIN_DURATION_SECONDS:
                logger.debug("Skipping short activity %s (%ds)", activity_id, duration)
                skipped += 1
                continue
            if activity_id in existing_ids:
                logger.debug("Activity %s already ingested — skipping", activity_id)
                skipped += 1
                continue

            if dry_run:
                logger.info("[DRY RUN] Would ingest activity %s", activity_id)
                ingested += 1
                continue

            try:
                # Remove any incomplete stub before re-ingesting
                stub = db.query(CompletedWorkout).filter(
                    CompletedWorkout.garmin_activity_id == activity_id,
                    CompletedWorkout.athlete_id == athlete_id,
                    CompletedWorkout.duration_seconds.is_(None),
                ).first()
                if stub:
                    db.delete(stub)
                    db.flush()

                activity_data = garmin.get_activity(activity_id)
                completed = parse_activity_summary(activity_data, athlete_id, db)
                completed.feedback_given = True  # suppress Slack notifications

                # Mark any linked planned workout as completed
                if completed.planned_workout_id:
                    planned = db.get(PlannedWorkout, completed.planned_workout_id)
                    if planned and planned.status not in ("completed", "cancelled"):
                        planned.status = "completed"
                        logger.info(
                            "Marked PlannedWorkout %d as completed (%s on %s)",
                            planned.id, planned.workout_type, planned.scheduled_date,
                        )

                db.commit()
                logger.info(
                    "Ingested activity %s (%s, %s km, %s)",
                    activity_id,
                    completed.activity_type,
                    completed.distance_km,
                    completed.date,
                )
                ingested += 1

            except Exception as e:
                logger.error("Failed to ingest activity %s: %s", activity_id, e)
                db.rollback()

    logger.info("Done. Ingested: %d, Skipped: %d", ingested, skipped)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--athlete-id", type=int, required=True)
    parser.add_argument("--days", type=int, default=30, help="How many days back to backfill (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="List activities without writing to DB")
    args = parser.parse_args()

    backfill(args.athlete_id, args.days, args.dry_run)
