"""APScheduler job definitions — morning check-in, activity poll, weekly review."""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# Rate-limit Garmin auth-error DMs to one per athlete per day (keyed by athlete_id → date).
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


def _next_checkin_start(tz_name: str) -> datetime:
    """Return the start_date for the morning check-in IntervalTrigger.

    - Before 06:00 local → today's 06:00
    - 06:00–noon local   → now (fire immediately; dedup guard prevents double-send)
    - After noon local   → tomorrow's 06:00 (too late to retry today)
    """
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    today_6am = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    today_noon = now_local.replace(hour=12, minute=0, second=0, microsecond=0)

    if now_local < today_6am:
        return today_6am
    if now_local < today_noon:
        return now_local
    return today_6am + timedelta(days=1)


# ---------------------------------------------------------------------------
# Job functions
# ---------------------------------------------------------------------------

def _run_morning_checkin_for_athlete(athlete_id: int) -> None:
    """Morning check-in job for a single athlete."""
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.database.session import get_session
    from running_coach_ai.coach.adapter import run_morning_checkin
    from running_coach_ai.garmin.client import is_garmin_auth_error

    logger.info("Morning check-in starting for athlete %d", athlete_id)
    try:
        with get_session() as db_session:
            athlete = db_session.query(Athlete).get(athlete_id)
            if not athlete or not athlete.allowed or not athlete.onboarding_complete:
                return
            try:
                run_morning_checkin(athlete, db_session)
            except Exception as inner_e:
                if is_garmin_auth_error(inner_e):
                    _notify_garmin_auth_error(athlete)
                raise
    except Exception as e:
        logger.error("Morning check-in failed for athlete %d: %s", athlete_id, e)


def _run_activity_poll(scheduler=None) -> None:
    """Activity polling job — runs every 10 min."""
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
    from datetime import timedelta
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
                    athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted
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


def _upsert_weekly_review_summary(athlete_id: int, week_start, week_summary: dict, narrative: str, db_session) -> None:
    """Persist the weekly review to WeeklyReviewSummary using SQLite upsert."""
    from running_coach_ai.database.models import WeeklyReviewSummary, CompletedWorkout, HealthSnapshot, PlannedWorkout
    from sqlalchemy.dialects.sqlite import insert
    from running_coach_ai.coach.persona import km_to_mi

    # Build daily volume array (Mon–Sun) from completed workouts
    week_end = week_start + timedelta(days=6)
    completed = (
        db_session.query(CompletedWorkout)
        .filter(
            CompletedWorkout.athlete_id == athlete_id,
            CompletedWorkout.date >= week_start,
            CompletedWorkout.date <= week_end,
        )
        .all()
    )
    daily_volume = [0.0] * 7
    for cw in completed:
        dow = cw.date.weekday()  # 0=Mon
        daily_volume[dow] = round(daily_volume[dow] + km_to_mi(cw.distance_km or 0), 1)

    # Collect 8-week body battery data (most recent 8 Sundays)
    eight_weeks_ago = week_start - timedelta(weeks=7)
    health_rows = (
        db_session.query(HealthSnapshot)
        .filter(
            HealthSnapshot.athlete_id == athlete_id,
            HealthSnapshot.date >= eight_weeks_ago,
            HealthSnapshot.date <= week_end,
        )
        .order_by(HealthSnapshot.date.asc())
        .all()
    )
    body_battery = [h.body_battery_start for h in health_rows if h.body_battery_start is not None][-8:]

    # Build next-week preview from PlannedWorkout rows
    next_week_start = week_start + timedelta(weeks=1)
    next_week_end = next_week_start + timedelta(days=6)
    planned_next = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete_id,
            PlannedWorkout.scheduled_date >= next_week_start,
            PlannedWorkout.scheduled_date <= next_week_end,
        )
        .order_by(PlannedWorkout.scheduled_date.asc())
        .all()
    )
    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    next_week_json = [
        {
            "day": day_labels[pw.scheduled_date.weekday()],
            "type": pw.workout_type or "rest",
            "label": pw.notes or pw.workout_type or "Rest",
        }
        for pw in planned_next
    ]

    total_miles = round(km_to_mi(week_summary.get("actual_km", 0) or 0), 1)
    elevation_ft = None  # elevation not tracked in aggregate_week; kept null
    avg_hrv = week_summary.get("avg_hrv")
    total_tss = week_summary.get("total_training_load")

    stmt = insert(WeeklyReviewSummary).values(
        athlete_id=athlete_id,
        week_start_date=week_start,
        narrative=narrative,
        total_miles=total_miles,
        elevation_gain_ft=elevation_ft,
        avg_hrv=avg_hrv,
        total_tss=total_tss,
        daily_volume_json=daily_volume,
        body_battery_json=body_battery,
        next_week_json=next_week_json,
    ).on_conflict_do_update(
        index_elements=["athlete_id", "week_start_date"],
        set_={
            "narrative": narrative,
            "total_miles": total_miles,
            "elevation_gain_ft": elevation_ft,
            "avg_hrv": avg_hrv,
            "total_tss": total_tss,
            "daily_volume_json": daily_volume,
            "body_battery_json": body_battery,
            "next_week_json": next_week_json,
        },
    )
    db_session.execute(stmt)
    db_session.commit()
    logger.info("WeeklyReviewSummary upserted for athlete %d week %s", athlete_id, week_start)


def _run_weekly_review() -> None:
    """Weekly review job — runs Sunday 20:00."""
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.database.session import get_session
    from running_coach_ai.coach.planner import aggregate_week
    from running_coach_ai.coach.feedback import generate_weekly_review
    from running_coach_ai.coach.adapter import adapt_next_week
    from running_coach_ai.garmin.workout_builder import sync_week_to_garmin

    logger.info("Weekly review starting")
    today = date.today()
    week_start = today - timedelta(days=today.weekday())

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
                week_summary = aggregate_week(athlete.id, week_start, db_session)
                review_message = generate_weekly_review(athlete, week_summary, db_session)
                adapt_next_week(athlete, week_summary, db_session)

                # Persist the weekly review summary for the web dashboard
                _upsert_weekly_review_summary(athlete.id, week_start, week_summary, review_message, db_session)

                # Sync next 2 weeks to Garmin
                if athlete.garmin_email and athlete.garmin_password_encrypted:
                    for offset in (1, 2):
                        target_week = week_start + timedelta(weeks=offset)
                        try:
                            sync_week_to_garmin(
                                athlete.id,
                                athlete.garmin_email,
                                athlete.garmin_password_encrypted,
                                target_week,
                                db_session,
                            )
                        except Exception as sync_e:
                            logger.error(
                                "Weekly review Garmin sync failed for athlete %d week %s: %s",
                                athlete.id, target_week, sync_e,
                            )

                from running_coach_ai.coach.notify import notify
                notify(
                    db_session, athlete,
                    kind="weekly_review",
                    title="Weekly review",
                    body=review_message,
                    action_path="/#story",
                )
                db_session.commit()
                logger.info("Weekly review delivered to athlete %d", athlete.id)

            except Exception as e:
                logger.error("Weekly review failed for athlete %d: %s", athlete.id, e)


# ---------------------------------------------------------------------------
# Job registration
# ---------------------------------------------------------------------------

def register_jobs(scheduler: BlockingScheduler) -> None:
    """Register all scheduled jobs. Called from main.py at startup."""
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.database.session import get_session

    # Morning check-in — one IntervalTrigger per athlete polling every 30 min from 07:00 local
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
            tz = athlete.timezone or "America/New_York"
            scheduler.add_job(
                _run_morning_checkin_for_athlete,
                IntervalTrigger(minutes=30, start_date=_next_checkin_start(tz), timezone=tz),
                args=[athlete.id],
                id=f"morning_checkin_{athlete.id}",
                replace_existing=True,
                misfire_grace_time=300,
            )
            logger.info("Registered morning check-in for athlete %d (%s)", athlete.id, tz)

    # Activity poll — every 30 minutes, 06:00–22:00 only
    scheduler.add_job(
        _run_activity_poll,
        CronTrigger(minute="*/30", hour="6-21"),
        args=[scheduler],
        id="activity_poll",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Weekly review — Sunday 20:00 system time
    scheduler.add_job(
        _run_weekly_review,
        CronTrigger(day_of_week="sun", hour=20, minute=0),
        id="weekly_review",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Daily Garmin reconciliation — 08:30, after morning check-ins have run
    scheduler.add_job(
        _run_garmin_reconciliation,
        CronTrigger(hour=8, minute=30),
        id="garmin_reconciliation",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    # Daily health data backfill — 14:00, catches any missed morning health snapshots
    scheduler.add_job(
        _run_health_backfill,
        CronTrigger(hour=14, minute=0),
        id="health_backfill",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    # Refresh athlete morning jobs every 5 minutes — picks up athletes onboarded
    # via the web after the scheduler started, without a restart.
    scheduler.add_job(
        _refresh_athlete_morning_jobs,
        IntervalTrigger(minutes=5),
        args=[scheduler],
        id="refresh_athlete_morning_jobs",
        replace_existing=True,
        misfire_grace_time=300,
    )

    logger.info("All scheduler jobs registered")


def _refresh_athlete_morning_jobs(scheduler) -> None:
    """Idempotently ensure every onboarded athlete has a morning check-in job.

    Picks up athletes onboarded via the web after the scheduler started.
    """
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.database.session import get_session

    with get_session() as db_session:
        athletes = (
            db_session.query(Athlete)
            .filter(
                Athlete.allowed == True,
                Athlete.onboarding_complete == True,
            )
            .all()
        )
        existing = {j.id for j in scheduler.get_jobs()}
        for athlete in athletes:
            job_id = f"morning_checkin_{athlete.id}"
            if job_id in existing:
                continue
            tz = athlete.timezone or "America/New_York"
            scheduler.add_job(
                _run_morning_checkin_for_athlete,
                IntervalTrigger(minutes=30, start_date=_next_checkin_start(tz), timezone=tz),
                args=[athlete.id],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=300,
            )
            logger.info("Refresh: registered morning check-in for athlete %d (%s)", athlete.id, tz)


def register_athlete_morning_job(scheduler: BlockingScheduler, athlete) -> None:
    """Register (or re-register) the morning check-in job for a single athlete.

    Called after new athlete onboarding completes so the job takes effect
    without restarting the scheduler.
    """
    tz = athlete.timezone or "America/New_York"
    scheduler.add_job(
        _run_morning_checkin_for_athlete,
        IntervalTrigger(minutes=30, start_date=_next_checkin_start(tz), timezone=tz),
        args=[athlete.id],
        id=f"morning_checkin_{athlete.id}",
        replace_existing=True,
        misfire_grace_time=300,
    )
    logger.info("Registered morning check-in for newly onboarded athlete %d (%s)", athlete.id, tz)
