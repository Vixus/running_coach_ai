"""GET /api/today — Today Card data for the magazine dashboard.

Returns a single JSON object whose `state` field drives the cover treatment.
Five states are supported (per spec 007 FR-003):

    NO_PLAN   →  athlete has no active Goal
    RACE_DAY  →  today's PlannedWorkout.workout_type == "race"
    COMPLETED →  today's CompletedWorkout exists
    REST_DAY  →  today's PlannedWorkout.workout_type == "rest", OR
                 active Goal exists but no PlannedWorkout row for today
    PRE_RUN   →  default — there's a workout today and it hasn't been completed

Rationale text resolution ladder per FR-005/FR-006/FR-007:

    morning_checkin → coach_analysis → rule_based → placeholder (before 7am)
                                                  → persona_static (race/no-plan)

All "today" computation uses `athlete.timezone` (FR-002). Zero Claude calls
at request time (FR-004). State-transition WebEvents (FR-032a/b) are emitted
via a Flask after-request hook so the HTTP response is never blocked.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, g, jsonify, session
from sqlalchemy import desc

from running_coach_ai.coach.persona import format_pace_mi, km_to_mi
from running_coach_ai.coach.personas import get_persona
from running_coach_ai.coach.today_rationale import (
    extract_rationale_paragraph,
    rule_based_completed,
    rule_based_morning,
    rule_based_rest,
)
from running_coach_ai.database.models import (
    Athlete,
    CompletedWorkout,
    Goal,
    HealthSnapshot,
    Notification,
    PlannedWorkout,
    TrainingPlan,
    WebEvent,
)
from running_coach_ai.database.session import get_session, scoped_query
from running_coach_ai.web.auth import login_required

logger = logging.getLogger(__name__)

bp = Blueprint("today", __name__)

# ─── Workout-type labels (reused from magazine, kept local to avoid coupling) ─

_TYPE_LABEL = {
    "easy": "Easy",
    "long_run": "Long Run",
    "tempo": "Tempo",
    "interval": "Intervals",
    "intervals": "Intervals",
    "threshold": "Threshold",
    "recovery": "Recovery",
    "rest": "Rest",
    "strides": "Strides",
    "race": "Race",
    "cross_training": "Cross-Train",
    "cross_train": "Cross-Train",
    "strength": "Strength",
    "workout": "Workout",
}

# Static cues rendered in the rest-day cover strip (spec 007 rest-day spec
# 2026-05-19). Same three for every rest day in v1; can be made dynamic
# later without a contract change.
_REST_DAY_CUES = [
    {"label": "Sleep", "copy": "in bed early"},
    {"label": "Fuel",  "copy": "carbs + protein"},
    {"label": "Move",  "copy": "walk or mobility"},
]


def _athlete_today(athlete: Athlete) -> tuple[date, datetime]:
    """Return (today_date, now_dt) computed in the athlete's local timezone."""
    tz = ZoneInfo(athlete.timezone or "America/New_York")
    now_local = datetime.now(tz)
    return now_local.date(), now_local


def _format_today_pretty(today_local: date) -> str:
    """'Friday, May 15, 2026' — day suffix safe on Windows."""
    return f"{today_local.strftime('%A, %B')} {today_local.day}, {today_local.year}"


def _format_stale_date(snap_date: date | None) -> str | None:
    """Return 'May 20'-style label for a stale snapshot's date, or None.

    Windows-safe (no %-d) — uses `snap_date.day` to drop the leading zero.
    """
    if snap_date is None:
        return None
    return f"{snap_date.strftime('%b')} {snap_date.day}"


# ─── State resolver ────────────────────────────────────────────────────────


def _resolve_state(
    db,
    athlete_id: int,
    today_local: date,
) -> tuple[str, Goal | None, PlannedWorkout | None, CompletedWorkout | None]:
    """Return (state, goal, planned, completed) per FR-003 precedence.

    Reads minimal data for the resolution; per-state payload builders fetch
    anything additional they need.
    """
    goal = (
        scoped_query(db, Goal, athlete_id)
        .filter(Goal.active == True)  # noqa: E712 — sqlalchemy boolean comparison
        .first()
    )
    if goal is None:
        return "NO_PLAN", None, None, None

    planned = (
        scoped_query(db, PlannedWorkout, athlete_id)
        .filter(PlannedWorkout.scheduled_date == today_local)
        .order_by(PlannedWorkout.id.asc())
        .first()
    )
    completed = (
        scoped_query(db, CompletedWorkout, athlete_id)
        .filter(CompletedWorkout.date == today_local)
        .order_by(desc(CompletedWorkout.id))
        .first()
    )

    if planned is not None and planned.workout_type == "race":
        return "RACE_DAY", goal, planned, completed
    if completed is not None:
        return "COMPLETED", goal, planned, completed
    if planned is None or planned.workout_type == "rest":
        return "REST_DAY", goal, planned, completed
    return "PRE_RUN", goal, planned, completed


# ─── Health snapshot fallback (3-day window, matching magazine.py) ─────────


def _resolve_health_snapshot(
    db, athlete_id: int, today_local: date
) -> tuple[HealthSnapshot | None, bool]:
    """Return (snap, is_stale). Thin wrapper around the shared resolver."""
    from running_coach_ai.coach.health_lookup import resolve_recent_snapshot
    return resolve_recent_snapshot(db, athlete_id, today_local)


# ─── Rationale source ladder ───────────────────────────────────────────────


def _morning_checkin_paragraph(
    db, athlete_id: int, now_local: datetime
) -> tuple[str | None, str | None]:
    """Return (paragraph_text, created_at_iso) for today's morning_checkin.

    Only notifications whose timestamp falls within the athlete's local "today"
    (midnight-to-midnight in `now_local`'s tz) are eligible — yesterday's
    check-in is never surfaced under today's date.
    """
    day_start_local = datetime.combine(now_local.date(), time.min, tzinfo=now_local.tzinfo)
    day_start_utc = day_start_local.astimezone(timezone.utc).replace(tzinfo=None)
    day_end_utc = (day_start_local + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
    notif = (
        scoped_query(db, Notification, athlete_id)
        .filter(
            Notification.kind == "morning_checkin",
            Notification.created_at >= day_start_utc,
            Notification.created_at < day_end_utc,
        )
        .order_by(desc(Notification.created_at))
        .first()
    )
    if notif is None or not notif.body:
        return None, None
    paragraph = extract_rationale_paragraph(notif.body)
    if not paragraph:
        return None, None
    return paragraph, notif.created_at.isoformat() + "Z"


def _is_before_7am_local(now_local: datetime) -> bool:
    return now_local.time() < time(7, 0)


def _current_training_plan(db, goal: Goal | None) -> TrainingPlan | None:
    if goal is None:
        return None
    return (
        db.query(TrainingPlan)
        .filter(TrainingPlan.goal_id == goal.id, TrainingPlan.active == True)  # noqa: E712
        .first()
    )


def _plan_with_current_week(plan: TrainingPlan | None, today_local: date):
    """Return a lightweight wrapper exposing `current_phase` + `current_week` for the rationale helpers."""
    if plan is None:
        return None
    week = None
    if plan.valid_from:
        week = max(1, ((today_local - plan.valid_from).days // 7) + 1)

    class _PlanView:
        def __init__(self, phase, w):
            self.current_phase = phase
            self.current_week = w

    return _PlanView(plan.current_phase, week)


# ─── Per-state payload builders ────────────────────────────────────────────


def _accent(athlete: Athlete) -> str:
    return get_persona(athlete.coach_key).accent_color


def _coach_display_name(athlete: Athlete) -> str:
    return get_persona(athlete.coach_key).name


def _build_pre_run(
    db,
    athlete: Athlete,
    planned: PlannedWorkout,
    goal: Goal,
    today_local: date,
    now_local: datetime,
) -> dict:
    """PRE_RUN payload — FR-005, FR-013, FR-016, FR-022/023/024."""
    snap, is_stale = _resolve_health_snapshot(db, athlete.id, today_local)
    plan = _current_training_plan(db, goal)
    plan_view = _plan_with_current_week(plan, today_local)

    # Headline
    type_label = _TYPE_LABEL.get(planned.workout_type, planned.workout_type.title())
    title = _headline_for_workout(planned)
    subtitle = planned.description or None

    # Rationale ladder: morning_checkin → placeholder (before 7am) → rule_based
    paragraph, _ = _morning_checkin_paragraph(db, athlete.id, now_local)
    if paragraph:
        rationale_text = paragraph
        rationale_source = "morning_checkin"
    elif _is_before_7am_local(now_local):
        rationale_text = (
            "Coach is checking in soon — pull this up after 7am for today's call."
        )
        rationale_source = "placeholder"
    else:
        rationale_text = rule_based_morning(snap, planned, plan_view, goal, athlete.name)
        rationale_source = "rule_based"

    return {
        "state": "PRE_RUN",
        "headline": {
            "eyebrow":  type_label,
            "ribbon":   type_label,
            "title":    title,
            "subtitle": subtitle,
        },
        "rationale": {
            "text":         rationale_text,
            "source":       rationale_source,
            "coach":        _coach_display_name(athlete),
            "accent_color": _accent(athlete),
        },
        "modifiers": {
            "on_watch": bool(planned.garmin_workout_id),
            "is_bonus": False,
        },
        "cover_lines": _morning_cover_lines(snap, is_stale),
        "actions": {
            "headline_chat_prompt":  "Tell me about today's workout.",
            "rationale_chat_prompt": "I have a question about today's plan.",
            "cta": None,
        },
    }


def _build_completed(
    db,
    athlete: Athlete,
    planned: PlannedWorkout | None,
    completed: CompletedWorkout,
    goal: Goal,
    today_local: date,
) -> dict:
    """COMPLETED payload — FR-006, FR-014, FR-017."""
    is_bonus = completed.planned_workout_id is None

    # Headline
    if is_bonus:
        eyebrow = "Bonus Run"
        ribbon = "Bonus Run — not on plan"
        workout_type = "run"
    else:
        workout_type = planned.workout_type if planned else (
            completed.planned_workout.workout_type if completed.planned_workout else "run"
        )
        eyebrow = _TYPE_LABEL.get(workout_type, workout_type.title())
        ribbon = "Completed"

    mi = km_to_mi(completed.distance_km) if completed.distance_km else None
    pace_str = format_pace_mi(completed.avg_pace_min_per_km) if completed.avg_pace_min_per_km else None
    title_parts = []
    if mi is not None:
        title_parts.append(f"{mi:.1f} mi")
    if pace_str:
        title_parts.append(f"@ {pace_str}")
    title = " ".join(title_parts) if title_parts else "Today's run"
    subtitle = (
        f"{_TYPE_LABEL.get(workout_type, workout_type.title())} ✓" if not is_bonus else None
    )

    # Rationale ladder: coach_analysis → rule_based
    paragraph = extract_rationale_paragraph(completed.coach_analysis) if completed.coach_analysis else ""
    if paragraph:
        rationale_text = paragraph
        rationale_source = "coach_analysis"
    else:
        target_pace_str = (
            format_pace_mi(planned.target_pace_min_per_km)
            if planned and planned.target_pace_min_per_km
            else None
        )
        rationale_text = rule_based_completed(
            completed_distance_mi=mi,
            completed_pace_str=pace_str,
            target_pace_str=target_pace_str,
            workout_type=workout_type,
            athlete_name=athlete.name,
        )
        rationale_source = "rule_based"

    return {
        "state": "COMPLETED",
        "headline": {
            "eyebrow":  eyebrow,
            "ribbon":   ribbon,
            "title":    title,
            "subtitle": subtitle,
        },
        "rationale": {
            "text":         rationale_text,
            "source":       rationale_source,
            "coach":        _coach_display_name(athlete),
            "accent_color": _accent(athlete),
        },
        "modifiers": {
            "on_watch": False,
            "is_bonus": is_bonus,
        },
        "cover_lines": _completed_cover_lines(completed),
        "actions": {
            "headline_chat_prompt":  "How did today's run go?",
            "rationale_chat_prompt": "I have a follow-up on this run.",
            "cta": None,
        },
    }


def _build_rest_day(
    db,
    athlete: Athlete,
    goal: Goal,
    today_local: date,
    now_local: datetime,
) -> dict:
    """REST_DAY payload — fires for both explicit rest rows and zero-row days.

    Reuses the FR-005 rationale ladder (morning_checkin → placeholder before 7am
    → rule-based) but the rule-based fallback is rest-specific via
    `rule_based_rest` — never "run it as written" framing on a rest day.
    Cover lines are the 4-up morning readiness grid (FR-013) and the new
    `cues` field exposes a static recovery-tip strip.
    """
    snap, is_stale = _resolve_health_snapshot(db, athlete.id, today_local)
    plan = _current_training_plan(db, goal)
    plan_view = _plan_with_current_week(plan, today_local)

    paragraph, _ = _morning_checkin_paragraph(db, athlete.id, now_local)
    if paragraph:
        rationale_text = paragraph
        rationale_source = "morning_checkin"
    elif _is_before_7am_local(now_local):
        rationale_text = (
            "Coach is checking in soon — pull this up after 7am for today's call."
        )
        rationale_source = "placeholder"
    else:
        rationale_text = rule_based_rest(snap, plan_view, goal, athlete.name)
        rationale_source = "rule_based"

    return {
        "state": "REST_DAY",
        "headline": {
            "eyebrow":  "Rest Day",
            "ribbon":   "Rest Day",
            "title":    "Recovery is the workout",
            "subtitle": None,
        },
        "rationale": {
            "text":         rationale_text,
            "source":       rationale_source,
            "coach":        _coach_display_name(athlete),
            "accent_color": _accent(athlete),
        },
        "modifiers": {
            "on_watch": False,
            "is_bonus": False,
        },
        "cover_lines": _morning_cover_lines(snap, is_stale),
        "cues": list(_REST_DAY_CUES),
        "actions": {
            "headline_chat_prompt":  "How should I make the most of today's recovery?",
            "rationale_chat_prompt": "I have a question about today's plan.",
            "cta": None,
        },
    }


def _build_race_day(
    db, athlete: Athlete, planned: PlannedWorkout, goal: Goal
) -> dict:
    """RACE_DAY payload — FR-007 + FR-015."""
    persona = get_persona(athlete.coach_key)
    race_name = (goal.race_name or "Race Day") if goal else "Race Day"

    # Cover lines
    dist_mi = km_to_mi(planned.target_distance_km) if planned.target_distance_km else None
    goal_pace = (
        format_pace_mi(planned.target_pace_min_per_km)
        if planned.target_pace_min_per_km
        else None
    )
    hr_cap = round(athlete.lthr_bpm * 0.92) if athlete.lthr_bpm else None
    cover_lines = [
        {
            "label": "Distance",
            "value": f"{dist_mi:.1f} mi" if dist_mi else "—",
            "drill_to": None,
            "is_stale": False,
        },
        {
            "label": "Goal Pace",
            "value": goal_pace or "—",
            "drill_to": None,
            "is_stale": False,
        },
        {
            "label": "HR Cap",
            "value": hr_cap if hr_cap is not None else "—",
            "drill_to": None,
            "is_stale": False,
        },
        {
            "label": "Weather",
            "value": "—",   # FR-015: weather sourcing out of scope v1
            "drill_to": None,
            "is_stale": False,
        },
    ]

    return {
        "state": "RACE_DAY",
        "headline": {
            "eyebrow":  "Race Day",
            "ribbon":   "Race Day",
            "title":    race_name,
            "subtitle": None,
        },
        "rationale": {
            "text":         persona.race_morning_greeting or "Trust the work. Execute the plan, mile by mile.",
            "source":       "persona_static",
            "coach":        persona.name,
            "accent_color": persona.accent_color,
        },
        "modifiers": {
            "on_watch": bool(planned.garmin_workout_id),
            "is_bonus": False,
        },
        "cover_lines": cover_lines,
        "actions": {
            "headline_chat_prompt":  "Walk me through race execution.",
            "rationale_chat_prompt": "I have a question about today's plan.",
            "cta": None,
        },
    }


def _build_no_plan(athlete: Athlete) -> dict:
    """NO_PLAN payload — FR-007a + FR-012."""
    persona = get_persona(athlete.coach_key)
    return {
        "state": "NO_PLAN",
        "headline": {
            "eyebrow":  None,
            "ribbon":   None,
            "title":    "Ready to train for something?",
            "subtitle": None,
        },
        "rationale": {
            "text":         "Ready to train for something? Pick a race and your coach will build a plan.",
            "source":       "persona_static",
            "coach":        persona.name,
            "accent_color": persona.accent_color,
        },
        "modifiers": {
            "on_watch": False,
            "is_bonus": False,
        },
        "cover_lines": None,
        "actions": {
            "headline_chat_prompt":  None,
            "rationale_chat_prompt": None,
            "cta": {
                "label":       "Pick a race",
                "chat_prompt": "I want to train for…",
            },
        },
    }



# ─── Cover line builders ───────────────────────────────────────────────────


def _morning_cover_lines(snap: HealthSnapshot | None, is_stale: bool) -> list[dict]:
    """Four cover lines (HRV / Body Battery / Sleep / RHR) for PRE_RUN and REST_DAY."""
    hrv = snap.hrv_score if (snap and snap.hrv_score is not None) else "—"
    bb = snap.body_battery_end if (snap and snap.body_battery_end is not None) else (
        snap.body_battery_start if (snap and snap.body_battery_start is not None) else "—"
    )
    sleep_h = round(snap.sleep_duration_seconds / 3600.0, 1) if (snap and snap.sleep_duration_seconds) else "—"
    rhr = snap.resting_hr if (snap and snap.resting_hr is not None) else "—"
    stale_date = _format_stale_date(snap.date) if (snap is not None and is_stale) else None

    return [
        {"label": "HRV ms",   "value": hrv,     "drill_to": "morning", "is_stale": is_stale, "stale_date": stale_date},
        {"label": "Body Bat", "value": bb,      "drill_to": "morning", "is_stale": is_stale, "stale_date": stale_date},
        {"label": "Sleep h",  "value": sleep_h, "drill_to": "morning", "is_stale": is_stale, "stale_date": stale_date},
        {"label": "RHR",      "value": rhr,     "drill_to": "morning", "is_stale": is_stale, "stale_date": stale_date},
    ]


def _completed_cover_lines(completed: CompletedWorkout) -> list[dict]:
    """Distance / Pace / Avg HR / Training Load — sourced from CompletedWorkout."""
    mi = km_to_mi(completed.distance_km) if completed.distance_km else None
    pace = format_pace_mi(completed.avg_pace_min_per_km) if completed.avg_pace_min_per_km else None
    return [
        {
            "label": "Distance",
            "value": f"{mi:.1f} mi" if mi is not None else "—",
            "drill_to": "last_run",
            "is_stale": False,
        },
        {
            "label": "Pace",
            "value": pace or "—",
            "drill_to": "last_run",
            "is_stale": False,
        },
        {
            "label": "Avg HR",
            "value": completed.avg_hr if completed.avg_hr is not None else "—",
            "drill_to": "last_run",
            "is_stale": False,
        },
        {
            "label": "Training Load",
            "value": round(completed.training_load) if completed.training_load else "—",
            "drill_to": "last_run",
            "is_stale": False,
        },
    ]


# ─── Headline helper ───────────────────────────────────────────────────────


def _headline_for_workout(planned: PlannedWorkout) -> str:
    """Workout headline text: '6 mi @ 7:45' or '45 min easy' depending on prescription."""
    pace_str = (
        format_pace_mi(planned.target_pace_min_per_km)
        if planned.target_pace_min_per_km
        else None
    )
    if planned.target_distance_km:
        mi = km_to_mi(planned.target_distance_km)
        mi_part = f"{mi:.1f} mi" if mi % 1 else f"{int(mi)} mi"
        return f"{mi_part} @ {pace_str}" if pace_str else mi_part
    if planned.target_duration_seconds:
        minutes = planned.target_duration_seconds // 60
        type_label = _TYPE_LABEL.get(planned.workout_type, planned.workout_type.title())
        return f"{minutes} min {type_label.lower()}"
    return _TYPE_LABEL.get(planned.workout_type, planned.workout_type.title())


# ─── State-transition WebEvent emission ────────────────────────────────────


def _emit_state_transition_event(
    athlete_id: int, from_state: str | None, to_state: str
) -> None:
    """Write a today.state_transition WebEvent row.

    Uses a fresh DB session so the insert is independent of the request
    session lifecycle (the request session may have already been closed
    by the time the after_request hook fires).
    """
    try:
        with get_session() as db:
            db.add(
                WebEvent(
                    timestamp=datetime.utcnow(),
                    severity="info",
                    category="today",
                    message=f"{from_state or 'NONE'} → {to_state}",
                    athlete_id=athlete_id,
                    details_json={
                        "kind": "today.state_transition",
                        "from_state": from_state,
                        "to_state": to_state,
                        "athlete_id": athlete_id,
                    },
                )
            )
    except Exception:
        logger.debug("Failed to emit today.state_transition WebEvent", exc_info=True)


def _last_recorded_state(db, athlete_id: int) -> str | None:
    """Look up the most recent state from prior today.state_transition events."""
    prior = (
        db.query(WebEvent)
        .filter(
            WebEvent.athlete_id == athlete_id,
            WebEvent.category == "today",
        )
        .order_by(desc(WebEvent.timestamp))
        .first()
    )
    if prior is None or not prior.details_json:
        return None
    return prior.details_json.get("to_state")


# ─── Endpoint ──────────────────────────────────────────────────────────────


@bp.route("/api/today")
@login_required
def today():
    athlete_id = session["athlete_id"]

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if athlete is None:
            return jsonify({"error": "Athlete not found"}), 404

        today_local, now_local = _athlete_today(athlete)
        state, goal, planned, completed = _resolve_state(db, athlete_id, today_local)

        if state == "NO_PLAN":
            payload = _build_no_plan(athlete)
        elif state == "RACE_DAY":
            payload = _build_race_day(db, athlete, planned, goal)
        elif state == "COMPLETED":
            payload = _build_completed(db, athlete, planned, completed, goal, today_local)
        elif state == "REST_DAY":
            payload = _build_rest_day(db, athlete, goal, today_local, now_local)
        else:  # PRE_RUN
            payload = _build_pre_run(db, athlete, planned, goal, today_local, now_local)

        # Wrap with common envelope fields
        payload["today_iso"] = today_local.isoformat()
        payload["today_pretty"] = _format_today_pretty(today_local)
        payload["athlete"] = {"id": athlete.id, "name": athlete.name}

        # Stash for after-request hook: emit a WebEvent only on a transition.
        prior_state = _last_recorded_state(db, athlete_id)
        if prior_state != state:
            g._today_state_transition = (athlete_id, prior_state, state)

        return jsonify(payload)


@bp.after_request
def _emit_transition_after_response(response):
    """Fire a today.state_transition WebEvent after the response is built.

    Using `after_request` keeps the WebEvent write off the latency-critical
    path (FR-032b). The state diff was computed inside the handler and
    stashed on `flask.g`.
    """
    transition = getattr(g, "_today_state_transition", None)
    if transition is not None:
        athlete_id, from_state, to_state = transition
        _emit_state_transition_event(athlete_id, from_state, to_state)
    return response
