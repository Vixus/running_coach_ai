"""Training plan generation — skeleton from Claude, expansion in Python."""

import json
import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from running_coach_ai.coach.persona import (
    COACH_PERSONA, call_claude,
    format_miles, format_pace_mi, mi_to_km, km_to_mi, round_to_5,
)
from running_coach_ai.database.models import (
    Athlete,
    Goal,
    PlannedWorkout,
    TrainingPlan,
)

logger = logging.getLogger(__name__)

# Phase 1 prompt: ask for a compact skeleton only (~2000 chars response).
# Distances are in miles; we convert to km internally.
SKELETON_PROMPT = """Generate a training plan skeleton for this runner.

ATHLETE: {name}, age {age}, {experience_level}
RACE: {race_type} on {race_date}, target {target_time}
CURRENT: {current_mi} mi/week, {training_days} days/week
PLAN: {num_weeks} weeks from {start_date}

Return ONLY a JSON array, no markdown fences, no commentary. Each element:
{{"w":1,"phase":"base","mi":25,"days":["easy","easy","tempo","easy","long_run"]}}

Rules:
- Phases: base | build | peak | taper
- Day types: easy | long_run | tempo | intervals | strides | cross_train
- Exactly {training_days} days per week
- Progressive overload ≤10%/week
- Start at {current_mi} mi/week (don't jump above current fitness)
- 2-3 week taper for marathon, 1-2 for shorter races
- ~80% easy volume, ~20% quality
- Introduce quality sessions gradually in base phase
- Minimum 3 miles per easy/strides session — never schedule a run under 30 minutes; if current weekly mileage is too low to support {training_days} days at that minimum, use fewer days rather than shorter runs"""


# Day-of-week slots per training_days (0 = Mon, 6 = Sun)
_TRAINING_SLOTS = {
    3: [1, 3, 6],              # Tue, Thu, Sun
    4: [0, 2, 4, 6],           # Mon, Wed, Fri, Sun
    5: [0, 1, 3, 5, 6],        # Mon, Tue, Thu, Sat, Sun
    6: [0, 1, 2, 3, 5, 6],     # Mon–Thu, Sat, Sun
    7: [0, 1, 2, 3, 4, 5, 6],
}


def _format_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}:{m:02d}"


def _race_distance_km(race_type: str) -> float:
    return {"marathon": 42.195, "half_marathon": 21.0975,
            "10k": 10.0, "5k": 5.0}.get(race_type, 42.195)


def _extract_json(text: str) -> str:
    """Extract the first JSON array or object from text, ignoring surrounding prose."""
    for open_ch, close_ch in [("[", "]"), ("{", "}")]:
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    extracted = text[start:i + 1]
                    # Fix trailing commas (Claude's most common JSON mistake)
                    extracted = re.sub(r",\s*([}\]])", r"\1", extracted)
                    return extracted
    return text.strip()


def _pace_str(pace_min_per_km: float) -> str:
    """Format a pace as min/mi (our display unit)."""
    return format_pace_mi(pace_min_per_km)


def _interval_structure(phase: str, week_num: int, total_weeks: int,
                        race_pace: float, easy_pace: float) -> tuple[dict, str]:
    """Generate interval session using 200m-increment track distances, progressing by phase.

    Distances: 200m, 400m, 600m, 800m, 1000m, 1200m, 1600m, 2000m (all 200m multiples).
    Rest times are whole minutes or clean seconds (60s, 90s, 2 min).
    """
    phase_progress = week_num / max(total_weeks, 1)

    if phase == "base":
        # Short aerobic intervals — build from 400m to 800m across the base phase
        if phase_progress < 0.4:
            reps, dist_m, rest_sec = 8, 400, 90
        elif phase_progress < 0.7:
            reps, dist_m, rest_sec = 6, 600, 90
        else:
            reps, dist_m, rest_sec = 6, 800, 120
        interval_pace = round(race_pace - 0.25, 1)

    elif phase == "build":
        # VO2max intervals — 800m to 1200m
        if phase_progress < 0.4:
            reps, dist_m, rest_sec = 6, 800, 90
        elif phase_progress < 0.7:
            reps, dist_m, rest_sec = 5, 1000, 90
        else:
            reps, dist_m, rest_sec = 5, 1200, 90
        interval_pace = round(race_pace - 0.35, 1)

    elif phase == "peak":
        # Race-specific — 1000m to 1600m, less rest
        if phase_progress < 0.5:
            reps, dist_m, rest_sec = 5, 1000, 60
        else:
            reps, dist_m, rest_sec = 4, 1600, 60
        interval_pace = round(race_pace - 0.4, 1)

    else:  # taper — brief tune-up, full recovery
        reps, dist_m, rest_sec = 4, 400, 90
        interval_pace = round(race_pace - 0.25, 1)

    rest_min = rest_sec / 60  # stored as decimal minutes for Garmin

    # Format rest cleanly for display
    rest_label = f"{rest_sec}s" if rest_sec < 120 else f"{rest_sec // 60} min"

    structure = {
        "steps": [
            {"type": "warmup", "duration_min": 15, "pace_min_per_km": easy_pace},
            {"type": "interval", "reps": reps, "distance_m": dist_m,
             "pace_min_per_km": interval_pace, "recovery_min": rest_min},
            {"type": "cooldown", "duration_min": 10, "pace_min_per_km": easy_pace},
        ]
    }
    description = (
        f"15 min warm-up, {reps}×{dist_m}m @ {_pace_str(interval_pace)} "
        f"with {rest_label} jog recovery, 10 min cool-down"
    )
    return structure, description


def _tempo_structure(phase: str, week_num: int, total_weeks: int,
                     race_pace: float, easy_pace: float) -> tuple[dict, str]:
    """Generate tempo session structure and description based on phase/progression."""
    phase_progress = week_num / max(total_weeks, 1)
    tempo_pace = round(race_pace + 0.1, 1)

    if phase == "base":
        # Broken tempo: 2 reps with rest
        rep_min = round_to_5(10 + phase_progress * 10)
        structure = {
            "steps": [
                {"type": "warmup", "duration_min": 10, "pace_min_per_km": easy_pace},
                {"type": "interval", "reps": 2, "duration_min": rep_min,
                 "pace_min_per_km": tempo_pace, "recovery_min": 3},
                {"type": "cooldown", "duration_min": 10, "pace_min_per_km": easy_pace},
            ]
        }
        description = (
            f"10 min warm-up, 2×{rep_min} min @ {_pace_str(tempo_pace)} "
            f"with 3 min recovery, 10 min cool-down"
        )
    elif phase in ("build", "peak"):
        # Continuous tempo, progressing from 20→45 min
        duration = round_to_5(min(20 + phase_progress * 35, 45))
        structure = {
            "steps": [
                {"type": "warmup", "duration_min": 10, "pace_min_per_km": easy_pace},
                {"type": "tempo", "duration_min": duration, "pace_min_per_km": tempo_pace},
                {"type": "cooldown", "duration_min": 10, "pace_min_per_km": easy_pace},
            ]
        }
        description = (
            f"10 min warm-up, {duration} min continuous tempo @ "
            f"{_pace_str(tempo_pace)}, 10 min cool-down"
        )
    else:  # taper
        structure = {
            "steps": [
                {"type": "warmup", "duration_min": 10, "pace_min_per_km": easy_pace},
                {"type": "tempo", "duration_min": 20, "pace_min_per_km": tempo_pace},
                {"type": "cooldown", "duration_min": 10, "pace_min_per_km": easy_pace},
            ]
        }
        description = f"10 min warm-up, 20 min tempo @ {_pace_str(tempo_pace)}, 10 min cool-down"

    return structure, description


def _distribute_km(total_km: float, day_types: list[str]) -> list[float]:
    """Distribute weekly km across sessions by type."""
    fractions = {
        "long_run": 0.30,
        "tempo": 0.15,
        "intervals": 0.13,
        "strides": 0.0,    # strides go at the end of an easy-distance run
        "cross_train": 0.0,
    }

    distances = [total_km * fractions.get(dt, 0) for dt in day_types]

    # Remaining km goes to easy-like sessions
    remaining = max(0, total_km - sum(distances))
    easy_idx = [i for i, dt in enumerate(day_types) if dt in ("easy", "strides")]
    if easy_idx:
        per_easy = remaining / len(easy_idx)
        for i in easy_idx:
            distances[i] = per_easy

    return [round(d, 1) for d in distances]


def _expand_skeleton(skeleton: list[dict], start_date: date, goal: Goal,
                     training_days: int, prescription_style: str | None = None) -> list[dict]:
    """Turn a compact skeleton into full week/day plan structure.

    Quality sessions (intervals, tempo) get full step-by-step structures and
    human-readable descriptions that progress across the plan.
    """
    race_km = _race_distance_km(goal.race_type)
    race_pace = goal.target_time_seconds / (race_km * 60)  # min/km
    easy_pace = round(race_pace + 1.2, 1)
    long_pace = round(race_pace + 1.0, 1)
    effective_style = prescription_style or "distance"

    total_weeks = len(skeleton)
    slots = _TRAINING_SLOTS.get(training_days, _TRAINING_SLOTS[5])

    # Align to Monday
    week_start = start_date - timedelta(days=start_date.weekday())

    weeks = []
    for entry in skeleton:
        week_num = entry.get("w", len(weeks) + 1)
        phase = entry.get("phase", "base")
        day_types = entry.get("days", [])[:training_days]
        # Skeleton uses miles; convert to km for internal storage
        week_mi = float(entry.get("mi", 0) or entry.get("km", 0) * km_to_mi(1) or 0)
        week_km = mi_to_km(week_mi)
        distances = _distribute_km(week_km, day_types)

        # Move long_run to the last slot (Sunday)
        types_ordered = list(day_types)
        dists_ordered = list(distances)
        if "long_run" in types_ordered:
            idx = types_ordered.index("long_run")
            types_ordered.append(types_ordered.pop(idx))
            dists_ordered.append(dists_ordered.pop(idx))

        days = []
        for i, (dtype, dist) in enumerate(zip(types_ordered, dists_ordered)):
            slot = slots[i] if i < len(slots) else i
            day_date = week_start + timedelta(days=slot)
            dur_secs = None

            # Build structured workouts for quality sessions
            if dtype == "intervals":
                target_zones, description = _interval_structure(
                    phase, week_num, total_weeks, race_pace, easy_pace)
                pace = round(race_pace - 0.3, 1)
            elif dtype == "tempo":
                target_zones, description = _tempo_structure(
                    phase, week_num, total_weeks, race_pace, easy_pace)
                pace = round(race_pace + 0.1, 1)
            elif dtype == "long_run":
                target_zones = None
                pace = long_pace
                if effective_style == "time":
                    dur_secs = max(300, round(dist * pace * 60 / 300) * 300)
                    description = f"{dur_secs // 60} min long run, easy effort, zone 1-2 throughout"
                    dist = 0
                else:
                    miles = max(1, round(km_to_mi(dist)))
                    dist = mi_to_km(miles)
                    description = f"{miles} mile long run, easy effort, zone 1-2 throughout"
            elif dtype == "strides":
                target_zones = None
                min_dist_km = 30.0 / easy_pace if easy_pace else 0
                dist = round(max(dist, min_dist_km), 1)
                duration = round_to_5(dist * easy_pace) if dist and easy_pace else 40
                pace = easy_pace
                if effective_style == "time":
                    dur_secs = duration * 60
                    description = f"{duration} min easy finishing with 6×100m strides"
                    dist = 0
                else:
                    miles = max(1, round(km_to_mi(dist)))
                    dist = mi_to_km(miles)
                    description = f"{miles} mile easy finishing with 6×100m strides"
            elif dtype == "cross_train":
                target_zones = None
                description = "Cross-training — swim, bike, or yoga. Low impact, active recovery."
                pace = None
                dist = 0
                dur_secs = 2700  # 45 min, always time-based
            else:  # easy
                target_zones = None
                min_dist_km = 30.0 / easy_pace if easy_pace else 0
                dist = round(max(dist, min_dist_km), 1)
                duration = round_to_5(dist * easy_pace) if dist and easy_pace else 45
                pace = easy_pace
                if effective_style == "time":
                    dur_secs = duration * 60
                    description = f"{duration} min easy, conversational pace, zone 1-2"
                    dist = 0
                else:
                    miles = max(1, round(km_to_mi(dist)))
                    dist = mi_to_km(miles)
                    description = f"{miles} mile easy, conversational pace, zone 1-2"

            days.append({
                "date": day_date.isoformat(),
                "type": dtype,
                "distance_km": dist if dist else None,
                "duration_seconds": dur_secs,
                "pace_min_per_km": pace,
                "description": description,
                "target_zones_json": target_zones,
            })

        weeks.append({
            "week_number": week_num,
            "phase": phase,
            "start_date": week_start.isoformat(),
            "target_km": week_km,   # internal storage in km
            "target_mi": round(week_mi, 1),
            "days": days,
        })
        week_start += timedelta(weeks=1)

    return weeks


def generate_plan(athlete: Athlete, goal: Goal, db_session: Session) -> TrainingPlan:
    """Generate a full training plan: skeleton from Claude, details from Python.

    Phase 1 — Claude returns a compact skeleton (phase/km/session-types per week).
    Phase 2 — Python expands it into dated workouts with distances, paces, and descriptions.
    """
    today = date.today()

    # Check for conflicting active goals (race dates within 4 weeks of each other).
    # If a conflict exists, deactivate the older goal so there is always exactly one
    # active race goal per athlete per training window.
    other_active_goals = (
        db_session.query(Goal)
        .filter(
            Goal.athlete_id == athlete.id,
            Goal.active == True,  # noqa: E712
            Goal.id != goal.id,
        )
        .all()
    )
    for other in other_active_goals:
        delta_days = abs((goal.race_date - other.race_date).days)
        if delta_days < 28:
            logger.warning(
                "Multi-goal conflict: athlete %d has two active goals with race dates "
                "%d days apart (goal %d on %s vs goal %d on %s). Deactivating older goal %d.",
                athlete.id,
                delta_days,
                goal.id,
                goal.race_date,
                other.id,
                other.race_date,
                other.id,
            )
            other.active = False
            db_session.flush()

    total_weeks = max(1, (goal.race_date - today).days // 7)

    current_mi = round(km_to_mi(goal.current_weekly_mileage_km or 0), 1)
    prompt = SKELETON_PROMPT.format(
        name=athlete.name,
        age=athlete.age,
        race_type=goal.race_type,
        race_date=goal.race_date.isoformat(),
        target_time=_format_time(goal.target_time_seconds),
        current_mi=current_mi,
        experience_level=goal.experience_level,
        training_days=goal.training_days_per_week,
        num_weeks=total_weeks,
        start_date=today.isoformat(),
    )

    response_text = call_claude(COACH_PERSONA,
                                [{"role": "user", "content": prompt}],
                                max_tokens=4096)
    cleaned = _extract_json(response_text)
    skeleton = json.loads(cleaned)

    # Handle both bare array and {"weeks": [...]} wrapper
    if isinstance(skeleton, dict):
        skeleton = skeleton.get("weeks", skeleton.get("plan", []))
    if not skeleton:
        raise ValueError("Claude returned an empty plan skeleton")

    logger.info("Received %d-week skeleton from Claude for athlete %d",
                len(skeleton), athlete.id)

    # Expand into full plan
    all_weeks = _expand_skeleton(skeleton, today, goal, goal.training_days_per_week,
                                  prescription_style=athlete.prescription_style)
    plan_data = {"weeks": all_weeks}

    # Create TrainingPlan row
    training_plan = TrainingPlan(
        athlete_id=athlete.id,
        goal_id=goal.id,
        valid_from=date.fromisoformat(all_weeks[0]["start_date"]),
        valid_to=goal.race_date,
        plan_json=plan_data,
        current_phase=all_weeks[0]["phase"],
        active=True,
    )
    db_session.add(training_plan)
    db_session.flush()

    # Create PlannedWorkout rows
    for week in all_weeks:
        for day in week["days"]:
            db_session.add(PlannedWorkout(
                plan_id=training_plan.id,
                athlete_id=athlete.id,
                scheduled_date=date.fromisoformat(day["date"]),
                workout_type=day["type"],
                description=day.get("description"),
                target_distance_km=day.get("distance_km"),
                target_duration_seconds=day.get("duration_seconds"),
                target_pace_min_per_km=day.get("pace_min_per_km"),
                target_zones_json=day.get("target_zones_json"),
                status="planned",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            ))

    db_session.commit()
    logger.info("Generated %d-week training plan (id=%d) for athlete %d",
                len(all_weeks), training_plan.id, athlete.id)
    return training_plan


def aggregate_week(athlete_id: int, week_start_date: date, db_session: Session) -> dict:
    """Aggregate planned vs completed workouts for a given week.

    Returns a summary dict used by weekly review and plan adaptation.
    """
    from running_coach_ai.database.models import CompletedWorkout, HealthSnapshot

    week_end_date = week_start_date + timedelta(days=6)

    planned = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete_id,
            PlannedWorkout.scheduled_date >= week_start_date,
            PlannedWorkout.scheduled_date <= week_end_date,
        )
        .all()
    )

    completed = (
        db_session.query(CompletedWorkout)
        .filter(
            CompletedWorkout.athlete_id == athlete_id,
            CompletedWorkout.date >= week_start_date,
            CompletedWorkout.date <= week_end_date,
        )
        .all()
    )

    health_snapshots = (
        db_session.query(HealthSnapshot)
        .filter(
            HealthSnapshot.athlete_id == athlete_id,
            HealthSnapshot.date >= week_start_date,
            HealthSnapshot.date <= week_end_date,
        )
        .all()
    )

    planned_km = sum(w.target_distance_km or 0 for w in planned)
    actual_km = sum(w.distance_km or 0 for w in completed)
    quality_planned = sum(1 for w in planned if w.workout_type in ("tempo", "intervals"))
    quality_completed = sum(
        1 for w in completed
        if any(
            p.workout_type in ("tempo", "intervals")
            for p in planned
            if p.id == w.planned_workout_id
        )
    )
    total_load = sum(w.training_load or 0 for w in completed)
    vo2max_values = [w.vo2max_estimate for w in completed if w.vo2max_estimate]

    return {
        "week_start": week_start_date.isoformat(),
        "planned_sessions": len(planned),
        "completed_sessions": len(completed),
        "completion_pct": (len(completed) / len(planned) * 100) if planned else 0,
        "planned_km": round(planned_km, 1),
        "actual_km": round(actual_km, 1),
        "quality_sessions_planned": quality_planned,
        "quality_sessions_completed": quality_completed,
        "total_training_load": round(total_load, 1),
        "avg_vo2max": round(sum(vo2max_values) / len(vo2max_values), 1) if vo2max_values else None,
        "avg_hrv": round(
            sum(h.hrv_score for h in health_snapshots if h.hrv_score) /
            max(1, sum(1 for h in health_snapshots if h.hrv_score)),
            1,
        ) if any(h.hrv_score for h in health_snapshots) else None,
        "avg_sleep_score": round(
            sum(h.sleep_score for h in health_snapshots if h.sleep_score) /
            max(1, sum(1 for h in health_snapshots if h.sleep_score)),
            1,
        ) if any(h.sleep_score for h in health_snapshots) else None,
    }
