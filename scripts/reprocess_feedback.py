"""Force-trigger post-run feedback for already-ingested CompletedWorkout IDs.

Usage:
    python scripts/reprocess_feedback.py <cw_id> [<cw_id> ...]

This re-runs biomechanics analysis and sends the Slack feedback DM for runs
that are already in the database but have feedback_given=False (e.g. after
fixing the smoothing algorithm and resetting feedback_given).
"""

import sys
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

from sqlalchemy import func
from running_coach_ai.database.session import get_session
from running_coach_ai.database.models import Athlete, CompletedWorkout, WorkoutTelemetry
from running_coach_ai.coach.biomechanics import analyse_workout, update_running_profile
from running_coach_ai.coach.feedback import generate_post_run_feedback
from running_coach_ai.garmin.client import get_garmin_client, fetch_athlete_lthr, fetch_activity_hr_zones
from running_coach_ai.config import settings
from slack_sdk import WebClient


def reprocess(cw_ids: list[int], dry_run: bool = False) -> None:
    slack_client = WebClient(token=settings.SLACK_BOT_TOKEN)

    with get_session() as db:
        for cw_id in cw_ids:
            cw = db.get(CompletedWorkout, cw_id)
            if not cw:
                logger.warning("CW %d not found", cw_id)
                continue

            athlete = db.get(Athlete, cw.athlete_id)
            if not athlete:
                logger.warning("Athlete %d not found", cw.athlete_id)
                continue

            telemetry = (
                db.query(WorkoutTelemetry)
                .filter(WorkoutTelemetry.completed_workout_id == cw_id)
                .first()
            )
            if not telemetry:
                logger.warning("CW %d has no telemetry — skipping", cw_id)
                continue

            # Fetch Garmin zones and LTHR
            garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)
            garmin_hr_zones = fetch_activity_hr_zones(garmin, cw.garmin_activity_id, athlete.id)
            if not athlete.lthr_bpm:
                lthr = fetch_athlete_lthr(garmin, athlete.id)
                if lthr:
                    athlete.lthr_bpm = lthr

            athlete_max_hr = (
                db.query(func.max(CompletedWorkout.max_hr))
                .filter(CompletedWorkout.athlete_id == cw.athlete_id, CompletedWorkout.max_hr.isnot(None))
                .scalar()
            ) or cw.max_hr or 189

            max_hr_run_count = (
                db.query(func.count(CompletedWorkout.id))
                .filter(CompletedWorkout.athlete_id == cw.athlete_id, CompletedWorkout.max_hr.isnot(None))
                .scalar()
            ) or 0

            bio = analyse_workout(
                telemetry, cw,
                athlete_max_hr=athlete_max_hr,
                max_hr_run_count=max_hr_run_count,
                garmin_hr_zones=garmin_hr_zones,
            )

            logger.info(
                "CW %d (athlete %d, %s) zones [%s]: Z1=%.1f%% Z2=%.1f%% Z3=%.1f%% Z4=%.1f%% Z5=%.1f%%",
                cw_id, cw.athlete_id, cw.date, bio.get("zone_source", "?"),
                bio["zone1_pct"] or 0, bio["zone2_pct"] or 0, bio["zone3_pct"] or 0,
                bio["zone4_pct"] or 0, bio["zone5_pct"] or 0,
            )
            logger.info("  LTHR boundaries: %s", bio.get("garmin_zone_boundaries"))

            if dry_run:
                logger.info("DRY RUN — skipping Slack send")
                continue

            generate_post_run_feedback(
                athlete, cw, bio, db, slack_client, athlete_max_hr=athlete_max_hr
            )
            logger.info("Feedback sent for CW %d", cw_id)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(1)

    ids = [int(x) for x in args]
    reprocess(ids, dry_run=dry)
