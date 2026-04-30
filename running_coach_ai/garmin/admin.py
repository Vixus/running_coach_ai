"""Garmin admin operations — athlete-id-based, return structured dicts.

Used by the web admin UI; surface-agnostic.
"""

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from running_coach_ai.database.models import Athlete, PlannedWorkout

logger = logging.getLogger(__name__)


def _run_garmin_verify(athlete: Athlete, db: Session):
    """Compare every upcoming planned workout against the live Garmin
    library and calendar. Returns three lists of PlannedWorkout objects:

      matched      — in library AND on calendar for the correct date
      library_only — in library but NOT on calendar
      missing      — not in the library at all

    Raises on auth or API failure; callers should handle exceptions.
    """
    from running_coach_ai.garmin.client import (
        get_garmin_client, get_garmin_workout_library,
    )
    from running_coach_ai.garmin.workout_builder import APP_MARKER_RE

    today = date.today()

    upcoming = (
        db.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete.id,
            PlannedWorkout.scheduled_date >= today,
            PlannedWorkout.status.in_(["planned", "modified"]),
            PlannedWorkout.workout_type != "rest",
        )
        .order_by(PlannedWorkout.scheduled_date)
        .all()
    )

    if not upcoming:
        return [], [], []

    garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)

    # Scan library for [rca:{athlete_id}:{date}] markers
    library_by_date: dict[str, list[int]] = {}
    for entry in get_garmin_workout_library(garmin):
        m = APP_MARKER_RE.search(entry.get("description") or "")
        if m and int(m.group(1)) == athlete.id:
            library_by_date.setdefault(m.group(2), []).append(int(entry["workoutId"]))

    # The Garmin calendar date-range API is unreliable (returns 404 for empty
    # ranges); DB garmin_schedule_id is the ground truth for "is on calendar."
    matched, library_only, missing = [], [], []
    for w in upcoming:
        date_iso = w.scheduled_date.isoformat()
        lib_ids = library_by_date.get(date_iso, [])
        if not lib_ids:
            missing.append(w)
        elif w.garmin_schedule_id:
            matched.append(w)
        else:
            library_only.append(w)

    return matched, library_only, missing


def verify_garmin_for_athlete(athlete: Athlete, db: Session) -> dict:
    """Compare DB plan vs live Garmin library/calendar.

    Returns:
        {
            "ok": bool,
            "error": str | None,
            "matched": [{"date": iso, "workout_type": str}, ...],
            "library_only": [...],   # in library but not on calendar
            "missing": [...],        # not in library at all
        }
    """
    if not athlete.garmin_email or not athlete.garmin_password_encrypted:
        return {"ok": False, "error": "No Garmin credentials stored.",
                "matched": [], "library_only": [], "missing": []}

    try:
        matched, library_only, missing = _run_garmin_verify(athlete, db)
    except Exception as e:
        logger.error("verify_garmin failed for athlete %d: %s", athlete.id, e)
        return {"ok": False, "error": str(e),
                "matched": [], "library_only": [], "missing": []}

    def _ser(rows):
        return [{"date": w.scheduled_date.isoformat(), "workout_type": w.workout_type} for w in rows]

    return {
        "ok": True,
        "error": None,
        "matched": _ser(matched),
        "library_only": _ser(library_only),
        "missing": _ser(missing),
    }


def resync_garmin_for_athlete(athlete: Athlete, db: Session) -> dict:
    """Delete app-marked workouts from Garmin in the upcoming date range,
    clear stored Garmin IDs, and re-upload all upcoming workouts.

    Returns:
        {
            "ok": bool,
            "error": str | None,
            "deleted": int,           # workouts removed from Garmin
            "delete_failed": int,
            "uploaded": int,          # workouts re-uploaded
            "upload_failed": int,
            "failed_dates": [iso, ...],
            "errored_weeks": [iso, ...],
        }
    """
    from running_coach_ai.garmin.client import (
        delete_workout, get_garmin_client,
        get_garmin_workout_library, remove_workout_schedule,
    )
    from running_coach_ai.garmin.workout_builder import APP_MARKER_RE, sync_week_to_garmin

    result = {
        "ok": False, "error": None,
        "deleted": 0, "delete_failed": 0,
        "uploaded": 0, "upload_failed": 0,
        "failed_dates": [], "errored_weeks": [],
    }
    if not athlete.garmin_email or not athlete.garmin_password_encrypted:
        result["error"] = "No Garmin credentials stored."
        return result

    today = date.today()
    upcoming = (
        db.query(PlannedWorkout.scheduled_date)
        .filter(
            PlannedWorkout.athlete_id == athlete.id,
            PlannedWorkout.scheduled_date >= today,
            PlannedWorkout.status.in_(["planned", "modified"]),
            PlannedWorkout.workout_type != "rest",
        )
        .distinct()
        .all()
    )
    if not upcoming:
        result["ok"] = True
        return result

    last_date = max(d.scheduled_date for d in upcoming)

    try:
        garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)
    except Exception as e:
        result["error"] = f"Could not authenticate with Garmin: {e}"
        return result

    # Step 1: Delete app-marked workouts in the upcoming range.
    try:
        library = get_garmin_workout_library(garmin)
        marked_wids: list[int] = []
        for entry in library:
            m = APP_MARKER_RE.search(entry.get("description") or "")
            if m and int(m.group(1)) == athlete.id:
                try:
                    entry_date = date.fromisoformat(m.group(2))
                    if today <= entry_date <= last_date:
                        marked_wids.append(int(entry["workoutId"]))
                except (ValueError, KeyError):
                    pass

        schedule_by_wid: dict[int, int] = {}
        for w in (
            db.query(PlannedWorkout)
            .filter(
                PlannedWorkout.athlete_id == athlete.id,
                PlannedWorkout.scheduled_date >= today,
                PlannedWorkout.scheduled_date <= last_date,
                PlannedWorkout.garmin_workout_id.isnot(None),
                PlannedWorkout.garmin_schedule_id.isnot(None),
            )
            .all()
        ):
            try:
                schedule_by_wid[int(w.garmin_workout_id)] = int(w.garmin_schedule_id)
            except (ValueError, TypeError):
                pass

        for wid in marked_wids:
            try:
                if wid in schedule_by_wid:
                    remove_workout_schedule(garmin, schedule_by_wid[wid])
                delete_workout(garmin, wid)
                result["deleted"] += 1
            except Exception as e:
                logger.warning("Could not delete marked workout %s: %s", wid, e)
                result["delete_failed"] += 1
    except Exception as e:
        logger.warning("Garmin library scan failed for athlete %d: %s", athlete.id, e)

    # Step 2: Clear stored Garmin IDs in the affected range.
    db.query(PlannedWorkout).filter(
        PlannedWorkout.athlete_id == athlete.id,
        PlannedWorkout.scheduled_date >= today,
        PlannedWorkout.scheduled_date <= last_date,
    ).update({"garmin_workout_id": None, "garmin_schedule_id": None})
    db.commit()

    # Step 3: Re-upload week by week.
    week_starts = sorted({
        d.scheduled_date - timedelta(days=d.scheduled_date.weekday())
        for d in upcoming
    })
    for week_start in week_starts:
        try:
            up, fail, failed_dates = sync_week_to_garmin(
                athlete.id, athlete.garmin_email,
                athlete.garmin_password_encrypted, week_start, db,
            )
            result["uploaded"] += up
            result["upload_failed"] += fail
            result["failed_dates"].extend([d.isoformat() for d in failed_dates])
        except Exception as e:
            logger.error("Resync failed for athlete %d week %s: %s", athlete.id, week_start, e)
            result["errored_weeks"].append(week_start.isoformat())

    result["ok"] = True
    return result


def clean_garmin_for_athlete(athlete: Athlete, db: Session) -> dict:
    """Wipe the entire Garmin workout library and re-upload upcoming workouts.

    Returns: same shape as resync, plus `library_deleted` for total wipe count.
    """
    from running_coach_ai.garmin.client import (
        delete_workout, get_garmin_client, get_garmin_workout_library,
    )
    from running_coach_ai.garmin.workout_builder import sync_week_to_garmin

    result = {
        "ok": False, "error": None,
        "deleted": 0, "delete_failed": 0,
        "uploaded": 0, "upload_failed": 0,
        "failed_dates": [], "errored_weeks": [],
    }
    if not athlete.garmin_email or not athlete.garmin_password_encrypted:
        result["error"] = "No Garmin credentials stored."
        return result

    today = date.today()

    try:
        garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)
    except Exception as e:
        result["error"] = f"Could not authenticate with Garmin: {e}"
        return result

    # Step 1: Wipe entire library.
    library = get_garmin_workout_library(garmin)
    for entry in library:
        wid = entry.get("workoutId")
        if not wid:
            continue
        try:
            delete_workout(garmin, int(wid))
            result["deleted"] += 1
        except Exception as e:
            logger.warning("Could not delete Garmin workout %s: %s", wid, e)
            result["delete_failed"] += 1

    # Step 2: Clear future planned IDs only.
    db.query(PlannedWorkout).filter(
        PlannedWorkout.athlete_id == athlete.id,
        PlannedWorkout.scheduled_date >= today,
    ).update({"garmin_workout_id": None, "garmin_schedule_id": None})
    db.commit()

    # Step 3: Re-upload.
    upcoming = (
        db.query(PlannedWorkout.scheduled_date)
        .filter(
            PlannedWorkout.athlete_id == athlete.id,
            PlannedWorkout.scheduled_date >= today,
            PlannedWorkout.status.in_(["planned", "modified"]),
            PlannedWorkout.workout_type != "rest",
        )
        .distinct()
        .all()
    )
    week_starts = sorted({
        d.scheduled_date - timedelta(days=d.scheduled_date.weekday())
        for d in upcoming
    })
    for week_start in week_starts:
        try:
            up, fail, failed_dates = sync_week_to_garmin(
                athlete.id, athlete.garmin_email,
                athlete.garmin_password_encrypted, week_start, db,
            )
            result["uploaded"] += up
            result["upload_failed"] += fail
            result["failed_dates"].extend([d.isoformat() for d in failed_dates])
        except Exception as e:
            logger.error("Re-sync failed for week %s: %s", week_start, e)
            result["errored_weeks"].append(week_start.isoformat())

    result["ok"] = True
    return result
