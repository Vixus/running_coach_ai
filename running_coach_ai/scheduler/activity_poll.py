"""Activity polling and post-run feedback scheduler job.

Runs every 30 min between 06:00 and 22:00. For each athlete with a cached
Garmin session, polls for new activities, ingests them, runs biomechanics
analysis on running activities, and writes a coaching feedback notification.
"""

import logging
from datetime import datetime, timedelta

from running_coach_ai.scheduler._helpers import _notify_garmin_auth_error
from running_coach_ai.scheduler.morning import register_athlete_morning_job

logger = logging.getLogger(__name__)


def _run_activity_poll(scheduler=None) -> None:
    """Activity polling job — runs every 30 min."""
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.database.session import get_session
    from running_coach_ai.garmin.client import get_garmin_client, poll_new_activities

    logger.info("Activity poll starting")
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
            try:
                if not athlete.garmin_email or not athlete.garmin_password_encrypted:
                    continue
                # Skip re-auth if no cached session exists — avoids 429 rate limits
                # from repeated SSO login attempts. The session is created during
                # onboarding or via !admin resync-garmin.
                import os as _os
                from running_coach_ai.config import settings as _settings
                token_dir = _os.path.join(_settings.GARMIN_SESSION_DIR, str(athlete.id))
                if not _os.path.isfile(_os.path.join(token_dir, "oauth1_token.json")):
                    logger.debug("Skipping activity poll for athlete %d: no cached Garmin session", athlete.id)
                    continue
                garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)
                new_ids = poll_new_activities(garmin, athlete.id, db_session)

                for activity_id in new_ids:
                    _ingest_and_feedback(athlete, activity_id, garmin, db_session, scheduler)

                # Retry any fully-ingested runs where feedback wasn't sent yet
                # (covers startup after crash, reprocessing after algorithm fixes, etc.)
                _retry_pending_feedback(athlete, garmin, db_session)

            except Exception as e:
                from running_coach_ai.garmin.client import is_garmin_auth_error
                if is_garmin_auth_error(e):
                    _notify_garmin_auth_error(athlete)
                logger.error("Activity poll failed for athlete %d: %s", athlete.id, e)


def _retry_pending_feedback(athlete, garmin, db_session) -> None:
    """Send feedback for fully-ingested running runs that haven't received it yet.

    Covers: restarts after a crash, manual feedback_given resets, and algorithm
    fixes where feedback needs to be re-sent. Only looks at runs from the last
    48 hours to avoid spamming old history.
    """
    from running_coach_ai.database.models import CompletedWorkout as _CW2, WorkoutTelemetry
    from running_coach_ai.coach.biomechanics import analyse_workout
    from running_coach_ai.coach.feedback import generate_post_run_feedback
    from running_coach_ai.garmin.client import fetch_athlete_lthr, fetch_activity_hr_zones
    from sqlalchemy import func as _sql_func

    RUNNING_TYPES = {
        "running", "trail_running", "treadmill_running", "track_running",
        "ultra_running", "virtual_run", "obstacle_run",
    }
    cutoff = (datetime.now() - timedelta(hours=48)).date()
    pending = (
        db_session.query(_CW2)
        .filter(
            _CW2.athlete_id == athlete.id,
            _CW2.feedback_given == False,
            _CW2.duration_seconds.isnot(None),
            _CW2.date >= cutoff,
            _CW2.activity_type.in_(RUNNING_TYPES),
        )
        .all()
    )
    if not pending:
        return

    # Refresh LTHR once per athlete if not stored
    if not athlete.lthr_bpm:
        lthr = fetch_athlete_lthr(garmin, athlete.id)
        if lthr:
            athlete.lthr_bpm = lthr

    athlete_max_hr = (
        db_session.query(_sql_func.max(_CW2.max_hr))
        .filter(_CW2.athlete_id == athlete.id, _CW2.max_hr.isnot(None))
        .scalar()
    ) or 189
    max_hr_run_count = (
        db_session.query(_sql_func.count(_CW2.id))
        .filter(_CW2.athlete_id == athlete.id, _CW2.max_hr.isnot(None))
        .scalar()
    ) or 0

    for cw in pending:
        try:
            telemetry = (
                db_session.query(WorkoutTelemetry)
                .filter(WorkoutTelemetry.completed_workout_id == cw.id)
                .first()
            )
            if not telemetry:
                logger.warning("No telemetry for pending CW %d — skipping retry", cw.id)
                continue

            garmin_hr_zones = fetch_activity_hr_zones(garmin, cw.garmin_activity_id, athlete.id)
            bio = analyse_workout(
                telemetry, cw,
                athlete_max_hr=athlete_max_hr,
                max_hr_run_count=max_hr_run_count,
                garmin_hr_zones=garmin_hr_zones,
            )
            generate_post_run_feedback(
                athlete, cw, bio, db_session, athlete_max_hr=athlete_max_hr,
            )
            logger.info("Pending feedback sent for CW %d (athlete %d)", cw.id, athlete.id)
        except Exception as e:
            logger.error("Pending feedback retry failed for CW %d athlete %d: %s", cw.id, athlete.id, e)


def _ingest_and_feedback(athlete, activity_id: str, garmin, db_session, scheduler=None) -> None:
    """Ingest a new activity and send post-run feedback."""
    from running_coach_ai.garmin.parser import parse_activity_summary
    from running_coach_ai.garmin.telemetry import extract_telemetry, ingest_lap_splits
    from running_coach_ai.coach.biomechanics import analyse_workout, update_running_profile
    from running_coach_ai.coach.feedback import generate_post_run_feedback

    # Garmin typeKey values that qualify for the full running pipeline.
    RUNNING_TYPES = {
        "running", "trail_running", "treadmill_running", "track_running",
        "ultra_running", "virtual_run", "obstacle_run",
    }

    # Stale-window guard: skip activities older than 16 hours to avoid
    # sending feedback for workouts completed outside the active-hours window.
    stale_cutoff = datetime.now() - timedelta(hours=16)

    try:
        # get_activity() → summary metrics (distance, HR, pace, GPS coords) in summaryDTO
        # get_activity_details() → time-series telemetry (metricDescriptors, activityDetailMetrics)
        activity_data = garmin.get_activity(activity_id)
        detail = garmin.get_activity_details(activity_id)

        # Check activity start time if available; skip if stale
        start_time_local = (activity_data.get("summaryDTO") or {}).get("startTimeLocal")
        if start_time_local:
            try:
                activity_dt = datetime.fromisoformat(start_time_local.replace("Z", ""))
                if activity_dt < stale_cutoff:
                    logger.info(
                        "Skipped stale operation: activity %s for athlete %d started at %s "
                        "(> 16 h ago) — skipping post-run feedback",
                        activity_id, athlete.id, start_time_local,
                    )
                    return
            except Exception:
                pass  # unparseable timestamp — proceed normally

        # Remove any prior incomplete stub (null duration_seconds = partial ingest)
        # so the unique constraint on garmin_activity_id doesn't block re-ingestion.
        from running_coach_ai.database.models import CompletedWorkout as _CW
        stub = db_session.query(_CW).filter(
            _CW.garmin_activity_id == str(activity_id),
            _CW.athlete_id == athlete.id,
            _CW.duration_seconds.is_(None),
        ).first()
        if stub:
            logger.info(
                "Removing incomplete stub for activity %s athlete %d before re-ingestion",
                activity_id, athlete.id,
            )
            db_session.delete(stub)
            db_session.flush()

        completed = parse_activity_summary(activity_data, athlete.id, db_session)

        # Determine whether this is a running activity or cross-training.
        activity_type = completed.activity_type or "unknown"
        is_running = activity_type in RUNNING_TYPES

        if is_running:
            telemetry = extract_telemetry(detail, completed.id, athlete.id, db_session)
            ingest_lap_splits(garmin, athlete.id, activity_id, telemetry, db_session)

            # Refresh LTHR from Garmin profile if not yet stored (or stale)
            from running_coach_ai.garmin.client import fetch_athlete_lthr, fetch_activity_hr_zones
            if not athlete.lthr_bpm:
                lthr = fetch_athlete_lthr(garmin, athlete.id)
                if lthr:
                    athlete.lthr_bpm = lthr

            # Fetch Garmin's pre-computed zone breakdown (LTHR-based, matches Garmin app)
            garmin_hr_zones = fetch_activity_hr_zones(garmin, str(activity_id), athlete.id)

            # Keep rolling max HR for legacy fallback only
            from sqlalchemy import func as _sql_func
            athlete_max_hr = (
                db_session.query(_sql_func.max(_CW.max_hr))
                .filter(_CW.athlete_id == athlete.id, _CW.max_hr.isnot(None))
                .scalar()
            ) or completed.max_hr or 189
            max_hr_run_count = (
                db_session.query(_sql_func.count(_CW.id))
                .filter(_CW.athlete_id == athlete.id, _CW.max_hr.isnot(None))
                .scalar()
            ) or 0
            biomechanics_result = analyse_workout(
                telemetry, completed,
                athlete_max_hr=athlete_max_hr,
                max_hr_run_count=max_hr_run_count,
                garmin_hr_zones=garmin_hr_zones,
            )
            update_running_profile(athlete.id, db_session)
            generate_post_run_feedback(
                athlete, completed, biomechanics_result, db_session,
                athlete_max_hr=athlete_max_hr,
            )
        else:
            # Cross-training (skiing, hiking, tennis, etc.): store the activity for
            # fatigue/fitness context in coaching conversations but skip running-specific
            # telemetry, biomechanics analysis, and post-activity feedback messages.
            completed.feedback_given = True
            logger.info(
                "Cross-training activity %s (%s) ingested for athlete %d — skipping running pipeline",
                activity_id, activity_type, athlete.id,
            )

        db_session.commit()

        # Timezone auto-detection from GPS
        from running_coach_ai.garmin.parser import extract_activity_start_coords
        from running_coach_ai.coach.timezone_utils import derive_timezone_from_coords
        coords = extract_activity_start_coords(activity_data)
        if coords:
            new_tz = derive_timezone_from_coords(*coords)
            if new_tz and new_tz != athlete.timezone:
                old_tz = athlete.timezone
                athlete.timezone = new_tz
                db_session.commit()
                logger.info(
                    "Timezone updated for athlete %d: %s -> %s",
                    athlete.id, old_tz, new_tz,
                )
                if scheduler is not None:
                    try:
                        register_athlete_morning_job(scheduler, athlete)
                    except Exception as sched_e:
                        logger.error(
                            "Failed to re-register morning job for athlete %d: %s",
                            athlete.id, sched_e,
                        )
    except Exception as e:
        logger.error("Failed to ingest activity %s for athlete %d: %s", activity_id, athlete.id, e)
