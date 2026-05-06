"""Athlete management admin operations — surface-agnostic."""

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


def refresh_garmin_data(athlete_id: int, days_back: int = 7) -> dict:
    """Pull Garmin activities and health data for the last N days.

    Bypasses the normal yesterday+today restriction and the 16-hour stale
    guard, so missed activities (e.g. a weekend run) can be backfilled on demand.
    """
    from datetime import date, timedelta
    from running_coach_ai.database.models import Athlete, CompletedWorkout, HealthSnapshot
    from running_coach_ai.database.session import get_session
    from running_coach_ai.garmin.client import (
        get_garmin_client, is_garmin_auth_error,
        get_health_snapshot, fetch_athlete_lthr, fetch_activity_hr_zones,
    )
    from running_coach_ai.garmin.parser import parse_activity_summary, parse_health_snapshot
    from running_coach_ai.garmin.telemetry import extract_telemetry, ingest_lap_splits
    from running_coach_ai.coach.biomechanics import analyse_workout, update_running_profile
    from running_coach_ai.coach.feedback import generate_post_run_feedback
    from sqlalchemy import func as _sql_func

    RUNNING_TYPES = {
        "running", "trail_running", "treadmill_running", "track_running",
        "ultra_running", "virtual_run", "obstacle_run",
    }
    MIN_DURATION = 600

    with get_session() as db:
        a = db.get(Athlete, athlete_id)
        if not a:
            return {"ok": False, "error": "Athlete not found"}
        if not a.garmin_email or not a.garmin_password_encrypted:
            return {"ok": False, "error": "No Garmin credentials configured"}
        garmin_email = a.garmin_email
        garmin_pw = a.garmin_password_encrypted

    try:
        garmin = get_garmin_client(athlete_id, garmin_email, garmin_pw)
    except Exception as e:
        import traceback as _tb
        logger.error("Admin refresh: Garmin client failed for athlete %d: %s\n%s",
                     athlete_id, e, _tb.format_exc())
        msg = f"Garmin auth failed: {e}" if is_garmin_auth_error(e) else str(e)
        return {"ok": False, "error": msg}

    today = date.today()
    start_date = (today - timedelta(days=days_back)).isoformat()
    end_date = today.isoformat()
    results: dict = {"ok": True, "activities_ingested": 0, "health_days_updated": 0, "errors": []}

    # --- activities ---
    try:
        activities = garmin.get_activities_by_date(start_date, end_date)
    except Exception as e:
        results["errors"].append(f"Activity list fetch failed: {e}")
        activities = []

    for act in activities:
        aid = str(act.get("activityId", ""))
        if not aid or (act.get("duration") or 0) < MIN_DURATION:
            continue

        with get_session() as db:
            already = db.query(CompletedWorkout).filter(
                CompletedWorkout.athlete_id == athlete_id,
                CompletedWorkout.garmin_activity_id == aid,
                CompletedWorkout.duration_seconds.isnot(None),
            ).first()
            if already:
                continue

        try:
            activity_data = garmin.get_activity(aid)
            detail = garmin.get_activity_details(aid)

            with get_session() as db:
                athlete_row = db.get(Athlete, athlete_id)

                # Remove any incomplete stub so the unique constraint doesn't block re-ingestion
                stub = db.query(CompletedWorkout).filter(
                    CompletedWorkout.garmin_activity_id == aid,
                    CompletedWorkout.athlete_id == athlete_id,
                    CompletedWorkout.duration_seconds.is_(None),
                ).first()
                if stub:
                    db.delete(stub)
                    db.flush()

                completed = parse_activity_summary(activity_data, athlete_id, db)
                activity_type = completed.activity_type or "unknown"
                is_running = activity_type in RUNNING_TYPES

                if is_running:
                    telemetry = extract_telemetry(detail, completed.id, athlete_id, db)
                    ingest_lap_splits(garmin, athlete_id, aid, telemetry, db)

                    if not athlete_row.lthr_bpm:
                        lthr = fetch_athlete_lthr(garmin, athlete_id)
                        if lthr:
                            athlete_row.lthr_bpm = lthr

                    garmin_hr_zones = fetch_activity_hr_zones(garmin, aid, athlete_id)
                    athlete_max_hr = (
                        db.query(_sql_func.max(CompletedWorkout.max_hr))
                        .filter(CompletedWorkout.athlete_id == athlete_id,
                                CompletedWorkout.max_hr.isnot(None))
                        .scalar()
                    ) or completed.max_hr or 189
                    max_hr_run_count = (
                        db.query(_sql_func.count(CompletedWorkout.id))
                        .filter(CompletedWorkout.athlete_id == athlete_id,
                                CompletedWorkout.max_hr.isnot(None))
                        .scalar()
                    ) or 0
                    bio = analyse_workout(
                        telemetry, completed,
                        athlete_max_hr=athlete_max_hr,
                        max_hr_run_count=max_hr_run_count,
                        garmin_hr_zones=garmin_hr_zones,
                    )
                    update_running_profile(athlete_id, db)
                    generate_post_run_feedback(
                        athlete_row, completed, bio, db, athlete_max_hr=athlete_max_hr,
                    )
                else:
                    completed.feedback_given = True

                db.commit()

            results["activities_ingested"] += 1
            logger.info("Admin refresh: ingested activity %s for athlete %d", aid, athlete_id)
        except Exception as e:
            results["errors"].append(f"Activity {aid}: {e}")
            logger.error("Admin refresh: failed to ingest activity %s for athlete %d: %s", aid, athlete_id, e)

    # --- health snapshots ---
    for i in range(days_back + 1):
        day = today - timedelta(days=i)
        day_str = day.isoformat()
        with get_session() as db:
            snap = db.query(HealthSnapshot).filter(
                HealthSnapshot.athlete_id == athlete_id,
                HealthSnapshot.date == day,
            ).first()
            key_fields = ("sleep_score", "hrv_score", "body_battery_start")
            if snap and all(getattr(snap, f) is not None for f in key_fields):
                continue
        try:
            raw = get_health_snapshot(garmin, day_str)
            with get_session() as db:
                parse_health_snapshot(raw, athlete_id, day, db)
                db.commit()
            results["health_days_updated"] += 1
        except Exception as e:
            results["errors"].append(f"Health {day_str}: {e}")

    logger.info(
        "Admin Garmin refresh for athlete %d: %d activities, %d health days, %d errors",
        athlete_id, results["activities_ingested"], results["health_days_updated"], len(results["errors"]),
    )
    return results


def trigger_morning_checkin(athlete_id: int, *, force: bool = False) -> dict:
    """Manually fire the morning check-in for an athlete.

    `force=True` clears `last_morning_checkin_date` so the dedup gate doesn't
    skip it.
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
        _run_morning_checkin_for_athlete(athlete_id)
    except Exception as e:
        logger.error("Admin morning-checkin failed for athlete %d: %s", athlete_id, e)
        return {"ok": False, "error": str(e)}
    return {"ok": True, "error": None}
