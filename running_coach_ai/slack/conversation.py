"""Coaching conversation handler — context assembly, tag extraction, memory."""

import json
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from running_coach_ai.coach.persona import call_claude, format_miles, format_pace_mi
from running_coach_ai.coach.personas import PERSONAS, get_persona
from running_coach_ai.database.models import (
    Athlete,
    CoachMemory,
    CompletedWorkout,
    ConversationMessage,
    Goal,
    HealthSnapshot,
    PlannedWorkout,
    RunningProfile,
    TrainingPlan,
    WorkoutTelemetry,
)
from running_coach_ai.database.session import scoped_query

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Garmin sync integrity validation
# ---------------------------------------------------------------------------

def _validate_garmin_sync(
    athlete: Athlete,
    upcoming: list,
    db_session: Session,
) -> None:
    """Validate and repair Garmin sync state for upcoming workouts.

    Uses a single bulk library scan (one API call) rather than per-workout
    schedule endpoint calls — reduces API overhead from O(n) to O(1).

    Repair logic (bounded to next 14 days to avoid flooding Garmin on every turn):
    - workout_id set AND schedule_id set, but NOT in library → stale IDs, clear both, queue resync
    - workout_id set AND schedule_id NULL (library-only orphan) → attempt to schedule the
      existing library entry; clear IDs if that fails so reconciliation re-uploads cleanly
    - workout_id NULL AND schedule_id NULL → already unsynced; reconciliation handles it
    """
    if not upcoming:
        return

    try:
        from running_coach_ai.garmin.client import (
            get_garmin_client, get_garmin_workout_library, schedule_existing_workout,
        )
        from running_coach_ai.garmin.workout_builder import APP_MARKER_RE

        garmin = get_garmin_client(
            athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted
        )

        # Single library scan — build date → [workout_id] map from app markers
        library_by_date: dict[str, list[int]] = {}
        for entry in get_garmin_workout_library(garmin):
            m = APP_MARKER_RE.search(entry.get("description") or "")
            if m and int(m.group(1)) == athlete.id:
                library_by_date.setdefault(m.group(2), []).append(int(entry["workoutId"]))

        today = date.today()
        repair_cutoff = today + timedelta(days=14)
        dates_to_resync: set[date] = set()
        changed = False

        for w in upcoming:
            date_iso = w.scheduled_date.isoformat()
            lib_ids = library_by_date.get(date_iso, [])
            within_repair_window = w.scheduled_date <= repair_cutoff

            if w.garmin_workout_id and w.garmin_schedule_id:
                # Fully synced in DB — verify it's still in Garmin library
                if not lib_ids:
                    logger.warning(
                        "Garmin sync gap: athlete %d on %s (workout_id=%s) "
                        "missing from library — clearing stale IDs",
                        athlete.id, w.scheduled_date, w.garmin_workout_id,
                    )
                    w.garmin_workout_id = None
                    w.garmin_schedule_id = None
                    changed = True
                    if within_repair_window:
                        dates_to_resync.add(w.scheduled_date)

            elif w.garmin_workout_id and not w.garmin_schedule_id:
                # Library-only orphan (upload succeeded, schedule failed previously)
                wid = int(w.garmin_workout_id)
                if wid in lib_ids and within_repair_window:
                    # Workout is still in library — just needs scheduling
                    try:
                        sched_id = schedule_existing_workout(garmin, wid, date_iso)
                        w.garmin_schedule_id = sched_id
                        changed = True
                        logger.info(
                            "Repaired library-only orphan for athlete %d on %s "
                            "(scheduled as %s)",
                            athlete.id, w.scheduled_date, sched_id or "unknown",
                        )
                    except Exception as e:
                        logger.warning(
                            "Could not schedule orphaned workout %s for athlete %d on %s: %s "
                            "— clearing IDs for clean reconciliation re-upload",
                            wid, athlete.id, w.scheduled_date, e,
                        )
                        w.garmin_workout_id = None
                        w.garmin_schedule_id = None
                        changed = True
                        dates_to_resync.add(w.scheduled_date)
                elif wid not in lib_ids:
                    # Stale workout_id — library no longer has it
                    logger.warning(
                        "Stale garmin_workout_id %s for athlete %d on %s — not in library, clearing",
                        wid, athlete.id, w.scheduled_date,
                    )
                    w.garmin_workout_id = None
                    w.garmin_schedule_id = None
                    changed = True
                    if within_repair_window:
                        dates_to_resync.add(w.scheduled_date)

        if changed:
            db_session.commit()

        # Targeted resync for cleared workouts within the repair window
        if dates_to_resync:
            from running_coach_ai.garmin.workout_builder import sync_day_to_garmin
            for target_date in sorted(dates_to_resync):
                try:
                    sync_day_to_garmin(
                        athlete.id,
                        athlete.garmin_email,
                        athlete.garmin_password_encrypted,
                        target_date,
                        db_session,
                    )
                except Exception as e:
                    logger.warning(
                        "Resync after validation failed for athlete %d on %s: %s",
                        athlete.id, target_date, e,
                    )

    except Exception as e:
        logger.warning("Garmin sync validation failed for athlete %d: %s", athlete.id, e)


# ---------------------------------------------------------------------------
# Detailed run telemetry context for coaching conversations
# ---------------------------------------------------------------------------

_DAY_OF_WEEK = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_MONTH_NAME = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _resolve_athlete_max_hr(athlete_id: int, db_session: Session) -> tuple[int, int]:
    """Return (athlete_max_hr, max_hr_run_count) for HR zone calibration."""
    from sqlalchemy import func as _sql_func

    max_hr = (
        db_session.query(_sql_func.max(CompletedWorkout.max_hr))
        .filter(CompletedWorkout.athlete_id == athlete_id, CompletedWorkout.max_hr.isnot(None))
        .scalar()
    ) or 189
    run_count = (
        db_session.query(_sql_func.count(CompletedWorkout.id))
        .filter(CompletedWorkout.athlete_id == athlete_id, CompletedWorkout.max_hr.isnot(None))
        .scalar()
    ) or 0
    return int(max_hr), int(run_count)


def _parse_date_references(text: str, today: date) -> list[date]:
    """Extract candidate dates from user message text, most recent first."""
    candidates: list[date] = []
    text_lower = text.lower()

    if "yesterday" in text_lower:
        candidates.append(today - timedelta(days=1))

    for name, weekday in _DAY_OF_WEEK.items():
        if name in text_lower:
            days_back = (today.weekday() - weekday) % 7 or 7
            candidates.append(today - timedelta(days=days_back))

    if "last week" in text_lower:
        week_start = today - timedelta(days=today.weekday() + 7)
        for i in range(7):
            candidates.append(week_start + timedelta(days=i))

    month_day_re = re.compile(
        r'\b(' + '|'.join(_MONTH_NAME) + r')[\s.]+(\d{1,2})(?:st|nd|rd|th)?\b',
        re.IGNORECASE,
    )
    for m in month_day_re.finditer(text):
        mon = _MONTH_NAME[m.group(1).lower()]
        day = int(m.group(2))
        try:
            candidate = date(today.year, mon, day)
            if candidate > today:
                candidate = date(today.year - 1, mon, day)
            candidates.append(candidate)
        except ValueError:
            pass

    return sorted(set(candidates), reverse=True)


def _detect_referenced_workout(
    text: str,
    athlete_id: int,
    exclude_id: int | None,
    db_session: Session,
) -> "CompletedWorkout | None":
    """Return a past CompletedWorkout (with telemetry) whose date is referenced in text.

    Only matches running-type activities. Returns None if no specific date is
    found in the message or no matching workout with telemetry exists.
    """
    _RUNNING = {
        "running", "trail_running", "treadmill_running", "track_running",
        "ultra_running", "virtual_run", "obstacle_run",
    }
    today = date.today()
    for target_date in _parse_date_references(text, today):
        cw = (
            scoped_query(db_session, CompletedWorkout, athlete_id)
            .join(WorkoutTelemetry, WorkoutTelemetry.completed_workout_id == CompletedWorkout.id)
            .filter(
                CompletedWorkout.date == target_date,
                CompletedWorkout.activity_type.in_(_RUNNING),
            )
            .first()
        )
        if cw and cw.id != exclude_id:
            return cw
    return None


def _format_run_analysis_for_context(
    completed: "CompletedWorkout",
    athlete: "Athlete",
    athlete_max_hr: int,
    max_hr_run_count: int,
) -> str:
    """Compute full biomechanics breakdown for a run and render it for the system prompt.

    Provides the coaching conversation with HR zone distribution (with absolute
    time), lap splits, decile progression, fatigue indices, aerobic indicators,
    and performance condition arc — enabling data-level Q&A mid-conversation.
    """
    from running_coach_ai.coach.biomechanics import analyse_workout

    telemetry = completed.telemetry
    if not telemetry:
        return ""

    try:
        bio = analyse_workout(
            telemetry,
            completed,
            athlete_max_hr=athlete_max_hr,
            max_hr_run_count=max_hr_run_count,
        )

        lines: list[str] = []

        pw = completed.planned_workout
        wtype = pw.workout_type if pw else "run"
        dist_str = format_miles(completed.distance_km) if completed.distance_km else "?"
        dur_str = f"{completed.duration_seconds // 60}min" if completed.duration_seconds else "?"
        lines.append(
            f"## Run Analysis — {completed.date.strftime('%b %d, %A')} · {wtype} · {dist_str} · {dur_str}"
        )
        lines.append("(Reference this section to answer any specific data questions about this run.)")
        lines.append("")

        # HR zone breakdown with absolute time
        total_secs = completed.duration_seconds or 0
        zone_source = bio.get("zone_source", "computed_max_hr")
        garmin_bounds = bio.get("garmin_zone_boundaries") or {}
        if zone_source == "garmin_lthr" and garmin_bounds:
            z2 = garmin_bounds.get(2, round(athlete_max_hr * 0.60))
            z3 = garmin_bounds.get(3, round(athlete_max_hr * 0.70))
            z4 = garmin_bounds.get(4, round(athlete_max_hr * 0.80))
            z5 = garmin_bounds.get(5, round(athlete_max_hr * 0.90))
            zone_label = f"Garmin LTHR-based zones (LTHR≈{garmin_bounds.get(5, '?')} bpm)"
        else:
            z2 = round(athlete_max_hr * 0.60)
            z3 = round(athlete_max_hr * 0.70)
            z4 = round(athlete_max_hr * 0.80)
            z5 = round(athlete_max_hr * 0.90)
            zone_label = f"computed from max HR {athlete_max_hr} bpm"

        def _zpct(key: str) -> float:
            v = bio.get(key)
            return v if v is not None else 0.0

        def _ztime(pct: float) -> str:
            if not total_secs or not pct:
                return ""
            secs = int(total_secs * pct / 100)
            return f"~{secs // 60}m{secs % 60:02d}s"

        z1p = _zpct("zone1_pct")
        z2p = _zpct("zone2_pct")
        z3p = _zpct("zone3_pct")
        z4p = _zpct("zone4_pct")
        z5p = _zpct("zone5_pct")
        lines.append(f"HR ZONE BREAKDOWN ({zone_label}):")
        lines.append(f"  Z1 (<{z2} bpm):          {z1p:5.1f}%  {_ztime(z1p)}")
        lines.append(f"  Z2 ({z2}–{z3} bpm):   {z2p:5.1f}%  {_ztime(z2p)}  ← aerobic base")
        lines.append(f"  Z3 ({z3}–{z4} bpm):   {z3p:5.1f}%  {_ztime(z3p)}")
        lines.append(f"  Z4 ({z4}–{z5} bpm):   {z4p:5.1f}%  {_ztime(z4p)}  ← threshold")
        lines.append(f"  Z5 (>{z5} bpm):          {z5p:5.1f}%  {_ztime(z5p)}")
        lines.append("")

        # Lap splits
        laps = telemetry.laps_json or []
        if laps:
            lines.append("LAP SPLITS:")
            for lap in laps:
                dist_m = lap.get("distance_m") or 0
                lap_dist = f"{dist_m / 1609.34:.2f} mi" if dist_m else "?"
                lap_pace = format_pace_mi(lap.get("avg_pace_min_per_km")) if lap.get("avg_pace_min_per_km") else "N/A"
                lap_hr = f"HR {lap['avg_hr']:.0f}" if lap.get("avg_hr") else ""
                lap_cad = f"cad {lap['avg_cadence_spm']:.0f}" if lap.get("avg_cadence_spm") else ""
                lap_pwr = f"pwr {lap['avg_power_w']:.0f}W" if lap.get("avg_power_w") else ""
                parts = [p for p in [lap_dist, lap_pace, lap_hr, lap_cad, lap_pwr] if p]
                lines.append(f"  Lap {lap.get('lap_number', '?'):>2}: " + " | ".join(parts))
            lines.append("")

        # Decile progression table
        hr_d = bio.get("hr_deciles") or [None] * 10
        pace_d = bio.get("pace_deciles") or [None] * 10
        cad_d = bio.get("cadence_deciles") or [None] * 10
        pwr_d = bio.get("power_deciles") or [None] * 10
        eco_d = bio.get("eco_deciles") or [None] * 10
        if any(v is not None for v in hr_d):
            lines.append("DECILE PROGRESSION (each segment ≈ 10% of run duration):")
            lines.append("  Seg | Pace (min/mi)  | HR (bpm) | Cadence | Power | Economy")
            for i in range(10):
                p = format_pace_mi(pace_d[i]) if pace_d[i] else "   —  "
                h = f"{hr_d[i]:.0f}" if hr_d[i] else " — "
                c = f"{cad_d[i]:.0f}" if cad_d[i] else " — "
                w = f"{pwr_d[i]:.0f}" if pwr_d[i] else " — "
                e = f"{eco_d[i]:.1f}" if eco_d[i] else " — "
                lines.append(f"   {i + 1:2d} |  {p:<13} |  {h:<7}  |   {c:<5}  | {w:<5} | {e}")
            lines.append("")

        # Fatigue indices
        def _fv(val, decimals: int = 1) -> str:
            return f"{val:.{decimals}f}" if val is not None else "N/A"

        def _fc(val) -> str:
            if val is None:
                return "N/A"
            return f"{'+' if val > 0 else ''}{val:.1f}%"

        lines.append("FATIGUE INDICES (first quarter → last quarter):")
        lines.append(
            f"  Cadence:        {_fv(bio.get('cadence_q1_spm'), 0)} → "
            f"{_fv(bio.get('cadence_q4_spm'), 0)} spm  ({_fc(bio.get('cadence_drop_pct'))})"
        )
        lines.append(
            f"  Stride length:  {_fv(bio.get('stride_q1_m'), 2)} → "
            f"{_fv(bio.get('stride_q4_m'), 2)} m    ({_fc(bio.get('stride_drop_pct'))})"
        )
        lines.append(
            f"  Ground contact: {_fv(bio.get('gct_q1_ms'), 0)} → "
            f"{_fv(bio.get('gct_q4_ms'), 0)} ms  ({_fc(bio.get('gct_increase_pct'))})"
        )
        lines.append(
            f"  Vertical ratio: {_fv(bio.get('vr_q1_pct'), 2)} → "
            f"{_fv(bio.get('vr_q4_pct'), 2)}%   ({_fc(bio.get('vr_change_pct'))})"
        )
        lines.append(
            f"  Power:          {_fv(bio.get('power_q1_w'), 0)} → "
            f"{_fv(bio.get('power_q4_w'), 0)} W    ({_fc(bio.get('power_fade_pct'))})"
        )
        lines.append("")

        # Aerobic indicators
        lines.append("AEROBIC INDICATORS:")
        lines.append(
            f"  HR drift (Q4 vs Q1):      {_fv(bio.get('hr_drift_pct'))}%"
            "  (>5% = cardiac fatigue, >10% = significant)"
        )
        lines.append(
            f"  Aerobic decoupling:        {_fv(bio.get('aerobic_decoupling'))}%"
            "  (>5% = running too hard for aerobic benefit)"
        )
        lines.append(
            f"  Zone 2 compliance:         {_fv(bio.get('zone_compliance_pct'))}%"
            "  (target >80% for easy/long runs)"
        )
        lines.append(f"  Cadence consistency (CV):  {_fv(bio.get('cadence_cv'))}%")
        lines.append("")

        # Performance condition arc
        if bio.get("perf_condition_start") is not None:
            def _signed(v: float) -> str:
                return f"{'+' if v >= 0 else ''}{v:.0f}"

            lines.append(
                f"PERFORMANCE CONDITION: Start {_signed(bio['perf_condition_start'])} | "
                f"Peak {_signed(bio['perf_condition_peak'])} | "
                f"End {_signed(bio['perf_condition_end'])} | "
                f"Shape: {bio.get('perf_condition_shape') or 'N/A'}"
            )
            lines.append("")

        # Detected fatigue patterns
        markers = bio.get("fatigue_markers") or []
        if markers:
            lines.append("DETECTED PATTERNS:")
            for marker in markers:
                lines.append(f"  • {marker}")

        return "\n".join(lines)

    except Exception as e:
        logger.warning("Could not compute run analysis context for workout %d: %s", completed.id, e)
        return ""


# ---------------------------------------------------------------------------
# System prompt assembly (8 sections per coach-output.md)
# ---------------------------------------------------------------------------

def build_system_prompt(
    athlete: Athlete,
    db_session: Session,
    spotlight_analyses: list[str] | None = None,
) -> str:
    """Assemble the full system prompt for a coaching conversation turn.

    Sections:
    1. Coach persona and philosophy
    2. Athlete profile
    3. Current plan phase + this week's sessions
    4. Today's health snapshot
    5. Last 5 completed workouts
    6. Weather today + 3 days
    7. Active coach memories
    8. (Conversation history is passed as messages, not in the system prompt)
    """
    # Calculate date context once up front — used by multiple sections.
    # Use the athlete's timezone so early-morning queries fetch the correct date.
    tz = ZoneInfo(athlete.timezone or "America/New_York")
    today = datetime.now(tz).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    sections = [get_persona(athlete.coach_key).persona_block]

    # --- Section 1b: Current date + data guardrails ---
    sections.append(
        f"\n## Current Date\n"
        f"Today is **{today.strftime('%A, %B %d, %Y')}**. "
        f"Current week runs {week_start.strftime('%b %d')} (Mon) – {week_end.strftime('%b %d')} (Sun).\n\n"
        f"**Data rules — never break these:**\n"
        f"- All Garmin data available to you is already loaded into this prompt. "
        f"If the athlete asks about a run not listed in Recent Workouts, emit `<garmin_fetch/>` to pull it — "
        f"never invent or estimate run stats.\n"
        f"- Never state a distance, pace, HR, or date you cannot see explicitly in this prompt.\n"
        f"- `<garmin_fetch/>` pulls new activity data from Garmin into context. "
        f"Use it when the athlete asks about a run you cannot find below."
    )

    # --- Section 2: Athlete profile ---
    goals = (
        scoped_query(db_session, Goal, athlete.id)
        .filter(Goal.active == True)
        .all()
    )
    upcoming_goal_lines = []
    completed_goal_lines = []
    for g in goals:
        h = g.target_time_seconds // 3600
        m = (g.target_time_seconds % 3600) // 60
        race_date_obj = g.race_date if isinstance(g.race_date, date) else date.fromisoformat(str(g.race_date))
        race_name_str = f" ({g.race_name})" if getattr(g, 'race_name', None) else " (event name unknown — do not infer from city/date)"
        if race_date_obj >= today:
            days_to_race = (race_date_obj - today).days
            if days_to_race == 0:
                countdown = "RACE DAY TODAY"
            else:
                countdown = f"{days_to_race} days ({days_to_race // 7} weeks) to race day"
            upcoming_goal_lines.append(
                f"  - {g.race_type}{race_name_str} on {g.race_date} | {countdown} | target: {h}:{m:02d} | "
                f"experience: {g.experience_level}, {g.training_days_per_week} days/week"
            )
        else:
            days_ago = (today - race_date_obj).days
            completed_goal_lines.append(
                f"  - [COMPLETED {days_ago}d ago] {g.race_type}{race_name_str} on {g.race_date} | target was {h}:{m:02d}"
            )

    goal_parts = []
    if upcoming_goal_lines:
        goal_parts.append("Upcoming races:\n" + "\n".join(upcoming_goal_lines))
    if completed_goal_lines:
        goal_parts.append("Completed races (for context only — do not treat as active targets):\n" + "\n".join(completed_goal_lines))
    goals_text = "\n".join(goal_parts) if goal_parts else "  - No active goals"
    style_label = {
        "time": "time-based (prescribe all runs by duration, nearest 5 min — use target_duration_seconds)",
        "distance": "distance-based (prescribe all runs in whole miles — use target_distance_km)",
    }.get(athlete.prescription_style, "not yet set — use distance-based as default")
    sections.append(
        f"\n## Athlete Profile\n"
        f"- Name: {athlete.name}\n"
        f"- Age: {athlete.age}\n"
        f"- Prescription style: {style_label}\n"
        f"- Goals:\n{goals_text}"
    )

    # --- Section 3: Current plan + this week ---
    plan = (
        scoped_query(db_session, TrainingPlan, athlete.id)
        .filter(TrainingPlan.active == True)
        .first()
    )
    if plan:
        total_weeks = max(1, (plan.valid_to - plan.valid_from).days // 7)
        elapsed_days = (today - plan.valid_from).days
        week_num = min(total_weeks, max(1, elapsed_days // 7 + 1))
        pct_complete = min(100, round(elapsed_days / max(1, (plan.valid_to - plan.valid_from).days) * 100))
        sections.append(
            f"\n## Current Training Phase: {plan.current_phase} "
            f"(Week {week_num} of {total_weeks} — {pct_complete}% through plan)"
        )

    this_week = (
        scoped_query(db_session, PlannedWorkout, athlete.id)
        .filter(
            PlannedWorkout.scheduled_date >= week_start,
            PlannedWorkout.scheduled_date <= week_end,
        )
        .order_by(PlannedWorkout.scheduled_date)
        .all()
    )
    if this_week:
        week_lines = ["## This Week's Sessions"]
        for w in this_week:
            if w.target_distance_km:
                vol = f" {format_miles(w.target_distance_km)}"
            elif w.target_duration_seconds:
                vol = f" {w.target_duration_seconds // 60}min"
            else:
                vol = ""
            status_marker = f" [{w.status}]" if w.status != "planned" else ""
            week_lines.append(f"- {w.scheduled_date} ({w.scheduled_date.strftime('%A')}) | {w.workout_type}{vol}{status_marker}: {w.description or ''}")
        sections.append("\n".join(week_lines))

    # --- Section 4: Health data — today's snapshot + 7-day HRV trend ---
    # If today's snapshot isn't in the DB yet, or exists but has no usable health data
    # (e.g. stored by a broken parser run earlier today), attempt a live fetch from Garmin.
    _KEY_HEALTH_FIELDS = ("sleep_score", "hrv_score", "body_battery_start")
    if athlete.garmin_email and athlete.garmin_password_encrypted:
        today_exists = (
            scoped_query(db_session, HealthSnapshot, athlete.id)
            .filter(HealthSnapshot.date == today)
            .first()
        )
        _needs_fetch = today_exists is None or any(
            getattr(today_exists, f) is None for f in _KEY_HEALTH_FIELDS
        )
        if _needs_fetch:
            import time as _time
            from running_coach_ai.garmin.client import get_garmin_client, get_health_snapshot
            from running_coach_ai.garmin.parser import parse_health_snapshot

            for _attempt in range(3):
                try:
                    garmin = get_garmin_client(
                        athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted
                    )
                    raw = get_health_snapshot(garmin, today.isoformat())
                    parse_health_snapshot(raw, athlete.id, today, db_session)
                    db_session.flush()
                    logger.info("Live health fetch for athlete %d in conversation context", athlete.id)
                    break
                except Exception as _e:
                    if _attempt < 2:
                        logger.warning(
                            "Live health fetch attempt %d/3 failed for athlete %d: %s",
                            _attempt + 1, athlete.id, _e,
                        )
                        _time.sleep(2.0 * (2 ** _attempt))
                    else:
                        logger.warning(
                            "Live health fetch failed for athlete %d in conversation after 3 attempts: %s",
                            athlete.id, _e,
                        )

    recent_health = (
        scoped_query(db_session, HealthSnapshot, athlete.id)
        .filter(
            HealthSnapshot.date >= today - timedelta(days=7),
        )
        .order_by(HealthSnapshot.date.desc())
        .all()
    )

    if recent_health:
        health = recent_health[0]  # most recent
        label = "Today's" if health.date == today else f"{health.date} (most recent available)"

        # Sleep duration in h:mm
        sleep_str = "N/A"
        if health.sleep_duration_seconds:
            sh = health.sleep_duration_seconds // 3600
            sm = (health.sleep_duration_seconds % 3600) // 60
            sleep_str = f"{sh}h {sm:02d}m"

        # Body battery: show both start and end if available
        bb_str = "N/A"
        if health.body_battery_start is not None:
            bb_str = str(health.body_battery_start)
            if health.body_battery_end is not None:
                bb_str += f" → {health.body_battery_end}"

        health_lines = [
            f"\n## {label} Health Data",
            f"- HRV: {health.hrv_score} ({health.hrv_status or 'N/A'}) — use this as the primary readiness signal",
            f"- Sleep: score {health.sleep_score}, duration {sleep_str}",
            f"- Resting HR: {health.resting_hr} bpm",
            f"- Body battery: {bb_str}",
            f"- Avg stress: {health.stress_avg}",
            f"- Steps: {health.steps}",
        ]
        if health.training_readiness is not None:
            health_lines.append(f"- Training readiness: {health.training_readiness}/100")
        if health.spo2_avg:
            health_lines.append(f"- SpO2: {health.spo2_avg:.1f}%")

        # 7-day HRV trend
        if len(recent_health) >= 2:
            health_lines.append("\n### HRV Last 7 Days (newest first)")
            for h in recent_health:
                trend_marker = ""
                if h.hrv_status:
                    trend_marker = {"BALANCED": "✓", "UNBALANCED": "↓", "LOW": "↓↓", "HIGH": "↑"}.get(
                        h.hrv_status.upper(), ""
                    )
                parts = [
                    f"HRV {h.hrv_score} {trend_marker}",
                    f"sleep {h.sleep_score or '?'}",
                    f"RHR {h.resting_hr or '?'}",
                    f"BB {h.body_battery_start or '?'}",
                ]
                if h.training_readiness is not None:
                    parts.append(f"TR {h.training_readiness}")
                health_lines.append(f"- {h.date}: {', '.join(parts)}")

        sections.append("\n".join(health_lines))
    else:
        sections.append(
            "\n## Health Data\n"
            "No Garmin health data available yet — will populate after the first morning check-in. "
            "Encourage the athlete to keep their Garmin device synced."
        )

    RUNNING_TYPES = {
        "running", "trail_running", "treadmill_running", "track_running",
        "ultra_running", "virtual_run", "obstacle_run",
    }

    # --- Section 5: Last 7 completed workouts (target vs actual for pace recalibration) ---
    all_recent = (
        scoped_query(db_session, CompletedWorkout, athlete.id)
        .order_by(CompletedWorkout.date.desc())
        .limit(14)  # fetch extra so we can split running vs cross-training
        .all()
    )
    recent_workouts = [w for w in all_recent if (w.activity_type or "running") in RUNNING_TYPES][:7]
    recent_xtraining = [w for w in all_recent if (w.activity_type or "running") not in RUNNING_TYPES][:7]

    if recent_workouts:
        last_run_date = recent_workouts[0].date.strftime("%b %d")
        workout_lines = [
            f"## Recent Workouts (target → actual) — {len(recent_workouts)} run(s) on record, most recent: {last_run_date}",
            "If a run is not listed here it is not in the database — emit <garmin_fetch/> rather than guessing.",
        ]
        for w in recent_workouts:
            pace_str = format_pace_mi(w.avg_pace_min_per_km) if w.avg_pace_min_per_km else "N/A"
            dist_str = format_miles(w.distance_km) if w.distance_km is not None else "N/A"
            dur_str = f"{w.duration_seconds // 60}min" if w.duration_seconds is not None else "N/A"
            hr_str = f"HR {w.avg_hr}" if w.avg_hr else ""
            # Training effect + VO2max
            te_str = f" TE:{w.aerobic_training_effect:.1f}" if w.aerobic_training_effect else ""
            vo2_str = f" VO2max:{w.vo2max_estimate:.0f}" if w.vo2max_estimate else ""
            # Target vs actual pace comparison
            pw = w.planned_workout
            workout_type = pw.workout_type if pw else "run"
            if pw and pw.target_pace_min_per_km and w.avg_pace_min_per_km:
                tgt_str = format_pace_mi(pw.target_pace_min_per_km)
                diff_sec_mi = (w.avg_pace_min_per_km - pw.target_pace_min_per_km) * 1.60934 * 60
                sign = "+" if diff_sec_mi >= 0 else ""
                pace_comparison = f"{tgt_str}→{pace_str} ({sign}{diff_sec_mi:.0f}s/mi)"
            elif pw and pw.target_pace_min_per_km:
                pace_comparison = f"{format_pace_mi(pw.target_pace_min_per_km)} target → N/A"
            else:
                pace_comparison = pace_str
            # Target vs actual distance
            if pw and pw.target_distance_km and w.distance_km is not None:
                tgt_dist = format_miles(pw.target_distance_km)
                dist_comparison = f"{tgt_dist}→{dist_str}"
            else:
                dist_comparison = dist_str
            workout_lines.append(
                f"- {w.date} | {workout_type} | {dist_comparison} in {dur_str} | {pace_comparison} | {hr_str}{te_str}{vo2_str}"
            )
        sections.append("\n".join(workout_lines))

    if recent_xtraining:
        xt_lines = [
            "## Recent Cross-Training (last 7 days — contributes to fatigue/fitness)",
            "Use these to factor cumulative load and recovery needs into your coaching. These are NOT running sessions.",
        ]
        for w in recent_xtraining:
            dur_str = f"{w.duration_seconds // 60}min" if w.duration_seconds is not None else "N/A"
            hr_str = f" | avg HR {w.avg_hr}" if w.avg_hr else ""
            tl_str = f" | training load {w.training_load:.0f}" if w.training_load else ""
            cal_str = f" | {w.calories} cal" if w.calories else ""
            elev_str = f" | +{w.elevation_gain_m:.0f}m elev" if w.elevation_gain_m else ""
            atype = (w.activity_type or "cross_training").replace("_", " ")
            xt_lines.append(f"- {w.date} | {atype} | {dur_str}{hr_str}{tl_str}{cal_str}{elev_str}")
        sections.append("\n".join(xt_lines))

    # --- Section 5a: Weekly load summary ---
    completed_this_week = (
        scoped_query(db_session, CompletedWorkout, athlete.id)
        .filter(CompletedWorkout.date >= week_start, CompletedWorkout.date <= week_end)
        .all()
    )
    recent_4wk = (
        scoped_query(db_session, CompletedWorkout, athlete.id)
        .filter(CompletedWorkout.date >= today - timedelta(days=28))
        .all()
    )
    # Volume metrics are running-only; cross-training is shown separately above.
    runs_this_week = [w for w in completed_this_week if (w.activity_type or "running") in RUNNING_TYPES]
    runs_4wk = [w for w in recent_4wk if (w.activity_type or "running") in RUNNING_TYPES]
    planned_run_sessions = [w for w in this_week if w.workout_type != "rest"]

    def _planned_km(w) -> float:
        if w.target_distance_km:
            return w.target_distance_km
        if w.target_duration_seconds and w.target_pace_min_per_km:
            return (w.target_duration_seconds / 60) / w.target_pace_min_per_km
        return 0.0

    km_planned_week = sum(_planned_km(w) for w in planned_run_sessions)
    km_done_week = sum(w.distance_km or 0 for w in runs_this_week)
    mi_planned = km_planned_week / 1.60934
    mi_done = km_done_week / 1.60934
    mi_4wk = sum(w.distance_km or 0 for w in runs_4wk) / 1.60934
    load_lines = [
        f"## Weekly Load (week of {week_start.strftime('%b %d')})",
        f"- Sessions: {len(runs_this_week)} done / {len(planned_run_sessions)} planned this week",
        f"- Volume: {mi_done:.1f} mi done / {mi_planned:.1f} mi planned this week",
        f"- 4-week rolling: {mi_4wk:.0f} mi total ({mi_4wk / 4:.1f} mi/week avg)",
    ]
    # VO2max trend across recent completed workouts
    vo2_series = sorted(
        [(w.date, w.vo2max_estimate) for w in recent_workouts if w.vo2max_estimate],
        key=lambda x: x[0],
    )
    if len(vo2_series) >= 2:
        v_old, v_new = vo2_series[0][1], vo2_series[-1][1]
        vo2_arrow = "↑" if v_new > v_old else ("↓" if v_new < v_old else "→")
        load_lines.append(
            f"- VO2max trend ({len(vo2_series)} runs): {v_old:.0f} → {v_new:.0f} {vo2_arrow}"
        )
    elif vo2_series:
        load_lines.append(f"- VO2max estimate: {vo2_series[0][1]:.0f}")
    sections.append("\n".join(load_lines))

    # --- Section 5c: Running biomechanics profile (30-day rolling averages + trends) ---
    profile = (
        scoped_query(db_session, RunningProfile, athlete.id)
        .first()
    )
    if profile:
        def _pfmt(val, unit="", decimals=1):
            if val is None:
                return "N/A"
            return f"{val:.{decimals}f}{unit}"

        def _trend_arrow(t):
            return {"improving": "↑ improving", "declining": "↓ declining", "stable": "→ stable"}.get(t or "", t or "N/A")

        profile_lines = [
            f"## Running Biomechanics Profile (30-day rolling, updated {profile.updated_at.strftime('%b %d')})",
            f"- Easy cadence: {_pfmt(profile.typical_cadence_easy_spm, ' spm', 0)} | Hard cadence: {_pfmt(profile.typical_cadence_hard_spm, ' spm', 0)} | Trend: {_trend_arrow(profile.cadence_trend)}",
            f"- Easy GCT: {_pfmt(profile.typical_ground_contact_easy_ms, ' ms', 0)} | Hard GCT: {_pfmt(profile.typical_ground_contact_hard_ms, ' ms', 0)}",
            f"- Vertical oscillation: {_pfmt(profile.typical_vertical_oscillation_cm, ' cm')} | Vertical ratio: {_pfmt(profile.typical_vertical_ratio_pct, '%')}",
            f"- HR drift (avg long runs): {_pfmt(profile.hr_drift_pct, '%')} | Trend: {_trend_arrow(profile.hr_drift_trend)}",
            f"- Aerobic decoupling: {_pfmt(profile.hr_pace_decoupling, '%')} | Trend: {_trend_arrow(profile.hr_pace_decoupling_trend)}",
            f"- Zone 2 compliance (easy runs): {_pfmt(profile.easy_hr_zone_compliance_pct, '%')} | Trend: {_trend_arrow(profile.easy_hr_zone_compliance_trend)}",
        ]
        sections.append("\n".join(profile_lines))

    # --- Section 5b: Upcoming training calendar (today → race day, with Garmin sync status) ---
    # This IS the Garmin calendar — workouts marked [on Garmin ✓] are live on the athlete's device.
    # Use plan.valid_to (race day) as the horizon so the coach can see and modify the full plan.
    cal_end = plan.valid_to if plan else today + timedelta(days=28)
    upcoming = (
        scoped_query(db_session, PlannedWorkout, athlete.id)
        .filter(
            PlannedWorkout.scheduled_date >= today,
            PlannedWorkout.scheduled_date <= cal_end,
        )
        .order_by(PlannedWorkout.scheduled_date)
        .all()
    )

    # Validate DB sync state against live Garmin (next 7 days only; silent on errors)
    if upcoming and athlete.garmin_email and athlete.garmin_password_encrypted:
        _validate_garmin_sync(athlete, upcoming, db_session)

    if upcoming:
        synced_count = sum(1 for w in upcoming if w.garmin_workout_id)
        cal_lines = [
            f"## Training Calendar — {today.strftime('%b %d')} → {cal_end.strftime('%b %d, %Y')} "
            f"({synced_count}/{len(upcoming)} synced to Garmin)"
        ]
        for w in upcoming:
            if w.target_distance_km:
                vol = f" {format_miles(w.target_distance_km)}"
            elif w.target_duration_seconds:
                vol = f" {w.target_duration_seconds // 60}min"
            else:
                vol = ""
            garmin_tag = " ✓Garmin" if w.garmin_workout_id else " ✗not on Garmin"
            status_tag = f" [{w.status}]" if w.status not in ("planned", None) else ""
            cal_lines.append(
                f"- {w.scheduled_date} ({w.scheduled_date.strftime('%A')}) |"
                f" {w.workout_type}{vol}{status_tag}{garmin_tag}: {w.description or ''}"
            )
        sections.append("\n".join(cal_lines))

    # --- Section 6: Weather ---
    try:
        if athlete.home_lat and athlete.home_lon:
            from running_coach_ai.weather.client import get_forecast, summarise_forecast
            forecast_raw = get_forecast(athlete.home_lat, athlete.home_lon)
            forecast_summary = summarise_forecast(forecast_raw)
            if forecast_summary:
                weather_lines = ["## Weather Forecast"]
                for day_info in forecast_summary[:4]:
                    weather_lines.append(
                        f"- {day_info['date']}: {day_info['condition']}, "
                        f"{day_info['temp_min']}–{day_info['temp_max']}°C, "
                        f"wind {day_info['wind_max']} km/h, "
                        f"rain {day_info['precip_prob']}%"
                    )
                sections.append("\n".join(weather_lines))
    except Exception as e:
        logger.warning("Weather fetch failed for athlete %d: %s", athlete.id, e)

    # --- Section 7: Active coach memories ---
    memories = (
        scoped_query(db_session, CoachMemory, athlete.id)
        .filter(CoachMemory.active == True)
        .all()
    )
    if memories:
        mem_lines = ["## Important Facts About This Athlete"]
        for m in memories:
            mem_lines.append(f"- [{m.category}] {m.content}")
        sections.append("\n".join(mem_lines))

    # --- Section 8: Detailed run telemetry for data-driven Q&A ---
    # Computed and injected by handle_message() for the most recent run (always)
    # and any additionally referenced run detected in the user's message.
    if spotlight_analyses:
        for analysis in spotlight_analyses:
            if analysis:
                sections.append(analysis)

    # --- Section 9: Available Coaches ---
    coach_lines = ["## Available Coaches"]
    for p in PERSONAS.values():
        coach_lines.append(f"- **{p.name}** (`{p.key}`): {p.description}")
    coach_lines.append(
        "\nWhen the athlete asks to see, browse, or list available coaches, present this section. "
        "Do not emit `<coach_switch>` unless they explicitly confirm a choice."
    )
    sections.append("\n".join(coach_lines))

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# On-demand Garmin fetch (conversation-time ingest, no feedback DM)
# ---------------------------------------------------------------------------

_GARMIN_FETCH_RE = re.compile(r"<garmin_fetch\s*(?:/>|></garmin_fetch\s*>)")


def _handle_garmin_fetch(athlete: Athlete, db_session: Session) -> bool:
    """Poll Garmin for new activities and ingest them during a conversation turn.

    Unlike the scheduler job, skips the feedback DM — the coaching response
    IS the feedback. Sets feedback_given=True so activity_poll won't double-send.

    Returns True if at least one new activity was ingested.
    """
    from running_coach_ai.garmin.client import (
        get_garmin_client, poll_new_activities,
        fetch_athlete_lthr, fetch_activity_hr_zones,
    )
    from running_coach_ai.garmin.parser import parse_activity_summary
    from running_coach_ai.garmin.telemetry import extract_telemetry, ingest_lap_splits
    from running_coach_ai.coach.biomechanics import analyse_workout, update_running_profile
    from sqlalchemy import func as _sql_func

    RUNNING_TYPES = {
        "running", "trail_running", "treadmill_running", "track_running",
        "ultra_running", "virtual_run", "obstacle_run",
    }

    if not athlete.garmin_email or not athlete.garmin_password_encrypted:
        return False

    try:
        garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)
        new_ids = poll_new_activities(garmin, athlete.id, db_session)
        if not new_ids:
            return False

        for activity_id in new_ids:
            try:
                activity_data = garmin.get_activity(activity_id)
                detail = garmin.get_activity_details(activity_id)

                stub = (
                    db_session.query(CompletedWorkout)
                    .filter(
                        CompletedWorkout.garmin_activity_id == str(activity_id),
                        CompletedWorkout.athlete_id == athlete.id,
                        CompletedWorkout.duration_seconds.is_(None),
                    )
                    .first()
                )
                if stub:
                    db_session.delete(stub)
                    db_session.flush()

                completed = parse_activity_summary(activity_data, athlete.id, db_session)
                activity_type = completed.activity_type or "unknown"

                if activity_type in RUNNING_TYPES:
                    telemetry = extract_telemetry(detail, completed.id, athlete.id, db_session)
                    ingest_lap_splits(garmin, athlete.id, activity_id, telemetry, db_session)

                    if not athlete.lthr_bpm:
                        lthr = fetch_athlete_lthr(garmin, athlete.id)
                        if lthr:
                            athlete.lthr_bpm = lthr

                    garmin_hr_zones = fetch_activity_hr_zones(garmin, str(activity_id), athlete.id)
                    athlete_max_hr = (
                        db_session.query(_sql_func.max(CompletedWorkout.max_hr))
                        .filter(CompletedWorkout.athlete_id == athlete.id, CompletedWorkout.max_hr.isnot(None))
                        .scalar()
                    ) or completed.max_hr or 189
                    max_hr_run_count = (
                        db_session.query(_sql_func.count(CompletedWorkout.id))
                        .filter(CompletedWorkout.athlete_id == athlete.id, CompletedWorkout.max_hr.isnot(None))
                        .scalar()
                    ) or 0
                    analyse_workout(
                        telemetry, completed,
                        athlete_max_hr=athlete_max_hr,
                        max_hr_run_count=max_hr_run_count,
                        garmin_hr_zones=garmin_hr_zones,
                    )
                    update_running_profile(athlete.id, db_session)

                completed.feedback_given = True
                db_session.commit()
                logger.info("Ingested activity %s during garmin_fetch for athlete %d", activity_id, athlete.id)

            except Exception as e:
                logger.error(
                    "Failed to ingest activity %s during garmin_fetch for athlete %d: %s",
                    activity_id, athlete.id, e,
                )

        return True

    except Exception as e:
        logger.error("Garmin fetch failed for athlete %d: %s", athlete.id, e)
        return False


# ---------------------------------------------------------------------------
# Tag extraction
# ---------------------------------------------------------------------------

def _round_duration(seconds) -> int | None:
    """Round a duration to the nearest 5 minutes (300 seconds), minimum 5 min."""
    if not seconds:
        return seconds
    return max(300, round(seconds / 300) * 300)


def extract_and_apply_plan(
    athlete_id: int,
    claude_response: str,
    db_session: Session,
) -> str:
    """Extract <plan> tags, apply mutations to DB + Garmin, return cleaned text.

    All DB mutations are applied first, then Garmin is synced in week batches.
    This avoids making one Garmin API call per workout (which hits rate limits
    for bulk changes like "move all runs to Saturday across a 20-week plan").
    """
    plan_blocks = re.findall(r"<plan>(.*?)</plan>", claude_response, re.DOTALL)

    # Collect Garmin work to do after all DB mutations are committed.
    resync_days: set[date] = set()     # specific dates needing targeted day sync
    delete_workouts: list[PlannedWorkout] = []  # cancelled/skipped workouts to remove

    for block in plan_blocks:
        try:
            plan_data = json.loads(block.strip())
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in <plan> block, skipping")
            continue

        sessions = plan_data.get("sessions", [])
        plan = None  # lazy-load once if needed for new-workout creation

        for session in sessions:
            session_date = date.fromisoformat(session["date"])

            workout = (
                db_session.query(PlannedWorkout)
                .filter(
                    PlannedWorkout.athlete_id == athlete_id,
                    PlannedWorkout.scheduled_date == session_date,
                )
                .first()
            )

            if workout:
                if "workout_type" in session:
                    workout.workout_type = session["workout_type"]
                if "workout_name" in session:
                    workout.workout_name = session["workout_name"] or None
                if "description" in session:
                    workout.description = session["description"]
                if "target_distance_km" in session:
                    workout.target_distance_km = session["target_distance_km"]
                if "target_duration_seconds" in session:
                    workout.target_duration_seconds = _round_duration(session["target_duration_seconds"])
                if "target_pace_min_per_km" in session:
                    workout.target_pace_min_per_km = session["target_pace_min_per_km"]
                if "target_zones_json" in session:
                    workout.target_zones_json = session["target_zones_json"]
                if "status" in session:
                    workout.status = session["status"]
                if "reason" in session:
                    workout.modified_reason = session["reason"]
                workout.updated_at = datetime.utcnow()

                # Queue Garmin work — do not call Garmin inside this loop.
                if workout.garmin_workout_id or workout.garmin_schedule_id:
                    if workout.status in ("skipped", "cancelled"):
                        delete_workouts.append(workout)
                    else:
                        resync_days.add(session_date)
            else:
                if plan is None:
                    plan = (
                        scoped_query(db_session, TrainingPlan, athlete_id)
                        .filter(TrainingPlan.active == True)
                        .first()
                    )
                if plan:
                    new_workout = PlannedWorkout(
                        plan_id=plan.id,
                        athlete_id=athlete_id,
                        scheduled_date=session_date,
                        workout_type=session.get("workout_type", "easy"),
                        workout_name=session.get("workout_name") or None,
                        description=session.get("description"),
                        target_distance_km=session.get("target_distance_km"),
                        target_duration_seconds=_round_duration(session.get("target_duration_seconds")),
                        target_pace_min_per_km=session.get("target_pace_min_per_km"),
                        target_zones_json=session.get("target_zones_json"),
                        status=session.get("status", "planned"),
                        modified_reason=session.get("reason"),
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    db_session.add(new_workout)
                    # New workouts have no Garmin IDs yet — day sync will upload them.
                    if session.get("status", "planned") not in ("skipped", "cancelled"):
                        resync_days.add(session_date)

    db_session.commit()

    # --- Garmin sync: deletions first, then targeted day syncs ---
    for workout in delete_workouts:
        _delete_garmin_workout(athlete_id, workout, db_session)

    if resync_days:
        athlete = db_session.query(Athlete).get(athlete_id)
        if athlete and athlete.garmin_email and athlete.garmin_password_encrypted:
            from running_coach_ai.garmin.workout_builder import sync_day_to_garmin
            for target_date in sorted(resync_days):
                try:
                    sync_day_to_garmin(
                        athlete_id,
                        athlete.garmin_email,
                        athlete.garmin_password_encrypted,
                        target_date,
                        db_session,
                    )
                except Exception as e:
                    logger.error(
                        "Targeted day sync failed for athlete %d on %s: %s",
                        athlete_id, target_date, e,
                    )

    # Strip all <plan> blocks from response
    cleaned = re.sub(r"<plan>.*?</plan>", "", claude_response, flags=re.DOTALL).strip()
    return cleaned


def _delete_garmin_workout(athlete_id: int, workout: PlannedWorkout, db_session: Session) -> None:
    """Remove a cancelled/skipped workout from Garmin Connect and clear its IDs.

    Each ID is only cleared if its deletion was confirmed (success or 404 = already gone).
    IDs are retained on transient errors so the reconciliation pass can retry them.
    """
    try:
        athlete = db_session.query(Athlete).get(athlete_id)
        if not athlete or not athlete.garmin_email or not athlete.garmin_password_encrypted:
            return

        from running_coach_ai.garmin.client import (
            delete_workout, get_garmin_client, remove_workout_schedule,
        )
        garmin = get_garmin_client(athlete_id, athlete.garmin_email, athlete.garmin_password_encrypted)

        if workout.garmin_schedule_id:
            try:
                remove_workout_schedule(garmin, int(workout.garmin_schedule_id))
                workout.garmin_schedule_id = None
            except Exception as e:
                if "404" in str(e):
                    workout.garmin_schedule_id = None  # already gone
                else:
                    logger.warning(
                        "Could not remove Garmin schedule %s for athlete %d on %s: %s — will retry",
                        workout.garmin_schedule_id, athlete_id, workout.scheduled_date, e,
                    )

        if workout.garmin_workout_id:
            try:
                delete_workout(garmin, int(workout.garmin_workout_id))
                workout.garmin_workout_id = None
            except Exception as e:
                if "404" in str(e):
                    workout.garmin_workout_id = None  # already gone
                else:
                    logger.warning(
                        "Could not delete Garmin workout %s for athlete %d on %s: %s — will retry",
                        workout.garmin_workout_id, athlete_id, workout.scheduled_date, e,
                    )

        db_session.commit()
        logger.info(
            "Garmin delete for athlete %d on %s — schedule_id=%s workout_id=%s",
            athlete_id, workout.scheduled_date,
            workout.garmin_schedule_id, workout.garmin_workout_id,
        )
    except Exception as e:
        logger.error("Failed to delete Garmin workout for athlete %d: %s", athlete_id, e)


def _reconcile_cancelled_garmin_workouts(athlete_id: int, db_session: Session) -> None:
    """Retry Garmin deletion for any cancelled/skipped workouts that still have Garmin IDs.

    Called after every plan mutation so that transient failures don't leave
    workouts stranded on the athlete's Garmin calendar.
    """
    today = date.today()
    lingering = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete_id,
            PlannedWorkout.status.in_(["cancelled", "skipped"]),
            PlannedWorkout.scheduled_date >= today,
            (PlannedWorkout.garmin_workout_id.isnot(None) |
             PlannedWorkout.garmin_schedule_id.isnot(None)),
        )
        .all()
    )
    for workout in lingering:
        logger.info(
            "Reconciliation: athlete %d has cancelled workout on %s still in Garmin — deleting",
            athlete_id, workout.scheduled_date,
        )
        _delete_garmin_workout(athlete_id, workout, db_session)


def _resync_garmin_workout(athlete_id: int, workout: PlannedWorkout, db_session: Session) -> None:
    """Delete old Garmin workout and re-upload the updated version.

    Skips workouts scheduled in the past — never write to the Garmin past.
    """
    if workout.scheduled_date < date.today():
        logger.debug("Skipping past-date Garmin resync for %s", workout.scheduled_date)
        return

    try:
        athlete = db_session.query(Athlete).get(athlete_id)
        if not athlete or not athlete.garmin_email or not athlete.garmin_password_encrypted:
            return

        from running_coach_ai.garmin.client import (
            delete_workout, get_garmin_client, remove_workout_schedule,
            schedule_workout, upload_workout,
        )
        from running_coach_ai.garmin.workout_builder import build_workout_json

        garmin = get_garmin_client(athlete_id, athlete.garmin_email, athlete.garmin_password_encrypted)

        # Remove calendar entry first, then workout definition
        if workout.garmin_schedule_id:
            try:
                remove_workout_schedule(garmin, int(workout.garmin_schedule_id))
            except Exception:
                pass
        if workout.garmin_workout_id:
            try:
                delete_workout(garmin, int(workout.garmin_workout_id))
            except Exception:
                pass

        # Upload new
        workout_json = build_workout_json(workout)
        result = upload_workout(garmin, workout_json)
        new_id = result.get("workoutId")
        if new_id:
            sched = schedule_workout(garmin, new_id, workout.scheduled_date.isoformat())
            workout.garmin_workout_id = str(new_id)
            # Garmin returns "workoutScheduleId"; "scheduleId" is a fallback
            schedule_id = (
                sched.get("workoutScheduleId") or sched.get("scheduleId", "")
                if isinstance(sched, dict) else ""
            )
            workout.garmin_schedule_id = str(schedule_id)
            db_session.commit()
    except Exception as e:
        logger.error("Failed to resync Garmin workout for athlete %d: %s", athlete_id, e)


def extract_and_sync_garmin(
    athlete_id: int,
    claude_response: str,
    db_session: Session,
    plan_already_synced: bool = False,
) -> tuple[str, str]:
    """Extract <garmin_sync/> tag and sync future workouts to Garmin.

    When plan_already_synced=True the <plan> handler in the same turn already
    uploaded the affected weeks, so we skip the full re-upload and run
    verify-only to confirm the state.

    Returns (cleaned_response, status_note) where status_note is a
    human-readable sync result appended to the reply (empty string if no sync).
    """
    # Match both the canonical <garmin_sync/> and the function-call variant
    # that some model versions emit: <function_calls><invoke name="garmin_sync">...</invoke></function_calls>
    _GARMIN_SYNC_RE = re.compile(
        r"<garmin_sync\s*(?:/>|></garmin_sync\s*>|>)"
        r"|<function_calls>\s*<invoke\s+name=[\"']garmin_sync[\"'][^>]*/?\s*>.*?</invoke>\s*</function_calls>",
        re.DOTALL,
    )
    if not _GARMIN_SYNC_RE.search(claude_response):
        cleaned = _GARMIN_SYNC_RE.sub("", claude_response).strip()
        return cleaned, ""

    cleaned = _GARMIN_SYNC_RE.sub("", claude_response).strip()

    try:
        athlete = db_session.query(Athlete).get(athlete_id)
        if not athlete or not athlete.garmin_email or not athlete.garmin_password_encrypted:
            return cleaned, "\n\n_(Garmin sync skipped — no Garmin credentials on file.)_"

        if plan_already_synced:
            # The <plan> handler already uploaded the affected weeks this turn.
            # Skip the full re-upload — it would just delete and re-push every workout
            # in the plan, inflating the count and doing redundant Garmin API work.
            # Instead run verify-only to confirm everything landed correctly.
            try:
                from running_coach_ai.slack.admin import _run_garmin_verify
                matched, library_only, missing = _run_garmin_verify(athlete, db_session)
                total = len(matched) + len(library_only) + len(missing)
                if not library_only and not missing:
                    note = f"\n\n_✅ Verified {len(matched)}/{total} workout(s) confirmed on your Garmin calendar._"
                else:
                    issues = len(library_only) + len(missing)
                    note = (
                        f"\n\n_⚠️ Plan updated — {len(matched)}/{total} workout(s) verified on Garmin, "
                        f"{issues} need attention. Try `!admin resync-garmin --confirm` to fix._"
                    )
            except Exception as verify_err:
                logger.warning("Post-plan verify failed for athlete %d: %s", athlete_id, verify_err)
                note = (
                    "\n\n_⚠️ Plan updated, but I couldn't verify Garmin sync right now, "
                    "so I can't confirm calendar status yet._"
                )
            return cleaned, note

        from running_coach_ai.garmin.workout_builder import sync_week_to_garmin

        today = date.today()
        # Sync every week from today to race day (plan.valid_to), or 4 weeks if no plan
        plan = (
            scoped_query(db_session, TrainingPlan, athlete_id)
            .filter(TrainingPlan.active == True)
            .first()
        )
        sync_end = plan.valid_to if plan else today + timedelta(weeks=4)

        total_uploaded = 0
        total_failed = 0
        week_monday = today - timedelta(days=today.weekday())
        while week_monday <= sync_end:
            up, fail, _ = sync_week_to_garmin(
                athlete_id,
                athlete.garmin_email,
                athlete.garmin_password_encrypted,
                week_monday,
                db_session,
            )
            total_uploaded += up
            total_failed += fail
            week_monday += timedelta(weeks=1)

        # Verify the sync actually landed on Garmin (one library + calendar fetch).
        try:
            from running_coach_ai.slack.admin import _run_garmin_verify
            matched, library_only, missing = _run_garmin_verify(athlete, db_session)
            total = len(matched) + len(library_only) + len(missing)
            if not library_only and not missing:
                note = f"\n\n_✅ Verified {len(matched)}/{total} workout(s) confirmed on your Garmin calendar._"
            else:
                issues = len(library_only) + len(missing)
                note = (
                    f"\n\n_⚠️ Synced {total_uploaded} workout(s) — "
                    f"{len(matched)}/{total} verified on Garmin calendar, "
                    f"{issues} need attention. Try `!admin resync-garmin --confirm` to fix._"
                )
        except Exception as verify_err:
            logger.warning("Post-sync verify failed for athlete %d: %s", athlete_id, verify_err)
            attempted = total_uploaded + total_failed
            note = (
                f"\n\n_⚠️ Attempted Garmin sync for {attempted} workout(s) "
                f"({total_uploaded} uploaded, {total_failed} failed), "
                "but verification failed, so I can't confirm calendar status yet._"
            )
        return cleaned, note

    except Exception as e:
        logger.error("Garmin sync triggered from conversation failed for athlete %d: %s", athlete_id, e)
        return cleaned, "\n\n_(Garmin sync failed — I'll try again next time.)_"


def extract_and_save_memories(
    athlete_id: int,
    claude_response: str,
    db_session: Session,
) -> str:
    """Extract <remember> tags, save as CoachMemory rows, return cleaned text."""
    remember_blocks = re.findall(r"<remember>(.*?)</remember>", claude_response, re.DOTALL)

    for block in remember_blocks:
        content = block.strip()
        if not content:
            continue

        # Check for duplicate
        existing = (
            db_session.query(CoachMemory)
            .filter(
                CoachMemory.athlete_id == athlete_id,
                CoachMemory.content == content,
            )
            .first()
        )
        if existing:
            continue

        # Infer category from keywords
        content_lower = content.lower()
        if any(kw in content_lower for kw in ("injur", "pain", "achilles", "knee", "shin", "hamstring", "calf")):
            category = "injury"
        elif any(kw in content_lower for kw in ("prefer", "likes", "doesn't like", "hates", "avoids")):
            category = "preference"
        elif any(kw in content_lower for kw in ("goal", "race", "target", "aim")):
            category = "goal_note"
        elif any(kw in content_lower for kw in ("personal", "family", "work", "travel")):
            category = "personal"
        else:
            category = "performance_flag"

        memory = CoachMemory(
            athlete_id=athlete_id,
            category=category,
            content=content,
            source="conversation",
        )
        db_session.add(memory)

    db_session.commit()

    # Strip all <remember> blocks from response
    cleaned = re.sub(r"<remember>.*?</remember>", "", claude_response, flags=re.DOTALL).strip()
    return cleaned


_PRESCRIPTION_DEFAULT_PACES: dict[str, float] = {
    "easy": 6.5, "long_run": 7.0, "tempo": 5.0, "strides": 5.5, "cross_train": 6.0,
}
_PRESCRIPTION_TAG_RE = re.compile(r"<switch_prescription>(time|distance)</switch_prescription>", re.IGNORECASE)


def _convert_upcoming_workouts(athlete_id: int, new_style: str, db_session: Session) -> int:
    """Convert all upcoming planned workouts to the new prescription style. Returns count changed."""
    from running_coach_ai.coach.persona import km_to_mi, mi_to_km
    today = date.today()
    workouts = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete_id,
            PlannedWorkout.scheduled_date >= today,
            PlannedWorkout.status.in_(["planned", "modified"]),
            PlannedWorkout.workout_type.notin_(["rest", "race"]),
        )
        .all()
    )
    changed = 0
    for w in workouts:
        if w.target_zones_json:
            continue  # structured intervals/tempo — leave untouched
        pace = w.target_pace_min_per_km or _PRESCRIPTION_DEFAULT_PACES.get(w.workout_type, 6.5)
        if new_style == "time":
            if w.target_duration_seconds:
                w.target_distance_km = None
                changed += 1
            elif w.target_distance_km:
                secs = w.target_distance_km * pace * 60
                w.target_duration_seconds = max(300, round(secs / 300) * 300)
                w.target_distance_km = None
                changed += 1
        elif new_style == "distance":
            if w.target_distance_km:
                miles = max(1, round(km_to_mi(w.target_distance_km)))
                w.target_distance_km = mi_to_km(miles)
                w.target_duration_seconds = None
                changed += 1
            elif w.target_duration_seconds:
                dist_km = (w.target_duration_seconds / 60) / pace
                miles = max(1, round(km_to_mi(dist_km)))
                w.target_distance_km = mi_to_km(miles)
                w.target_duration_seconds = None
                changed += 1
    return changed


def extract_prescription_switch(athlete: Athlete, response: str, db_session: Session) -> str:
    """Extract <switch_prescription> tag, convert upcoming workouts, return cleaned response."""
    match = _PRESCRIPTION_TAG_RE.search(response)
    if not match:
        return response
    new_style = match.group(1).lower()
    old_style = athlete.prescription_style
    athlete.prescription_style = new_style
    changed = _convert_upcoming_workouts(athlete.id, new_style, db_session)
    db_session.flush()
    logger.info(
        "Prescription style switched %s→%s for athlete %d; %d workouts converted",
        old_style, new_style, athlete.id, changed,
    )
    return _PRESCRIPTION_TAG_RE.sub("", response).strip()


def extract_coach_switch(athlete: Athlete, response: str, db_session: Session) -> str:
    """Extract <coach_switch>key</coach_switch> tag, update athlete.coach_key if valid, return cleaned response."""
    from running_coach_ai.coach.personas import is_valid_coach_key, resolve_coach_key

    match = re.search(r"<coach_switch>(.*?)</coach_switch>", response, re.DOTALL)
    # Always strip the tag regardless of validity
    cleaned = re.sub(r"<coach_switch>.*?</coach_switch>", "", response, flags=re.DOTALL).strip()

    if match:
        new_key = match.group(1).strip()
        if is_valid_coach_key(new_key):
            resolved_key = resolve_coach_key(new_key)
            if resolved_key and resolved_key != athlete.coach_key:
                athlete.coach_key = resolved_key
                db_session.commit()
                logger.info("Coach switched to '%s' for athlete %d", resolved_key, athlete.id)
        else:
            logger.warning(
                "Invalid coach_switch key '%s' for athlete %d — ignoring",
                new_key, athlete.id,
            )

    return cleaned


# ---------------------------------------------------------------------------
# Main conversation handler
# ---------------------------------------------------------------------------

def process_message(
    athlete: Athlete,
    user_text: str,
    db_session: Session,
    source: str = "slack",
    coach_key: str | None = None,
) -> str:
    """Pure function: process one coaching message turn and return the cleaned response.

    Loads history, builds system prompt (using coach_key override if provided),
    calls Claude, applies XML side effects (<plan>, <remember>, <garmin_sync/>),
    persists ConversationMessage rows with the given source, and returns the
    cleaned response text. Does NOT send any Slack DM.
    """
    # Temporarily override athlete.coach_key for system prompt assembly if requested
    original_coach_key = athlete.coach_key
    if coach_key:
        athlete.coach_key = coach_key

    try:
        return _process_message_inner(athlete, user_text, db_session, source=source)
    finally:
        # Restore original — ephemeral override, do not persist to DB
        athlete.coach_key = original_coach_key


def _process_message_inner(
    athlete: Athlete,
    text: str,
    db_session: Session,
    source: str = "slack",
) -> str:
    """Internal implementation of process_message after coach_key is set."""
    # Load conversation history (last 30 messages)
    history = (
        scoped_query(db_session, ConversationMessage, athlete.id)
        .order_by(ConversationMessage.created_at.desc())
        .limit(30)
        .all()
    )
    # Reverse to chronological order
    history = list(reversed(history))

    return _handle_message_core(athlete, text, history, db_session, source=source)


def handle_message(athlete: Athlete, text: str, db_session: Session) -> str:
    """Handle a coaching conversation message (Slack entry point).

    Thin wrapper around process_message() that always uses source="slack".
    1. Load conversation history
    2. Build system prompt
    3. Call Claude
    4. Extract and apply <plan> mutations
    5. Extract and save <remember> memories
    6. Persist messages
    7. Return cleaned reply
    """
    return process_message(athlete, text, db_session, source="slack")


def _handle_message_core(
    athlete: Athlete,
    text: str,
    history: list,
    db_session: Session,
    source: str = "slack",
) -> str:
    """Core message processing logic shared by handle_message and process_message."""
    # Build messages for Claude
    messages = [{"role": msg.role, "content": msg.content} for msg in history]
    messages.append({"role": "user", "content": text})

    # Build detailed run telemetry context for data-driven Q&A
    RUNNING_TYPES_CTX = {
        "running", "trail_running", "treadmill_running", "track_running",
        "ultra_running", "virtual_run", "obstacle_run",
    }
    spotlight_analyses: list[str] = []
    athlete_max_hr, max_hr_run_count = _resolve_athlete_max_hr(athlete.id, db_session)

    # Always include the most recent run with telemetry
    latest_run = (
        scoped_query(db_session, CompletedWorkout, athlete.id)
        .join(WorkoutTelemetry, WorkoutTelemetry.completed_workout_id == CompletedWorkout.id)
        .filter(CompletedWorkout.activity_type.in_(RUNNING_TYPES_CTX))
        .order_by(CompletedWorkout.date.desc())
        .first()
    )
    if latest_run:
        analysis = _format_run_analysis_for_context(latest_run, athlete, athlete_max_hr, max_hr_run_count)
        if analysis:
            spotlight_analyses.append(analysis)

    # On-demand: also include any specific past run the user refers to by date/day
    latest_id = latest_run.id if latest_run else None
    referenced_run = _detect_referenced_workout(text, athlete.id, latest_id, db_session)
    if referenced_run:
        ref_analysis = _format_run_analysis_for_context(referenced_run, athlete, athlete_max_hr, max_hr_run_count)
        if ref_analysis:
            spotlight_analyses.append(ref_analysis)

    # Build system prompt
    system_prompt = build_system_prompt(athlete, db_session, spotlight_analyses=spotlight_analyses or None)

    # Token budget management — if prompt is very large, reduce windows
    estimated_chars = len(system_prompt) + sum(len(m["content"]) for m in messages)
    if estimated_chars > 150000:
        # Reduce conversation history
        messages = messages[-20:]
    if estimated_chars > 180000:
        messages = messages[-15:]

    # Call Claude — use a large token budget so bulk plan changes (80+ sessions)
    # are never truncated mid-JSON. A 20-week plan needs ~8k tokens for plan data alone.
    try:
        response = call_claude(system_prompt, messages, max_tokens=16384)
    except TimeoutError as e:
        logger.warning("Claude API timed out for athlete %d: %s", athlete.id, e)
        return "Give me a moment — I'm having trouble reaching my notes right now. Try again in a few seconds."
    except Exception as e:
        logger.error("Claude API call failed for athlete %d: %s", athlete.id, e)
        return "Lost my train of thought — give me a second and send that again."

    # Handle <garmin_fetch/> FIRST — if new data is ingested, re-call Claude
    # with a refreshed system prompt so the response is grounded in real data.
    if _GARMIN_FETCH_RE.search(response):
        new_data = _handle_garmin_fetch(athlete, db_session)
        if new_data:
            fresh_latest = (
                scoped_query(db_session, CompletedWorkout, athlete.id)
                .join(WorkoutTelemetry, WorkoutTelemetry.completed_workout_id == CompletedWorkout.id)
                .filter(CompletedWorkout.activity_type.in_(RUNNING_TYPES_CTX))
                .order_by(CompletedWorkout.date.desc())
                .first()
            )
            fresh_analyses: list[str] = []
            if fresh_latest:
                a = _format_run_analysis_for_context(fresh_latest, athlete, athlete_max_hr, max_hr_run_count)
                if a:
                    fresh_analyses.append(a)
            fresh_system_prompt = build_system_prompt(
                athlete, db_session, spotlight_analyses=fresh_analyses or None
            )
            try:
                response = call_claude(fresh_system_prompt, messages, max_tokens=16384)
            except Exception as e:
                logger.error("Re-call after garmin_fetch failed for athlete %d: %s", athlete.id, e)
                response = _GARMIN_FETCH_RE.sub("", response).strip()
        else:
            response = _GARMIN_FETCH_RE.sub("", response).strip()
            response += (
                "\n\n_(I checked Garmin but no new activities have synced yet — "
                "give it a minute and try again, or do a manual sync in the Garmin app.)_"
            )

    # Extract tags and apply side effects.
    # Check for <plan> blocks BEFORE stripping so extract_and_sync_garmin knows
    # the plan handler already synced affected weeks — preventing a double-upload.
    had_plan_block = bool(re.search(r"<plan>", response, re.DOTALL))
    response = extract_and_apply_plan(athlete.id, response, db_session)
    _reconcile_cancelled_garmin_workouts(athlete.id, db_session)
    response, sync_note = extract_and_sync_garmin(
        athlete.id, response, db_session, plan_already_synced=had_plan_block
    )
    response = extract_and_save_memories(athlete.id, response, db_session)
    response = extract_prescription_switch(athlete, response, db_session)
    response = extract_coach_switch(athlete, response, db_session)
    if sync_note:
        response = response + sync_note

    # Persist conversation messages with source tagging
    user_msg = ConversationMessage(
        athlete_id=athlete.id,
        role="user",
        content=text,
        source=source,
    )
    assistant_msg = ConversationMessage(
        athlete_id=athlete.id,
        role="assistant",
        content=response,
        source=source,
    )
    db_session.add(user_msg)
    db_session.add(assistant_msg)
    db_session.commit()

    return response
