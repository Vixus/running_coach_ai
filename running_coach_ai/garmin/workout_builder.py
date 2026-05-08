"""Build Garmin Connect workout JSON for upload."""

import logging
import re
from datetime import date, datetime

from sqlalchemy.orm import Session

from running_coach_ai.database.models import PlannedWorkout
from running_coach_ai.garmin.client import (
    delete_workout,
    get_garmin_client,
    get_garmin_workout_library,
    remove_workout_schedule,
    schedule_workout,
    update_workout,
    upload_workout,
)

# Marker embedded in every workout description to identify app-created workouts.
# Format: [rca:{athlete_id}:{YYYY-MM-DD}]
# This allows us to scan the Garmin library and delete stale duplicates reliably,
# without depending on stored IDs that may have gone stale across failed syncs.
APP_MARKER_RE = re.compile(r'\[rca:(\d+):(\d{4}-\d{2}-\d{2})\]')

logger = logging.getLogger(__name__)

# Map internal step type names to valid Garmin stepTypeKey values.
# Garmin accepts: warmup, cooldown, interval, recovery, rest.
# "tempo" and other internal types map to "interval".
# Map internal step type names → (stepTypeId, stepTypeKey)
# Garmin step type IDs: 1=warmup, 2=cooldown, 3=interval, 4=recovery, 5=rest
_GARMIN_STEP_TYPE = {
    "warmup":   (1, "warmup"),
    "cooldown": (2, "cooldown"),
    "interval": (3, "interval"),
    "recovery": (4, "recovery"),
    "rest":     (5, "rest"),
    "tempo":    (3, "interval"),   # no "tempo" type in Garmin — use interval with pace
    "easy":     (3, "interval"),
    "run":      (3, "interval"),
}

# Human-readable display names for each workout type (used in Garmin workout name)
_WORKOUT_DISPLAY_NAMES = {
    "easy": "Easy Run",
    "long_run": "Long Run",
    "tempo": "Tempo Run",
    "intervals": "Intervals",
    "strides": "Easy Run + Strides",
    "cross_train": "Cross-Training",
    "rest": "Rest Day",
    "race": "Race",
}


def _pace_to_speed_ms(pace_min_per_km: float) -> float:
    """Convert pace (min/km) to speed in m/s for the Garmin pace.zone API.

    Garmin's pace.zone targetValueOne/Two must be in m/s (float), not sec/km.
    The device converts m/s back to min/mi or min/km for display based on
    the athlete's unit preference.

      5:00/km  →  1000/(5.0*60)  = 3.333 m/s
      6:00/km  →  1000/(6.0*60)  = 2.778 m/s
      9:30/mi  →  5.905 min/km   = 2.824 m/s

    Valid running paces are 2–20 min/km. Values outside this range indicate a
    unit error (e.g. Claude put min/mi instead of min/km in a <plan> tag).
    """
    if pace_min_per_km < 2.0 or pace_min_per_km > 20.0:
        logger.warning(
            "Pace value %.2f min/km is outside the valid 2-20 min/km range -- "
            "likely a unit error in plan data. Clamping to valid range.",
            pace_min_per_km,
        )
        pace_min_per_km = max(2.0, min(20.0, pace_min_per_km))
    return 1000.0 / (pace_min_per_km * 60)  # m/s


_MI_TO_M = 1609.344  # meters per mile


def _km_to_integer_miles_in_meters(km: float) -> float:
    """Round a km distance to the nearest integer mile, return in meters.

    Used so Garmin shows a clean "5 mi" or "10 mi" target rather than
    a fractional metric value.  8.05 km → 5.0 mi → 8046.7 m
    """
    miles = km * 1000.0 / _MI_TO_M   # km → m → mi
    miles_int = max(1, round(miles))
    return miles_int * _MI_TO_M


def _build_simple_step(
    order: int,
    step_type: str,
    distance_m: float | None = None,
    duration_secs: float | None = None,
    pace_min_per_km: float | None = None,
) -> dict:
    """Build a single ExecutableStepDTO.

    Pace zone (±5% window) is set on every step that has a pace target.
    Garmin devices convert the sec/km value to min/mi for display when set to imperial.
    """
    type_id, type_key = _GARMIN_STEP_TYPE.get(step_type, (3, "interval"))
    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": type_id, "stepTypeKey": type_key},
    }

    if distance_m is not None:
        step["endCondition"] = {"conditionTypeId": 3, "conditionTypeKey": "distance"}
        step["endConditionValue"] = distance_m
    elif duration_secs is not None:
        step["endCondition"] = {"conditionTypeId": 2, "conditionTypeKey": "time"}
        step["endConditionValue"] = duration_secs
    else:
        step["endCondition"] = {"conditionTypeId": 1, "conditionTypeKey": "lap.button"}
        step["endConditionValue"] = None

    if pace_min_per_km is not None:
        speed_ms = _pace_to_speed_ms(pace_min_per_km)
        # ±5% speed window — higher speed = faster pace, lower speed = slower pace
        step["targetType"] = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
        step["targetValueOne"] = round(speed_ms * 1.05, 4)   # faster bound (higher speed)
        step["targetValueTwo"] = round(speed_ms * 0.95, 4)   # slower bound (lower speed)
    else:
        # No pace target — do NOT set targetValueOne/Two to 0.
        # Garmin stores 0.0 in targetValueTwo and some devices/apps misinterpret it
        # as a pace (displaying "0:05/mi"). Omitting the fields avoids this.
        step["targetType"] = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}

    return step


def build_workout_json(planned_workout: PlannedWorkout) -> dict:
    """Build a Garmin Connect workout JSON from a PlannedWorkout.

    Naming: short display name only ("Easy Run", "Long Run", etc.) — Garmin
    shows the date from the calendar so it doesn't need to be in the name.

    Distance-based simple sessions (easy, long_run, strides): distance is
    rounded to the nearest integer mile so Garmin shows a clean "8 mi" target.

    Structured sessions (tempo, intervals): steps are time-based (integer
    minutes for warmup/tempo/cooldown) or 200m-multiple distance for repeats.

    All steps with a pace target get a ±5% pace zone. Garmin devices display
    this in min/mi when the device is set to imperial units.
    """
    # Determine workout type for naming - detect races
    workout_type_for_name = planned_workout.workout_type
    if (planned_workout.description and "RACE DAY" in planned_workout.description.upper()) or \
       (planned_workout.target_distance_km and planned_workout.target_distance_km >= 40):  # Marathon or ultra
        workout_type_for_name = "race"

    workout_name = (
        planned_workout.workout_name
        or _WORKOUT_DISPLAY_NAMES.get(
            workout_type_for_name,
            workout_type_for_name.replace("_", " ").title(),
        )
    )

    # Embed the app marker so we can identify and delete this workout during future syncs,
    # even if the stored Garmin ID has gone stale.
    marker = f"[rca:{planned_workout.athlete_id}:{planned_workout.scheduled_date}]"
    base_description = planned_workout.description or ""
    description = f"{marker} {base_description}".strip() if base_description else marker

    workout = {
        "workoutName": workout_name,
        "description": description,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
                "workoutSteps": [],
            }
        ],
    }

    steps_list = workout["workoutSegments"][0]["workoutSteps"]
    zones = planned_workout.target_zones_json

    if zones and "steps" in zones:
        # Structured session — build from zone steps (tempo/intervals)
        step_order = 1
        for zone_step in zones["steps"]:
            step_type = zone_step["type"]

            if step_type == "interval" and zone_step.get("reps", 1) > 1:
                # RepeatGroupDTO for interval repeats (distance in meters, 200m multiples)
                interval_step = _build_simple_step(
                    order=1,
                    step_type="interval",
                    distance_m=zone_step.get("distance_m"),
                    duration_secs=zone_step.get("duration_min", 0) * 60 if zone_step.get("duration_min") else None,
                    pace_min_per_km=zone_step.get("pace_min_per_km"),
                )
                recovery_step = _build_simple_step(
                    order=2,
                    step_type="recovery",
                    duration_secs=zone_step.get("recovery_min", 2) * 60,
                    pace_min_per_km=None,
                )
                repeat_group = {
                    "type": "RepeatGroupDTO",
                    "stepOrder": step_order,
                    "numberOfIterations": zone_step["reps"],
                    "workoutSteps": [interval_step, recovery_step],
                }
                steps_list.append(repeat_group)
            else:
                # Single step — duration_min comes from planner in integer minutes
                step = _build_simple_step(
                    order=step_order,
                    step_type=step_type,
                    distance_m=zone_step.get("distance_m"),
                    duration_secs=zone_step.get("duration_min", 0) * 60 if zone_step.get("duration_min") else None,
                    pace_min_per_km=zone_step.get("pace_min_per_km"),
                )
                steps_list.append(step)

            step_order += 1
    else:
        # Time-based types: easy and strides are prescribed by duration, not distance.
        # Resolution order:
        #   1. distance × pace → duration (planner always sets both)
        #   2. parse "X min" from description (Claude-created workouts may omit distance)
        # Long runs and races remain distance-based.
        _TIME_BASED_TYPES = {"easy", "strides", "cross_train"}
        duration_min: float | None = None
        if planned_workout.workout_type in _TIME_BASED_TYPES:
            if planned_workout.target_duration_seconds:
                duration_min = max(5, round(planned_workout.target_duration_seconds / 60 / 5) * 5)
            elif planned_workout.target_distance_km and planned_workout.target_pace_min_per_km:
                raw_min = planned_workout.target_distance_km * planned_workout.target_pace_min_per_km
                duration_min = max(5, round(raw_min / 5) * 5)
            elif planned_workout.description:
                m = re.search(r'(\d+)\s*min', planned_workout.description, re.IGNORECASE)
                if m:
                    duration_min = max(5, round(int(m.group(1)) / 5) * 5)

        if duration_min is not None:
            step = _build_simple_step(
                order=1,
                step_type="interval",
                duration_secs=duration_min * 60,
                pace_min_per_km=planned_workout.target_pace_min_per_km,
            )
            steps_list.append(step)
        else:
            # Distance-based: round to nearest integer mile
            distance_m = _km_to_integer_miles_in_meters(
                planned_workout.target_distance_km or 5.0
            )
            step = _build_simple_step(
                order=1,
                step_type="interval",
                distance_m=distance_m,
                pace_min_per_km=planned_workout.target_pace_min_per_km,
            )
            steps_list.append(step)
            workout["estimatedDistanceInMeters"] = distance_m

    return workout


def sync_week_to_garmin(
    athlete_id: int,
    email: str,
    encrypted_password: bytes,
    week_start_date: date,
    db_session: Session,
    garmin_client=None,
) -> tuple[int, int, list[date]]:
    """Upload (or re-upload) future planned workouts for a given week to Garmin.

    Only syncs workouts scheduled on or after today — never writes to the past.
    Deletes existing Garmin workout + schedule entries before re-uploading.
    Returns (uploaded, failed, failed_dates) counts and list of dates that failed.

    Pass an already-authenticated ``garmin_client`` to avoid a fresh login on
    every call (important when syncing multiple weeks in a tight loop).
    """
    from datetime import timedelta

    today = date.today()
    week_end_date = week_start_date + timedelta(days=6)
    workouts = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete_id,
            PlannedWorkout.scheduled_date >= week_start_date,
            PlannedWorkout.scheduled_date <= week_end_date,
            PlannedWorkout.scheduled_date >= today,   # never sync past dates
            PlannedWorkout.status.in_(["planned", "modified"]),
            PlannedWorkout.workout_type != "rest",
        )
        .all()
    )

    if not workouts:
        logger.info("No workouts to sync for athlete %d, week of %s", athlete_id, week_start_date)
        return 0, 0, []

    # Pre-compute per-type fallback paces from recent workouts that DO have a pace.
    # Used when a planned workout has target_pace_min_per_km=None (plan was incomplete).
    fallback_paces: dict[str, float] = {}
    for wt in ("easy", "long_run", "tempo", "strides", "intervals"):
        sample = (
            db_session.query(PlannedWorkout.target_pace_min_per_km)
            .filter(
                PlannedWorkout.athlete_id == athlete_id,
                PlannedWorkout.workout_type == wt,
                PlannedWorkout.target_pace_min_per_km.isnot(None),
            )
            .order_by(PlannedWorkout.scheduled_date.desc())
            .limit(1)
            .scalar()
        )
        if sample:
            fallback_paces[wt] = sample

    garmin = garmin_client if garmin_client is not None else get_garmin_client(athlete_id, email, encrypted_password, db_session)

    # --- Pre-scan: build a map of app-created library entries for this week's dates ---
    # Scans the description field for [rca:{athlete_id}:{date}] markers so we can delete
    # stale duplicates even when stored IDs have gone out of sync.
    app_by_date: dict[str, list[int]] = {}  # date_iso -> [workout_id, ...]
    for entry in get_garmin_workout_library(garmin):
        m = APP_MARKER_RE.search(entry.get("description") or "")
        if m and int(m.group(1)) == athlete_id:
            app_by_date.setdefault(m.group(2), []).append(int(entry["workoutId"]))

    # Build schedule entry index from DB: workout_id -> schedule_id.
    # The Garmin calendar date-range API is unreliable (returns 404 for empty ranges),
    # so we use stored IDs from the DB instead.
    schedule_by_wid: dict[int, int] = {}
    for w in workouts:
        if w.garmin_workout_id and w.garmin_schedule_id:
            try:
                schedule_by_wid[int(w.garmin_workout_id)] = int(w.garmin_schedule_id)
            except (ValueError, TypeError):
                pass

    uploaded = 0
    failed = 0
    failed_dates: list[date] = []

    for workout in workouts:
        # Fill missing pace from fallback so all synced workouts have a target.
        # If no fallback exists either, raise immediately so the failure is captured
        # with a clear message rather than producing a broken zero-pace payload.
        if workout.target_pace_min_per_km is None:
            if workout.workout_type in fallback_paces:
                logger.info(
                    "Workout %s (%s) missing pace — using fallback %.2f min/km",
                    workout.scheduled_date, workout.workout_type, fallback_paces[workout.workout_type],
                )
                workout.target_pace_min_per_km = fallback_paces[workout.workout_type]
            else:
                raise ValueError(
                    f"No pace for {workout.workout_type} on {workout.scheduled_date} "
                    f"(athlete {athlete_id}) and no fallback pace found in recent workouts. "
                    f"Set target_pace_min_per_km on the workout or complete at least one "
                    f"{workout.workout_type} session with a recorded pace."
                )

        date_iso = workout.scheduled_date.isoformat()

        # Primary: delete all app-created library entries for this date (marker-based).
        # This reliably removes stale duplicates regardless of whether stored IDs are current.
        for old_wid in app_by_date.get(date_iso, []):
            if old_wid in schedule_by_wid:
                try:
                    remove_workout_schedule(garmin, schedule_by_wid[old_wid])
                    logger.info(
                        "Removed schedule %s for stale workout %s on %s (athlete %d)",
                        schedule_by_wid[old_wid], old_wid, date_iso, athlete_id,
                    )
                except Exception as e:
                    logger.warning("Failed to remove schedule for wid %s: %s", old_wid, e)
            try:
                delete_workout(garmin, old_wid)
                logger.info("Deleted stale app workout %s on %s (athlete %d)", old_wid, date_iso, athlete_id)
            except Exception as e:
                logger.warning("Failed to delete stale workout %s: %s", old_wid, e)

        # Fallback: also try stored IDs in case this workout predates the marker system.
        # 404s are expected if the marker scan already deleted them — those are fine.
        if workout.garmin_schedule_id:
            try:
                remove_workout_schedule(garmin, int(workout.garmin_schedule_id))
            except Exception as e:
                if "404" not in str(e):
                    logger.warning("Failed to remove schedule %s: %s", workout.garmin_schedule_id, e)
        if workout.garmin_workout_id:
            try:
                delete_workout(garmin, int(workout.garmin_workout_id))
            except Exception as e:
                if "404" not in str(e):
                    logger.warning("Failed to delete old Garmin workout %s: %s", workout.garmin_workout_id, e)

        # Build and upload new workout
        workout_json = build_workout_json(workout)
        workout_id = None

        # Step 1: upload to workout library
        try:
            import json as _json
            logger.debug("Uploading workout JSON for %s: %s",
                         workout.scheduled_date, _json.dumps(workout_json, indent=2))
            result = upload_workout(garmin, workout_json)
            logger.debug("Garmin upload response for %s: %s", workout.scheduled_date, result)
            workout_id = result.get("workoutId") if isinstance(result, dict) else None
            if not workout_id:
                logger.error(
                    "Upload returned no workoutId for %s (athlete %d). Response: %s",
                    workout.scheduled_date, athlete_id, result,
                )
                failed += 1
                failed_dates.append(workout.scheduled_date)
                continue
        except Exception as e:
            # Try to extract Garmin's error response body for diagnosis
            resp_body = ""
            if hasattr(e, "response") and e.response is not None:
                try:
                    resp_body = e.response.text
                except Exception:
                    pass
            elif hasattr(e, "__context__") and hasattr(e.__context__, "response"):
                try:
                    resp_body = e.__context__.response.text
                except Exception:
                    pass
            logger.error("Upload failed for %s (athlete %d): %s | Response body: %s",
                         workout.scheduled_date, athlete_id, e, resp_body or "(unavailable)")
            workout.garmin_workout_id = None
            failed += 1
            failed_dates.append(workout.scheduled_date)
            continue

        # Step 2: schedule on calendar (separate try so we can tell which step failed)
        workout.garmin_workout_id = str(workout_id)
        try:
            sched_result = schedule_workout(garmin, workout_id, workout.scheduled_date.isoformat())
            logger.debug("Garmin schedule response for %s: %s", workout.scheduled_date, sched_result)
            # Garmin returns "workoutScheduleId" — "scheduleId" is a fallback for older API versions
            schedule_id = None
            if isinstance(sched_result, dict):
                schedule_id = (
                    sched_result.get("workoutScheduleId")
                    or sched_result.get("scheduleId")
                )
            workout.garmin_schedule_id = str(schedule_id) if schedule_id else None
            workout.last_garmin_synced_at = datetime.utcnow()
            logger.info(
                "Uploaded + scheduled %s to Garmin (workout_id=%s, schedule_id=%s) for athlete %d",
                workout.scheduled_date, workout_id, schedule_id or "unknown", athlete_id,
            )
            uploaded += 1
        except Exception as e:
            logger.error(
                "Schedule failed for %s (workout_id=%s, athlete %d): %s — "
                "deleting orphaned library entry and marking unsynced for reconciliation.",
                workout.scheduled_date, workout_id, athlete_id, e,
            )
            try:
                delete_workout(garmin, workout_id)
                logger.info("Deleted orphaned library entry %s for athlete %d", workout_id, athlete_id)
            except Exception as del_e:
                logger.warning("Could not delete orphaned workout %s: %s", workout_id, del_e)
            workout.garmin_workout_id = None
            workout.garmin_schedule_id = None
            failed += 1
            failed_dates.append(workout.scheduled_date)

    db_session.commit()
    return uploaded, failed, failed_dates


def sync_day_to_garmin(
    athlete_id: int,
    email: str,
    encrypted_password: bytes,
    target_date: date,
    db_session: Session,
) -> bool:
    """Upload or update a single day's planned workout on Garmin Connect.

    If the workout already has a garmin_workout_id, attempts an in-place PUT
    update (preserving the calendar schedule entry). Falls back to delete +
    create if the update fails or the ID is stale. Skips dates in the past.

    Returns True if all workouts were successfully synced (or no sync needed).
    """
    today = date.today()
    if target_date < today:
        logger.debug("Skipping past-date day sync for %s", target_date)
        return True

    workouts = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete_id,
            PlannedWorkout.scheduled_date == target_date,
            PlannedWorkout.status.in_(["planned", "modified"]),
            PlannedWorkout.workout_type != "rest",
        )
        .all()
    )

    if not workouts:
        logger.debug("No workouts to sync for athlete %d on %s", athlete_id, target_date)
        return True

    # Fallback paces from recent history (same logic as sync_week_to_garmin)
    fallback_paces: dict[str, float] = {}
    for wt in ("easy", "long_run", "tempo", "strides", "intervals"):
        sample = (
            db_session.query(PlannedWorkout.target_pace_min_per_km)
            .filter(
                PlannedWorkout.athlete_id == athlete_id,
                PlannedWorkout.workout_type == wt,
                PlannedWorkout.target_pace_min_per_km.isnot(None),
            )
            .order_by(PlannedWorkout.scheduled_date.desc())
            .limit(1)
            .scalar()
        )
        if sample:
            fallback_paces[wt] = sample

    garmin = get_garmin_client(athlete_id, email, encrypted_password, db_session)
    date_iso = target_date.isoformat()
    all_success = True

    for workout in workouts:
        if workout.target_pace_min_per_km is None:
            if workout.workout_type in fallback_paces:
                workout.target_pace_min_per_km = fallback_paces[workout.workout_type]
                logger.info(
                    "Workout %s (%s) missing pace — using fallback %.2f min/km",
                    workout.scheduled_date, workout.workout_type, workout.target_pace_min_per_km,
                )
            else:
                logger.error(
                    "No pace for %s on %s (athlete %d) and no fallback found.",
                    workout.workout_type, workout.scheduled_date, athlete_id,
                )
                all_success = False
                continue

        workout_json = build_workout_json(workout)

        # Attempt in-place update if a Garmin ID exists — avoids touching the schedule.
        if workout.garmin_workout_id:
            try:
                update_workout(garmin, int(workout.garmin_workout_id), workout_json)
                logger.info(
                    "Updated Garmin workout %s in-place for athlete %d on %s",
                    workout.garmin_workout_id, athlete_id, target_date,
                )
                continue  # Schedule unchanged — skip delete+create
            except Exception as e:
                logger.warning(
                    "In-place update failed for workout %s (athlete %d on %s): %s — "
                    "falling back to delete+create",
                    workout.garmin_workout_id, athlete_id, target_date, e,
                )

        # Delete+create path: scan library for stale marked entries for this date.
        library = get_garmin_workout_library(garmin)
        app_wids: list[int] = []
        for entry in library:
            m = APP_MARKER_RE.search(entry.get("description") or "")
            if m and int(m.group(1)) == athlete_id and m.group(2) == date_iso:
                app_wids.append(int(entry["workoutId"]))

        # Build schedule ID map from DB (calendar date-range API is unreliable).
        sched_by_wid: dict[int, int] = {}
        if workout.garmin_workout_id and workout.garmin_schedule_id:
            try:
                sched_by_wid[int(workout.garmin_workout_id)] = int(workout.garmin_schedule_id)
            except (ValueError, TypeError):
                pass

        for old_wid in app_wids:
            if old_wid in sched_by_wid:
                try:
                    remove_workout_schedule(garmin, sched_by_wid[old_wid])
                except Exception as e:
                    logger.warning("Failed to remove schedule for wid %s: %s", old_wid, e)
            try:
                delete_workout(garmin, old_wid)
            except Exception as e:
                logger.warning("Failed to delete stale workout %s: %s", old_wid, e)

        # Fallback: also try stored IDs (pre-marker workouts)
        if workout.garmin_schedule_id:
            try:
                remove_workout_schedule(garmin, int(workout.garmin_schedule_id))
            except Exception as e:
                if "404" not in str(e):
                    logger.warning("Failed to remove schedule %s: %s", workout.garmin_schedule_id, e)
        if workout.garmin_workout_id:
            try:
                delete_workout(garmin, int(workout.garmin_workout_id))
            except Exception as e:
                if "404" not in str(e):
                    logger.warning("Failed to delete workout %s: %s", workout.garmin_workout_id, e)

        # Upload and schedule fresh
        try:
            import json as _json
            logger.debug("Uploading workout JSON for %s: %s",
                         target_date, _json.dumps(workout_json, indent=2))
            result = upload_workout(garmin, workout_json)
            workout_id = result.get("workoutId") if isinstance(result, dict) else None
            if not workout_id:
                logger.error(
                    "Upload returned no workoutId for %s (athlete %d).",
                    target_date, athlete_id,
                )
                all_success = False
                continue
            workout.garmin_workout_id = str(workout_id)
            sched_result = schedule_workout(garmin, workout_id, date_iso)
            schedule_id = None
            if isinstance(sched_result, dict):
                schedule_id = sched_result.get("workoutScheduleId") or sched_result.get("scheduleId")
            workout.garmin_schedule_id = str(schedule_id) if schedule_id else None
            workout.last_garmin_synced_at = datetime.utcnow()
            logger.info(
                "Uploaded + scheduled %s to Garmin (workout_id=%s, schedule_id=%s) for athlete %d",
                target_date, workout_id, schedule_id or "unknown", athlete_id,
            )
        except Exception as e:
            logger.error(
                "Day sync schedule failed for athlete %d on %s: %s — "
                "deleting orphaned library entry and marking unsynced for reconciliation.",
                athlete_id, target_date, e,
            )
            if workout.garmin_workout_id:
                try:
                    delete_workout(garmin, int(workout.garmin_workout_id))
                    logger.info("Deleted orphaned library entry %s for athlete %d", workout.garmin_workout_id, athlete_id)
                except Exception as del_e:
                    logger.warning("Could not delete orphaned workout %s: %s", workout.garmin_workout_id, del_e)
            workout.garmin_workout_id = None
            workout.garmin_schedule_id = None
            all_success = False

    db_session.commit()
    return all_success
