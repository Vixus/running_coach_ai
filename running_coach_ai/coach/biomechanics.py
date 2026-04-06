"""Biomechanical analysis of workout telemetry and RunningProfile updates."""

import logging
import statistics
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from running_coach_ai.database.models import CompletedWorkout, RunningProfile, WorkoutTelemetry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _valid(values: list) -> list:
    """Filter None and 0-equivalent sentinel values."""
    return [v for v in (values or []) if v is not None]


def _quarter_avg(values: list, quarter: int) -> float | None:
    """Return the mean of a quarter of a list (1=first, 4=last)."""
    if not values:
        return None
    n = len(values)
    size = max(1, n // 4)
    if quarter == 1:
        segment = values[:size]
    elif quarter == 4:
        segment = values[n - size:]
    else:
        start = (quarter - 1) * size
        segment = values[start: start + size]
    valid = [v for v in segment if v is not None]
    return statistics.mean(valid) if valid else None


def _half_avg(values: list, half: int) -> float | None:
    """Return the mean of the first or second half of a list."""
    if not values:
        return None
    mid = len(values) // 2
    segment = values[:mid] if half == 1 else values[mid:]
    valid = [v for v in segment if v is not None]
    return statistics.mean(valid) if valid else None


def _decile_avgs(values: list) -> list[float | None]:
    """Split a list into 10 equal-length time segments and return the mean of each."""
    if not values:
        return [None] * 10
    n = len(values)
    result = []
    for i in range(10):
        start = i * n // 10
        end = (i + 1) * n // 10
        segment = [v for v in values[start:end] if v is not None]
        result.append(round(statistics.mean(segment), 2) if segment else None)
    return result


def _pct_change(a: float | None, b: float | None) -> float | None:
    """Percentage change from a to b. Positive = b is larger."""
    if a and b and a != 0:
        return round((b - a) / a * 100, 1)
    return None


def _smooth_hr(values: list, duration_seconds: int | None, target_window_seconds: int = 30) -> list:
    """Apply a centered rolling average to a HR series to reduce zone-crossing noise.

    Garmin displays HR zones using smoothed data (~30 s window). Computing zones
    from raw per-second telemetry samples inflates Z4/Z5 because momentary spikes
    across the boundary are counted as separate zone entries. This function brings
    our zone computation in line with Garmin's display methodology without
    altering the raw data used for decile/quarter averages.
    """
    n = len(values)
    if n < 2 or not duration_seconds:
        return values
    # Infer samples per second from actual count vs duration
    samples_per_sec = n / duration_seconds
    half_win = max(1, round(target_window_seconds * samples_per_sec / 2))
    result = []
    for i in range(n):
        start = max(0, i - half_win)
        end = min(n, i + half_win + 1)
        segment = [v for v in values[start:end] if v is not None]
        result.append(round(sum(segment) / len(segment)) if segment else None)
    return result


def _fmt(val: float | None, decimals: int = 1) -> str:
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}"


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyse_workout(
    telemetry: WorkoutTelemetry,
    completed: CompletedWorkout,
    athlete_max_hr: int | None = None,
    max_hr_run_count: int = 0,
    garmin_hr_zones: list | None = None,
) -> dict:
    """Deep biomechanical analysis from per-sample telemetry streams.

    Returns a rich dict that ``generate_post_run_feedback`` formats for Claude.
    All quarter and decile comparisons use time-indexed position in the stream
    (first N samples = first quarter of run duration).

    cadence_json stores directRunCadence (single-leg, half of true SPM).
    All cadence values returned in this dict are ×2 (true SPM).

    athlete_max_hr: the athlete's true physiological max HR (e.g. from their
        Garmin profile or rolling historical maximum). When provided, zone
        thresholds are anchored to this rather than today's observed max,
        which may be lower if the session wasn't a maximal effort.
    """
    result: dict = {}

    hr = telemetry.heart_rate_json or []
    pace = telemetry.pace_json or []           # min/km, converted from m/s
    cadence_raw = telemetry.cadence_json or []  # single-leg — multiply ×2 for true SPM
    stride = telemetry.stride_length_json or [] # cm (Garmin) or m — check raw
    gct = telemetry.ground_contact_time_json or []
    vo = telemetry.vertical_oscillation_json or []
    vr = telemetry.vertical_ratio_json or []
    power = telemetry.power_json or []
    perf_cond = telemetry.performance_condition_json or []

    # Cadence in true SPM
    cadence = [v * 2 if v is not None else None for v in cadence_raw]

    # Stride length: Garmin stores in cm (divide by 100 for metres)
    stride_m = [v / 100 if v is not None else None for v in stride]

    # --- Establish max HR for zone thresholds ---
    # Prefer the athlete's configured/historical max HR over today's observed
    # max. A single non-maximal session will produce a lower observed max,
    # which shifts ALL zone thresholds down and makes the run look harder.
    max_hr = athlete_max_hr or completed.max_hr or 189

    # ── HR zone computation uses smoothed HR to match Garmin's display ───────
    # Raw per-second telemetry has boundary-crossing noise (momentary spikes
    # across a zone threshold inflate Z4/Z5). Garmin's app displays zones using
    # a ~30-second rolling average. We apply the same smoothing here so the
    # coach's zone percentages are comparable to what the athlete sees in Garmin.
    # Raw HR is preserved for decile/quarter/drift analysis below.
    hr_for_zones = _smooth_hr(hr, completed.duration_seconds, target_window_seconds=30)

    # ── HR zone thresholds (% of max HR) ────────────────────────────────────
    z_bounds = [0.60, 0.70, 0.80, 0.90, 1.0]
    z_thresh = [round(max_hr * b) for b in z_bounds]

    valid_hr_smooth = _valid(hr_for_zones)
    if valid_hr_smooth:
        total = len(valid_hr_smooth)
        z_counts = [0, 0, 0, 0, 0]
        for v in valid_hr_smooth:
            if v < z_thresh[0]:
                z_counts[0] += 1
            elif v < z_thresh[1]:
                z_counts[1] += 1
            elif v < z_thresh[2]:
                z_counts[2] += 1
            elif v < z_thresh[3]:
                z_counts[3] += 1
            else:
                z_counts[4] += 1
        for i in range(5):
            result[f"zone{i+1}_pct"] = round(z_counts[i] / total * 100, 1)
        result["zone2_threshold"] = z_thresh[1]   # upper edge of Z2
        result["zone4_threshold"] = z_thresh[3]   # upper edge of Z4
    else:
        for i in range(5):
            result[f"zone{i+1}_pct"] = None
        result["zone2_threshold"] = None
        result["zone4_threshold"] = None

    # ── Zone confidence ───────────────────────────────────────────────────────
    result["zone_max_hr"] = max_hr
    result["zone_max_hr_run_count"] = max_hr_run_count
    if max_hr_run_count >= 5:
        result["zone_confidence"] = "high"
    elif max_hr_run_count >= 3:
        result["zone_confidence"] = "moderate"
    else:
        result["zone_confidence"] = "low"

    # ── Prefer Garmin's pre-computed LTHR-based zones if available ────────────
    # Garmin computes zones using the athlete's configured LTHR (not % max HR),
    # which is more physiologically meaningful. When available these replace the
    # smoothed-HR computed zones above so values match exactly what the athlete
    # sees in the Garmin Connect app.
    if garmin_hr_zones:
        total_secs = sum(z.get("secsInZone", 0) for z in garmin_hr_zones) or 1
        zone_boundaries: dict[int, int] = {}   # zoneNumber → lower bpm boundary
        for z in garmin_hr_zones:
            znum = z.get("zoneNumber")
            secs = z.get("secsInZone", 0)
            lb = z.get("zoneLowBoundary")
            if znum and 1 <= znum <= 5:
                result[f"zone{znum}_pct"] = round(secs / total_secs * 100, 1)
                if lb is not None:
                    zone_boundaries[znum] = int(lb)
        # Expose boundary bpm for the feedback prompt
        result["garmin_zone_boundaries"] = zone_boundaries   # {1:100, 2:118, 3:140, 4:158, 5:177}
        result["zone_source"] = "garmin_lthr"
        result["zone_confidence"] = "high"   # Garmin data is authoritative
        # Recompute Z2 compliance using Garmin zone seconds directly (Z1+Z2 / total)
        z1_secs = next((z.get("secsInZone", 0) for z in garmin_hr_zones if z.get("zoneNumber") == 1), 0)
        z2_secs = next((z.get("secsInZone", 0) for z in garmin_hr_zones if z.get("zoneNumber") == 2), 0)
        result["zone_compliance_pct"] = round((z1_secs + z2_secs) / total_secs * 100, 1)
    else:
        result["garmin_zone_boundaries"] = {}
        result["zone_source"] = "computed_max_hr"

    # ── Legacy zone 2 compliance (fallback when no Garmin zones) ─────────────
    if not garmin_hr_zones:
        if valid_hr_smooth:
            z2_threshold = max_hr * 0.70
            in_z2 = sum(1 for v in valid_hr_smooth if v <= z2_threshold)
            result["zone_compliance_pct"] = round(in_z2 / len(valid_hr_smooth) * 100, 1)
        else:
            result["zone_compliance_pct"] = None

    # ── HR drift and aerobic decoupling ─────────────────────────────────────
    if len(hr) >= 8:
        q1_hr = _quarter_avg(hr, 1)
        q4_hr = _quarter_avg(hr, 4)
        if q1_hr and q4_hr and q1_hr > 0:
            result["hr_drift_pct"] = round((q4_hr - q1_hr) / q1_hr * 100, 1)
        else:
            result["hr_drift_pct"] = None
    else:
        result["hr_drift_pct"] = None

    result["aerobic_decoupling"] = None
    result["eco_drift_pct"] = None
    if hr and pace and len(hr) >= 4 and len(pace) >= 4:
        h1_hr = _half_avg(hr, 1)
        h2_hr = _half_avg(hr, 2)
        h1_pace = _half_avg(pace, 1)
        h2_pace = _half_avg(pace, 2)
        if all(v and v > 0 for v in [h1_hr, h2_hr, h1_pace, h2_pace]):
            ratio_1 = h1_hr / h1_pace
            ratio_2 = h2_hr / h2_pace
            result["aerobic_decoupling"] = round((ratio_2 - ratio_1) / ratio_1 * 100, 1)
            # Running economy drift uses same ratio but we label it separately for clarity
            result["eco_drift_pct"] = result["aerobic_decoupling"]

    # ── Cadence consistency (CV) ─────────────────────────────────────────────
    valid_cad = [v for v in cadence if v is not None and v > 0]
    if len(valid_cad) >= 4:
        mean_c = statistics.mean(valid_cad)
        stdev_c = statistics.stdev(valid_cad) if len(valid_cad) > 1 else 0
        result["cadence_cv"] = round(stdev_c / mean_c * 100, 1) if mean_c > 0 else None
    else:
        result["cadence_cv"] = None

    # ── Effort distribution ──────────────────────────────────────────────────
    valid_pace = [v for v in pace if v is not None and v > 0]
    result["effort_distribution"] = None
    if len(valid_pace) >= 4:
        h1p = _half_avg(pace, 1)
        h2p = _half_avg(pace, 2)
        if h1p and h2p:
            diff_pct = (h2p - h1p) / h1p * 100
            if diff_pct < -2:
                result["effort_distribution"] = "negative_split"
            elif diff_pct > 2:
                result["effort_distribution"] = "positive_split"
            else:
                result["effort_distribution"] = "even"

    # ── Decile progression tables ────────────────────────────────────────────
    result["hr_deciles"] = _decile_avgs(hr)
    result["pace_deciles"] = _decile_avgs(pace)
    result["cadence_deciles"] = _decile_avgs(cadence)
    result["power_deciles"] = _decile_avgs(power)

    # Running economy per decile: HR / (1/pace) = HR × pace (higher = worse)
    if hr and pace and len(hr) == len(pace):
        eco_series = [
            h * p if h is not None and p is not None else None
            for h, p in zip(hr, pace)
        ]
        result["eco_deciles"] = _decile_avgs(eco_series)
    else:
        result["eco_deciles"] = [None] * 10

    # ── Quarter-over-quarter fatigue indices ─────────────────────────────────
    # Cadence (true SPM)
    q1_cad = _quarter_avg(cadence, 1)
    q4_cad = _quarter_avg(cadence, 4)
    result["cadence_q1_spm"] = round(q1_cad, 0) if q1_cad else None
    result["cadence_q4_spm"] = round(q4_cad, 0) if q4_cad else None
    result["cadence_drop_pct"] = _pct_change(q1_cad, q4_cad)

    # Stride length (metres)
    q1_str = _quarter_avg(stride_m, 1)
    q4_str = _quarter_avg(stride_m, 4)
    result["stride_q1_m"] = round(q1_str, 3) if q1_str else None
    result["stride_q4_m"] = round(q4_str, 3) if q4_str else None
    result["stride_drop_pct"] = _pct_change(q1_str, q4_str)

    # Ground contact time (ms) — higher = worse
    q1_gct = _quarter_avg(gct, 1)
    q4_gct = _quarter_avg(gct, 4)
    result["gct_q1_ms"] = round(q1_gct, 0) if q1_gct else None
    result["gct_q4_ms"] = round(q4_gct, 0) if q4_gct else None
    result["gct_increase_pct"] = _pct_change(q1_gct, q4_gct)

    # Vertical ratio (%) — higher = more vertical bounce = worse
    q1_vr = _quarter_avg(vr, 1)
    q4_vr = _quarter_avg(vr, 4)
    result["vr_q1_pct"] = round(q1_vr, 2) if q1_vr else None
    result["vr_q4_pct"] = round(q4_vr, 2) if q4_vr else None
    result["vr_change_pct"] = _pct_change(q1_vr, q4_vr)

    # Vertical oscillation (cm) — higher = more wasted energy
    q1_vo = _quarter_avg(vo, 1)
    q4_vo = _quarter_avg(vo, 4)
    result["vo_q1_cm"] = round(q1_vo, 2) if q1_vo else None
    result["vo_q4_cm"] = round(q4_vo, 2) if q4_vo else None
    result["vo_change_pct"] = _pct_change(q1_vo, q4_vo)

    # Power (W)
    valid_power = [v for v in power if v is not None and v > 10]
    q1_pwr = _quarter_avg(valid_power, 1) if len(valid_power) >= 8 else None
    q4_pwr = _quarter_avg(valid_power, 4) if len(valid_power) >= 8 else None
    result["power_q1_w"] = round(q1_pwr, 0) if q1_pwr else None
    result["power_q4_w"] = round(q4_pwr, 0) if q4_pwr else None
    result["power_fade_pct"] = _pct_change(q1_pwr, q4_pwr)

    # ── Performance condition arc ────────────────────────────────────────────
    valid_pc = [(i, v) for i, v in enumerate(perf_cond) if v is not None]
    if valid_pc:
        pc_vals = [v for _, v in valid_pc]
        first_pc = pc_vals[0]
        last_pc = pc_vals[-1]
        peak_pc = max(pc_vals)
        peak_idx = next(i for i, v in valid_pc if v == peak_pc)
        peak_position = peak_idx / max(len(perf_cond) - 1, 1)  # 0–1

        result["perf_condition_start"] = round(first_pc, 1)
        result["perf_condition_peak"] = round(peak_pc, 1)
        result["perf_condition_end"] = round(last_pc, 1)

        if peak_position < 0.25:
            shape = "peaked_early"
        elif peak_position > 0.75:
            shape = "peaked_late"
        elif last_pc > first_pc + 1:
            shape = "rising"
        elif last_pc < first_pc - 1:
            shape = "falling"
        else:
            shape = "flat"
        result["perf_condition_shape"] = shape
    else:
        result["perf_condition_start"] = None
        result["perf_condition_peak"] = None
        result["perf_condition_end"] = None
        result["perf_condition_shape"] = None

    # ── Fatigue markers — human-readable detected patterns ───────────────────
    markers = []

    cad_drop = result["cadence_drop_pct"]
    if cad_drop is not None and cad_drop < -3:
        markers.append(
            f"Cadence degraded {abs(cad_drop):.1f}% in the final quarter "
            f"({result['cadence_q1_spm']:.0f} → {result['cadence_q4_spm']:.0f} spm)"
        )

    str_drop = result["stride_drop_pct"]
    if str_drop is not None and str_drop < -3:
        markers.append(
            f"Stride length shortened {abs(str_drop):.1f}% "
            f"({result['stride_q1_m']:.2f} → {result['stride_q4_m']:.2f} m)"
        )

    gct_chg = result["gct_increase_pct"]
    if gct_chg is not None and gct_chg > 3:
        markers.append(
            f"Ground contact time increased {gct_chg:.1f}% "
            f"({result['gct_q1_ms']:.0f} → {result['gct_q4_ms']:.0f} ms) — ground time worsened"
        )

    vr_chg = result["vr_change_pct"]
    if vr_chg is not None and vr_chg > 3:
        markers.append(
            f"Vertical ratio worsened {vr_chg:.1f}% "
            f"({result['vr_q1_pct']:.1f} → {result['vr_q4_pct']:.1f}%) — more vertical, less forward"
        )

    pwr_fade = result["power_fade_pct"]
    if pwr_fade is not None and pwr_fade < -5:
        markers.append(
            f"Power faded {abs(pwr_fade):.1f}% "
            f"({result['power_q1_w']:.0f} → {result['power_q4_w']:.0f} W)"
        )

    eco_drift = result["eco_drift_pct"]
    if eco_drift is not None and eco_drift > 5:
        markers.append(
            f"Running economy degraded {eco_drift:.1f}% across the run "
            f"(HR:pace ratio drifted — heart working harder for same speed)"
        )

    hr_drift = result["hr_drift_pct"]
    if hr_drift is not None and hr_drift > 8:
        markers.append(
            f"Significant cardiac drift: HR rose {hr_drift:.1f}% at equal effort "
            f"(first quarter avg → last quarter avg)"
        )

    # Coordination fatigue: if cadence dropped AND stride shortened simultaneously
    if cad_drop is not None and str_drop is not None and cad_drop < -2 and str_drop < -2:
        markers.append(
            "Classic late-run fatigue signature: both cadence and stride length degraded "
            "together — speed maintained by neither mechanism, indicating neuromuscular fatigue"
        )

    if not markers:
        markers.append("No significant fatigue markers detected — form held well throughout")

    result["fatigue_markers"] = markers

    logger.info(
        "Biomechanics analysis complete for workout %d: %d fatigue markers, "
        "eco_drift=%.1f%%, hr_drift=%.1f%%",
        completed.id,
        len(markers),
        eco_drift or 0,
        hr_drift or 0,
    )
    return result


def update_running_profile(athlete_id: int, db_session: Session) -> RunningProfile:
    """Recompute and upsert the RunningProfile for an athlete.

    Queries the last 30 days of WorkoutTelemetry and CompletedWorkout,
    computes 30-day rolling averages, and sets trend direction where
    ≥3 data points exist.
    """
    cutoff = datetime.utcnow() - timedelta(days=30)

    # Get recent telemetry with their completed workouts
    rows = (
        db_session.query(WorkoutTelemetry, CompletedWorkout)
        .join(CompletedWorkout, WorkoutTelemetry.completed_workout_id == CompletedWorkout.id)
        .filter(
            WorkoutTelemetry.athlete_id == athlete_id,
            WorkoutTelemetry.recorded_at >= cutoff,
        )
        .all()
    )

    easy_cadences = []
    hard_cadences = []
    easy_gct = []
    hard_gct = []
    vo_values = []
    vr_values = []
    hr_drifts = []
    decouplings = []
    zone_compliances = []

    for telemetry, completed in rows:
        workout_type = None
        # Try to find workout type from planned workout
        if completed.planned_workout_id:
            from running_coach_ai.database.models import PlannedWorkout
            pw = db_session.query(PlannedWorkout).get(completed.planned_workout_id)
            if pw:
                workout_type = pw.workout_type

        is_easy = workout_type in ("easy", "long_run", None)
        is_hard = workout_type in ("tempo", "intervals")

        # Cadence
        cadence = telemetry.cadence_json or []
        valid_c = [v for v in cadence if v is not None and v > 0]
        if valid_c:
            avg_c = statistics.mean(valid_c)
            if is_easy:
                easy_cadences.append(avg_c)
            elif is_hard:
                hard_cadences.append(avg_c)

        # Ground contact time
        gct = telemetry.ground_contact_time_json or []
        valid_gct = [v for v in gct if v is not None and v > 0]
        if valid_gct:
            avg_gct = statistics.mean(valid_gct)
            if is_easy:
                easy_gct.append(avg_gct)
            elif is_hard:
                hard_gct.append(avg_gct)

        # Vertical oscillation
        vo = telemetry.vertical_oscillation_json or []
        valid_vo = [v for v in vo if v is not None]
        if valid_vo:
            vo_values.append(statistics.mean(valid_vo))

        # Vertical ratio
        vr = telemetry.vertical_ratio_json or []
        valid_vr = [v for v in vr if v is not None]
        if valid_vr:
            vr_values.append(statistics.mean(valid_vr))

        # Biomechanics
        bio = analyse_workout(telemetry, completed)
        if bio["hr_drift_pct"] is not None:
            hr_drifts.append(bio["hr_drift_pct"])
        if bio["aerobic_decoupling"] is not None:
            decouplings.append(bio["aerobic_decoupling"])
        if bio["zone_compliance_pct"] is not None:
            zone_compliances.append(bio["zone_compliance_pct"])

    def _avg(lst):
        return round(statistics.mean(lst), 2) if lst else None

    def _trend(lst):
        """Determine trend from a list of values (≥3 required)."""
        if len(lst) < 3:
            return None
        # Compare last third to first third
        n = len(lst)
        third = max(1, n // 3)
        early_avg = statistics.mean(lst[:third])
        recent_avg = statistics.mean(lst[-third:])
        diff_pct = (recent_avg - early_avg) / early_avg * 100 if early_avg != 0 else 0
        if diff_pct > 3:
            return "improving"
        if diff_pct < -3:
            return "declining"
        return "stable"

    # Cadence trend — lower CV is better (improving = more consistent)
    cadence_trend = _trend(easy_cadences) if len(easy_cadences) >= 3 else None

    # Upsert RunningProfile
    profile = (
        db_session.query(RunningProfile)
        .filter(RunningProfile.athlete_id == athlete_id)
        .first()
    )
    if not profile:
        profile = RunningProfile(athlete_id=athlete_id)
        db_session.add(profile)

    profile.updated_at = datetime.utcnow()
    profile.typical_cadence_easy_spm = _avg(easy_cadences)
    profile.typical_cadence_hard_spm = _avg(hard_cadences)
    profile.typical_ground_contact_easy_ms = _avg(easy_gct)
    profile.typical_ground_contact_hard_ms = _avg(hard_gct)
    profile.typical_vertical_oscillation_cm = _avg(vo_values)
    profile.typical_vertical_ratio_pct = _avg(vr_values)
    profile.cadence_trend = cadence_trend
    profile.hr_drift_pct = _avg(hr_drifts)
    profile.hr_pace_decoupling = _avg(decouplings)
    profile.easy_hr_zone_compliance_pct = _avg(zone_compliances)
    profile.hr_drift_trend = _trend(hr_drifts)
    profile.hr_pace_decoupling_trend = _trend(decouplings)
    profile.easy_hr_zone_compliance_trend = _trend(zone_compliances)

    db_session.commit()
    logger.info("Updated RunningProfile for athlete %d", athlete_id)
    return profile
