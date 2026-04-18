"""Normalise raw Garmin API responses into database model fields."""

import logging
from datetime import date

from sqlalchemy.orm import Session

from running_coach_ai.database.models import CompletedWorkout, HealthSnapshot, PlannedWorkout

logger = logging.getLogger(__name__)


def _safe_get(obj, *keys, default=None):
    """Safely traverse nested dicts/lists."""
    for key in keys:
        if obj is None:
            return default
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and isinstance(key, int):
            obj = obj[key] if key < len(obj) else None
        else:
            return default
    return obj if obj is not None else default


def parse_health_snapshot(raw: dict, athlete_id: int, snapshot_date: date, db_session: Session) -> HealthSnapshot:
    """Map raw Garmin health API responses to HealthSnapshot fields.

    Upserts on (athlete_id, date) — safe to re-run if the job fires twice.
    """
    # --- Sleep ---
    sleep_score = None
    sleep_duration = None
    if raw.get("sleep"):
        dto = _safe_get(raw["sleep"], "dailySleepDTO")
        if dto:
            # Try all known Garmin API variants for sleep score (field name changes across firmware)
            # Modern firmware (2024+): dailySleepDTO.sleepScores.totalScore
            sleep_score = (
                _safe_get(dto, "sleepScores", "totalScore")
                or _safe_get(dto, "sleepScores", "overall", "value")
                or _safe_get(dto, "overallSleepScore", "value")
                or _safe_get(dto, "overallSleepScore")
                or _safe_get(dto, "sleepScore")
                or _safe_get(raw["sleep"], "sleepScore")
            )
            sleep_duration = _safe_get(dto, "sleepTimeSeconds")
        if sleep_score is None:
            logger.warning(
                "Sleep score not found for athlete %d — dto keys: %s, raw sleep keys: %s",
                athlete_id,
                list(dto.keys()) if dto else "no dto",
                list(raw["sleep"].keys()) if isinstance(raw.get("sleep"), dict) else "n/a",
            )
        logger.debug(
            "Sleep parsed for athlete %d: score=%s duration=%s (dto keys: %s)",
            athlete_id, sleep_score, sleep_duration,
            list(dto.keys()) if dto else "no dto",
        )

    # --- HRV ---
    hrv_score = None
    hrv_status = None
    if raw.get("hrv"):
        summary = _safe_get(raw["hrv"], "hrvSummary")
        if summary:
            # Garmin uses "lastNightAvg" in current API responses (was "lastNight" historically)
            hrv_score = _safe_get(summary, "lastNightAvg") or _safe_get(summary, "lastNight")
            hrv_status = _safe_get(summary, "status")
        logger.debug(
            "HRV parsed for athlete %d: score=%s status=%s (summary keys: %s)",
            athlete_id, hrv_score, hrv_status,
            list(summary.keys()) if summary else "no summary",
        )

    # --- Resting HR ---
    # get_rhr_day() returns allMetrics.metricsMap.WELLNESS_RESTING_HEART_RATE[0].value
    # Older/alternative paths kept as fallbacks.
    resting_hr = None
    if raw.get("rhr"):
        metrics_map = _safe_get(raw["rhr"], "allMetrics", "metricsMap") or {}
        rhr_list = metrics_map.get("WELLNESS_RESTING_HEART_RATE") or []
        resting_hr = (
            _safe_get(rhr_list, 0, "value")
            or _safe_get(raw["rhr"], "restingHeartRate")
            or _safe_get(raw["rhr"], "allDayHR", "restingHeartRate")
        )
        logger.debug(
            "RHR parsed for athlete %d: rhr=%s (top-level keys: %s)",
            athlete_id, resting_hr,
            list(raw["rhr"].keys()) if isinstance(raw["rhr"], dict) else type(raw["rhr"]).__name__,
        )

    # --- Body battery ---
    # bodyBatteryValuesArray contains [timestamp_ms, battery_level] pairs throughout the day.
    # Peak value = wakeup battery (after overnight recovery).
    # Last value = current/end-of-day battery level.
    # bodyBatteryStatList is empty in current API responses; use valuesArray instead.
    body_battery_start = None
    body_battery_end = None
    if raw.get("body_battery") and isinstance(raw["body_battery"], list) and raw["body_battery"]:
        bb_entry = raw["body_battery"][0]
        vals_array = bb_entry.get("bodyBatteryValuesArray") or []
        levels: list = []
        if vals_array:
            levels = [
                v[1] for v in vals_array
                if isinstance(v, (list, tuple)) and len(v) >= 2 and v[1] is not None
            ]
            if levels:
                body_battery_start = max(levels)   # peak = after overnight recovery
                body_battery_end = levels[-1]      # last = current/end-of-day value
        if body_battery_start is None:
            body_battery_start = _safe_get(bb_entry, "charged")
        logger.debug(
            "Body battery for athlete %d: start(peak)=%s end=%s "
            "(value entries: %d, first_5=%s, last_5=%s)",
            athlete_id, body_battery_start, body_battery_end, len(vals_array),
            levels[:5], levels[-5:],
        )

    # --- Stress ---
    stress_avg = None
    if raw.get("stress"):
        stress_avg = (
            _safe_get(raw["stress"], "avgStressLevel")
            or _safe_get(raw["stress"], "overallStressLevel")
        )
        logger.debug("Stress parsed for athlete %d: avg=%s", athlete_id, stress_avg)

    # --- Steps ---
    steps = None
    if raw.get("steps"):
        steps_data = raw["steps"]
        if isinstance(steps_data, list):
            # Each entry is a 15-min interval with a "steps" count
            total = sum(
                entry.get("steps", 0)
                for entry in steps_data
                if isinstance(entry, dict)
            )
            steps = total if total > 0 else None
        elif isinstance(steps_data, dict):
            steps = steps_data.get("totalSteps") or steps_data.get("steps")
        logger.debug(
            "Steps parsed for athlete %d: total=%s (response type: %s)",
            athlete_id, steps, type(steps_data).__name__,
        )

    # --- SpO2 ---
    spo2_avg = None
    if raw.get("spo2"):
        spo2_data = raw["spo2"]
        if isinstance(spo2_data, dict):
            spo2_avg = spo2_data.get("averageSpO2") or spo2_data.get("avgSpo2")
        elif isinstance(spo2_data, list) and spo2_data:
            readings = [
                r.get("spO2Reading") or r.get("reading")
                for r in spo2_data
                if isinstance(r, dict)
            ]
            readings = [r for r in readings if r is not None]
            if readings:
                spo2_avg = round(sum(readings) / len(readings), 1)

    # --- Training readiness ---
    training_readiness = None
    if raw.get("training_readiness"):
        tr_data = raw["training_readiness"]
        # Garmin API returns a list of daily records; unwrap to the first entry.
        if isinstance(tr_data, list) and tr_data:
            tr_data = tr_data[0]
        if isinstance(tr_data, dict):
            training_readiness = (
                tr_data.get("trainingReadinessScore")
                or tr_data.get("score")
                or tr_data.get("value")
            )
            if training_readiness is not None:
                training_readiness = int(training_readiness)
        logger.debug("Training readiness parsed for athlete %d: %s", athlete_id, training_readiness)
        if training_readiness is None:
            logger.warning(
                "Training readiness score not found for athlete %d — keys: %s",
                athlete_id,
                list(tr_data.keys()) if isinstance(tr_data, dict) else type(tr_data).__name__,
            )

    # --- Log summary of what was parsed ---
    logger.info(
        "HealthSnapshot athlete %d %s — HRV: %s (%s), sleep: %s score/%s s, "
        "RHR: %s, BB: %s→%s, stress: %s, steps: %s, SpO2: %s, TR: %s",
        athlete_id, snapshot_date,
        hrv_score, hrv_status, sleep_score, sleep_duration,
        resting_hr, body_battery_start, body_battery_end,
        stress_avg, steps, spo2_avg, training_readiness,
    )

    # --- Upsert on (athlete_id, date) ---
    # For existing records, merge: only overwrite a field when the new value is
    # not None.  This preserves best-known data across multiple fetch attempts
    # (e.g. first fetch gets HRV+sleep, second gets body_battery — both survive).
    fields = {
        "hrv_score": hrv_score,
        "hrv_status": hrv_status,
        "resting_hr": resting_hr,
        "sleep_score": sleep_score,
        "sleep_duration_seconds": sleep_duration,
        "body_battery_start": body_battery_start,
        "body_battery_end": body_battery_end,
        "stress_avg": stress_avg,
        "steps": steps,
        "spo2_avg": spo2_avg,
        "training_readiness": training_readiness,
    }

    existing = (
        db_session.query(HealthSnapshot)
        .filter(
            HealthSnapshot.athlete_id == athlete_id,
            HealthSnapshot.date == snapshot_date,
        )
        .first()
    )

    if existing:
        snapshot = existing
        for attr, val in fields.items():
            if val is not None:
                setattr(snapshot, attr, val)
    else:
        snapshot = HealthSnapshot(athlete_id=athlete_id, date=snapshot_date)
        for attr, val in fields.items():
            setattr(snapshot, attr, val)
        db_session.add(snapshot)

    db_session.commit()
    return snapshot


def parse_activity_list_entry(activity: dict, athlete_id: int, db_session: Session) -> "CompletedWorkout | None":
    """Map a Garmin activity list entry (from get_activities_by_date) to a CompletedWorkout.

    Used for bulk historical import. Returns None if the activity already exists.
    """
    garmin_activity_id = str(activity.get("activityId", ""))
    if not garmin_activity_id:
        return None

    existing = (
        db_session.query(CompletedWorkout)
        .filter(
            CompletedWorkout.athlete_id == athlete_id,
            CompletedWorkout.garmin_activity_id == garmin_activity_id,
        )
        .first()
    )
    if existing:
        return None

    def _pace(speed_mps):
        if speed_mps and speed_mps > 0:
            return round((1000 / speed_mps) / 60, 2)
        return None

    start_time_str = activity.get("startTimeLocal") or activity.get("startTimeGMT") or ""
    try:
        activity_date = date.fromisoformat(start_time_str[:10])
    except (ValueError, AttributeError):
        activity_date = date.today()

    distance_m = activity.get("distance") or 0
    avg_speed = activity.get("averageSpeed")
    max_speed = activity.get("maxSpeed")

    def _int_or_none(val):
        try:
            v = int(val)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    # Activity type: typeKey from the activityType dict (e.g. "running", "hiking", "tennis")
    activity_type_obj = activity.get("activityType") or {}
    activity_type = (
        activity_type_obj.get("typeKey")
        if isinstance(activity_type_obj, dict)
        else str(activity_type_obj)
    ) or "unknown"

    workout = CompletedWorkout(
        athlete_id=athlete_id,
        garmin_activity_id=garmin_activity_id,
        activity_type=activity_type,
        date=activity_date,
        distance_km=round(distance_m / 1000, 2) if distance_m else None,
        duration_seconds=_int_or_none(activity.get("duration")),
        avg_hr=_int_or_none(activity.get("averageHR")),
        max_hr=_int_or_none(activity.get("maxHR")),
        avg_pace_min_per_km=_pace(avg_speed),
        max_pace_min_per_km=_pace(max_speed),
        avg_cadence_spm=_int_or_none(activity.get("averageRunningCadenceInStepsPerMinute")),
        max_cadence_spm=_int_or_none(activity.get("maxRunningCadenceInStepsPerMinute")),
        elevation_gain_m=activity.get("elevationGain"),
        training_load=activity.get("activityTrainingLoad"),
        aerobic_training_effect=activity.get("aerobicTrainingEffect"),
        anaerobic_training_effect=activity.get("anaerobicTrainingEffect"),
        vo2max_estimate=activity.get("vO2MaxValue"),
        calories=_int_or_none(activity.get("calories")),
        feedback_given=True,  # historical — no feedback needed
    )
    db_session.add(workout)
    return workout


def extract_activity_start_coords(detail: dict) -> tuple[float, float] | None:
    """Extract the GPS starting coordinates from a get_activity() or get_activity_details() response.

    Priority:
      1. summaryDTO.startLatitude / startLongitude  (get_activity() format)
      2. activityDetail.activity.summaryDTO.startLatitude / startLongitude (legacy)
      3. activityDetail.activity.lapDTOs[0].startLatitude / startLongitude (legacy fallback)

    Returns (lat, lon) as floats, or None when no valid GPS data is present
    (e.g., treadmill runs where coordinates are null or exactly 0.0/0.0).
    """
    # get_activity() format: summaryDTO at top level
    summary_dto = detail.get("summaryDTO") or {}
    if not summary_dto:
        # Legacy: activityDetail.activity.summaryDTO
        activity = detail.get("activityDetail", {}).get("activity", {})
        summary_dto = activity.get("summaryDTO") or {}

    lat = summary_dto.get("startLatitude")
    lon = summary_dto.get("startLongitude")

    # Fallback: first lap (legacy format)
    if not lat or not lon:
        activity = detail.get("activityDetail", {}).get("activity", detail)
        laps = activity.get("lapDTOs") or []
        if laps:
            lat = laps[0].get("startLatitude")
            lon = laps[0].get("startLongitude")

    if lat is None or lon is None:
        return None

    lat, lon = float(lat), float(lon)

    # Reject zero coordinates (treadmill / unrecorded GPS).
    # A real activity at exactly 0°N 0°E is accepted (Gulf of Guinea area).
    if abs(lat) <= 0.001 and abs(lon) <= 0.001:
        return None

    return lat, lon


def parse_activity_summary(detail: dict, athlete_id: int, db_session: Session) -> CompletedWorkout:
    """Map get_activity() response to CompletedWorkout fields.

    Expects the response from garmin.get_activity() where summary metrics live
    in summaryDTO and activityId is at the top level.
    Converts units: metres → km, m/s → min/km pace.
    """
    # get_activity() format: activityId at top level, metrics in summaryDTO
    # Legacy fallback: activityDetail.activity (flat fields)
    if "summaryDTO" in detail:
        summary = detail["summaryDTO"]
        garmin_activity_id = str(detail.get("activityId", ""))
    else:
        activity = detail.get("activityDetail", {}).get("activity", detail)
        summary = activity
        garmin_activity_id = str(activity.get("activityId", ""))

    def _pace(speed_mps):
        """Convert speed in m/s to pace in min/km."""
        if speed_mps and speed_mps > 0:
            return round((1000 / speed_mps) / 60, 2)
        return None

    def _int_or_none(val):
        try:
            v = int(val)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    distance_m = summary.get("distance") or 0
    avg_speed = summary.get("averageSpeed")
    max_speed = summary.get("maxSpeed")

    # Parse date from startTimeLocal; fall back to today
    start_time_str = summary.get("startTimeLocal", "")
    try:
        activity_date = date.fromisoformat(start_time_str[:10])
    except (ValueError, AttributeError):
        activity_date = date.today()

    # Activity type: from top-level activityType dict (get_activity format) or activityTypeDTO
    activity_type_obj = (
        detail.get("activityType")
        or detail.get("activityTypeDTO")
        or {}
    )
    activity_type = (
        activity_type_obj.get("typeKey")
        if isinstance(activity_type_obj, dict)
        else str(activity_type_obj)
    ) or "unknown"

    workout = CompletedWorkout(
        athlete_id=athlete_id,
        garmin_activity_id=garmin_activity_id,
        activity_type=activity_type,
        date=activity_date,
        distance_km=round(distance_m / 1000, 2) if distance_m else None,
        duration_seconds=_int_or_none(summary.get("duration")),
        avg_hr=_int_or_none(summary.get("averageHR")),
        max_hr=_int_or_none(summary.get("maxHR")),
        avg_pace_min_per_km=_pace(avg_speed),
        max_pace_min_per_km=_pace(max_speed),
        # summaryDTO uses averageRunCadence; legacy used averageRunningCadenceInStepsPerMinute
        avg_cadence_spm=_int_or_none(
            summary.get("averageRunCadence") or summary.get("averageRunningCadenceInStepsPerMinute")
        ),
        max_cadence_spm=_int_or_none(
            summary.get("maxRunCadence") or summary.get("maxRunningCadenceInStepsPerMinute")
        ),
        avg_stride_length_m=summary.get("strideLength") or summary.get("avgStrideLength"),
        avg_ground_contact_time_ms=summary.get("groundContactTime") or summary.get("avgGroundContactTime"),
        avg_vertical_oscillation_cm=summary.get("verticalOscillation") or summary.get("avgVerticalOscillation"),
        avg_vertical_ratio_pct=summary.get("verticalRatio") or summary.get("avgVerticalRatio"),
        avg_power_w=summary.get("averagePower") or summary.get("avgPower"),
        max_power_w=summary.get("maxPower"),
        elevation_gain_m=summary.get("elevationGain"),
        training_load=summary.get("activityTrainingLoad"),
        # summaryDTO uses trainingEffect for aerobic; anaerobicTrainingEffect for anaerobic
        aerobic_training_effect=summary.get("trainingEffect") or summary.get("aerobicTrainingEffect"),
        anaerobic_training_effect=summary.get("anaerobicTrainingEffect"),
        vo2max_estimate=summary.get("vO2MaxValue"),
        calories=_int_or_none(summary.get("calories")),
        feedback_given=False,
    )
    db_session.add(workout)

    # Link to the PlannedWorkout for this athlete on this date, if one exists and
    # hasn't already been claimed by another CompletedWorkout.
    planned = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete_id,
            PlannedWorkout.scheduled_date == activity_date,
            PlannedWorkout.status != "cancelled",
        )
        .outerjoin(CompletedWorkout, CompletedWorkout.planned_workout_id == PlannedWorkout.id)
        .filter(CompletedWorkout.id.is_(None))
        .first()
    )
    if planned:
        workout.planned_workout_id = planned.id
        logger.info(
            "Linked CompletedWorkout to PlannedWorkout id=%d (%s) for athlete %d on %s",
            planned.id, planned.workout_type, athlete_id, activity_date,
        )

    db_session.flush()  # get ID before returning
    logger.info("Persisted CompletedWorkout (garmin_id=%s) for athlete %d", garmin_activity_id, athlete_id)
    return workout
