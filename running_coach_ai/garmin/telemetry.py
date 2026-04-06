"""Extract and store time-series telemetry streams and lap splits."""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from running_coach_ai.database.models import WorkoutTelemetry

logger = logging.getLogger(__name__)

# Mapping from Garmin metricsKey → WorkoutTelemetry column name
STREAM_KEY_MAP = {
    "directHeartRate": "heart_rate_json",
    "directSpeed": "pace_json",
    "directRunCadence": "cadence_json",
    "directStrideLength": "stride_length_json",
    "directGroundContactTime": "ground_contact_time_json",
    "directVerticalOscillation": "vertical_oscillation_json",
    "directVerticalRatio": "vertical_ratio_json",
    "directPower": "power_json",
    "directElevation": "elevation_json",
    "directAirTemperature": "air_temperature_json",
    "directRespirationRate": "respiration_rate_json",
    "directPerformanceCondition": "performance_condition_json",
}


def _speed_to_pace(speed_mps: float | None) -> float | None:
    """Convert speed in m/s to pace in min/km."""
    if speed_mps and speed_mps > 0:
        return round((1000 / speed_mps) / 60, 3)
    return None


def extract_telemetry(
    detail: dict,
    completed_workout_id: int,
    athlete_id: int,
    db_session: Session,
) -> WorkoutTelemetry:
    """Extract time-series telemetry from get_activity_details() response.

    Builds a metricsKey → column_index map from metricDescriptors, then
    extracts each recognised stream as a JSON array. Missing streams stored
    as NULL. Records which keys were present in telemetry_channels_json.
    """
    metric_descriptors = detail.get("metricDescriptors", [])
    metric_samples = detail.get("activityDetailMetrics", [])

    # Build index map: key → column index
    # Garmin API uses 'key' and 'metricsIndex' (not 'metricsKey'/'index')
    key_to_index: dict[str, int] = {}
    for descriptor in metric_descriptors:
        key = descriptor.get("key") or descriptor.get("metricsKey")
        index = descriptor.get("metricsIndex") if descriptor.get("metricsIndex") is not None else descriptor.get("index")
        if key is not None and index is not None:
            key_to_index[key] = index

    # Extract each stream
    streams: dict[str, list | None] = {col: None for col in STREAM_KEY_MAP.values()}
    present_keys: list[str] = []

    for garmin_key, column_name in STREAM_KEY_MAP.items():
        if garmin_key not in key_to_index:
            continue

        col_idx = key_to_index[garmin_key]
        values = []

        for sample in metric_samples:
            metrics = sample.get("metrics", [])
            val = metrics[col_idx] if col_idx < len(metrics) else None

            # Convert speed → pace for directSpeed
            if garmin_key == "directSpeed":
                val = _speed_to_pace(val)

            values.append(val)

        streams[column_name] = values
        present_keys.append(garmin_key)

    telemetry = WorkoutTelemetry(
        athlete_id=athlete_id,
        completed_workout_id=completed_workout_id,
        sample_interval_seconds=1,
        heart_rate_json=streams["heart_rate_json"],
        pace_json=streams["pace_json"],
        cadence_json=streams["cadence_json"],
        stride_length_json=streams["stride_length_json"],
        ground_contact_time_json=streams["ground_contact_time_json"],
        vertical_oscillation_json=streams["vertical_oscillation_json"],
        vertical_ratio_json=streams["vertical_ratio_json"],
        power_json=streams["power_json"],
        elevation_json=streams["elevation_json"],
        air_temperature_json=streams["air_temperature_json"],
        respiration_rate_json=streams["respiration_rate_json"],
        performance_condition_json=streams["performance_condition_json"],
        recorded_at=datetime.utcnow(),
    )
    db_session.add(telemetry)
    db_session.flush()

    # Update the CompletedWorkout's telemetry_channels_json
    from running_coach_ai.database.models import CompletedWorkout
    cw = db_session.query(CompletedWorkout).get(completed_workout_id)
    if cw:
        cw.telemetry_channels_json = present_keys

    logger.info(
        "Extracted telemetry for workout %d: %d streams present",
        completed_workout_id, len(present_keys),
    )
    return telemetry


def ingest_lap_splits(
    garmin,
    athlete_id: int,
    activity_id: str,
    telemetry: WorkoutTelemetry,
    db_session: Session,
) -> None:
    """Fetch lap splits and store as laps_json on the WorkoutTelemetry row."""
    try:
        splits_data = garmin.get_activity_splits(activity_id)
    except Exception as e:
        logger.warning("Could not fetch splits for activity %s: %s", activity_id, e)
        return

    laps = []
    lap_list = splits_data if isinstance(splits_data, list) else splits_data.get("lapDTOs", [])

    for i, lap in enumerate(lap_list, start=1):
        def _pace(speed):
            if speed and speed > 0:
                return round((1000 / speed) / 60, 2)
            return None

        laps.append({
            "lap_number": i,
            "distance_m": lap.get("distance"),
            "duration_seconds": lap.get("duration"),
            "avg_hr": lap.get("averageHR"),
            "max_hr": lap.get("maxHR"),
            "avg_pace_min_per_km": _pace(lap.get("averageSpeed")),
            "avg_cadence_spm": lap.get("averageRunningCadenceInStepsPerMinute"),
            "avg_ground_contact_time_ms": lap.get("avgGroundContactTime"),
            "avg_vertical_oscillation_cm": lap.get("avgVerticalOscillation"),
            "avg_power_w": lap.get("avgPower"),
        })

    telemetry.laps_json = laps
    db_session.commit()
    logger.info("Stored %d lap splits for telemetry %d", len(laps), telemetry.id)
