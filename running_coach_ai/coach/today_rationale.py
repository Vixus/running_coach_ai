"""Rule-based rationale templates + body parser for the Today Card.

These are pure functions (no DB, no Garmin, no Claude). They are called by
`running_coach_ai/web/api/today.py` when the Claude-written rationale
sources (morning_checkin notification body, post-run coach_analysis) are
missing or empty.

Per spec 007 (FR-008/008a/008b/008c, FR-011):
- `extract_rationale_paragraph()` pulls the first paragraph out of a
  Claude-generated body (used for both morning_checkin and coach_analysis
  sources).
- `rule_based_morning()` returns a single short paragraph in coach voice,
  always weaving in the "why of the run" (workout type + periodization
  context) regardless of whether overnight watch data is available. Two
  branches: with-snapshot (~6 templates) and no-snapshot (run-by-feel).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — type-only imports
    from running_coach_ai.database.models import (
        Goal,
        HealthSnapshot,
        PlannedWorkout,
        TrainingPlan,
    )


# ─── Paragraph extraction ──────────────────────────────────────────────────


def extract_rationale_paragraph(body: str | None) -> str:
    """Return the first paragraph of a coach-generated body.

    A "paragraph" is whatever appears before the first blank line. If the
    body has no blank line, the whole stripped body is returned. Empty or
    None inputs return an empty string.

    Used for both `Notification(kind="morning_checkin").body` and
    `CompletedWorkout.coach_analysis` per FR-011. The athlete-centered
    prompt directive (FR-009/FR-010) instructs Claude to lead with the
    rationale paragraph, so the first paragraph is the right unit.
    """
    if not body:
        return ""
    stripped = body.strip()
    if not stripped:
        return ""
    # Split on the first blank line (one or more newlines surrounded by
    # optional whitespace). Take the head.
    paragraphs = re.split(r"\n\s*\n", stripped, maxsplit=1)
    return paragraphs[0].strip()


# ─── Workout-type labels for rationale prose ───────────────────────────────

_WORKOUT_LABEL = {
    "easy": "easy run",
    "long_run": "long run",
    "tempo": "tempo",
    "interval": "interval session",
    "intervals": "interval session",
    "threshold": "threshold session",
    "strides": "strides",
    "recovery": "recovery run",
    "rest": "rest day",
    "race": "race",
    "cross_training": "cross-training",
    "cross_train": "cross-training",
    "strength": "strength session",
    "workout": "workout",
}


def _workout_label(workout_type: str | None) -> str:
    if not workout_type:
        return "session"
    return _WORKOUT_LABEL.get(workout_type, workout_type.replace("_", " "))


# ─── By-feel cues keyed by workout type ────────────────────────────────────

_BY_FEEL_CUE = {
    "easy": (
        "If your legs feel fresh, run conversational; if they're heavy, dial "
        "back to a walk-jog and protect tomorrow."
    ),
    "long_run": (
        "If you're sluggish in the first 20 minutes, give yourself permission "
        "to cut it short. The endurance is already banked — don't dig a hole."
    ),
    "tempo": (
        "If your legs are heavy, hold the easy end of the pace range; if "
        "you're springy, run the target paces — never go past them today."
    ),
    "interval": (
        "If reps one and two feel ragged, cut the set in half and call it. "
        "Crisp at the top beats grinding at the bottom."
    ),
    "intervals": (
        "If reps one and two feel ragged, cut the set in half and call it. "
        "Crisp at the top beats grinding at the bottom."
    ),
    "threshold": (
        "If you can't hold target pace at controlled breathing, drop 10 sec "
        "per mile and treat it as steady-state work."
    ),
    "strides": (
        "Run them on feel — fast and relaxed for 20 seconds, fully recovered "
        "between. Skip a few reps if your form gets sloppy."
    ),
    "recovery": (
        "Keep it truly easy — if you're checking your watch for pace, it's "
        "already too fast. Today is for blood flow, nothing else."
    ),
    "rest": (
        "Sleep, eat, walk if you want to move. The work is letting the body "
        "absorb the load."
    ),
    "cross_training": (
        "Pick something low-impact and easy. Aim for steady aerobic, not "
        "intervals."
    ),
    "race": (
        "Trust the work. Execute the plan mile by mile."
    ),
}


def _by_feel_cue(workout_type: str | None) -> str:
    if not workout_type:
        return (
            "Run on feel today — if anything tightens up, dial it back. "
            "Consistency over heroics."
        )
    return _BY_FEEL_CUE.get(
        workout_type,
        "Run on feel today — if anything tightens up, dial it back.",
    )


# ─── Periodization clause builder ──────────────────────────────────────────


def _periodization_clause(
    plan: "TrainingPlan | None", goal: "Goal | None", workout_label: str
) -> str:
    """Build the 'why this workout exists in your plan' clause.

    Omits gracefully when the plan or goal is missing (FR-008c). Always
    returns a clean sentence fragment that fits inline with the rest of
    the paragraph.
    """
    phase = getattr(plan, "current_phase", None) if plan else None
    week = getattr(plan, "current_week", None) if plan else None
    race_name = getattr(goal, "race_name", None) if goal else None

    phase_text = phase.replace("_", " ") if isinstance(phase, str) else None

    parts = []
    if week:
        parts.append(f"week {week}")
    if phase_text:
        parts.append(f"of the {phase_text} block")
    if race_name:
        parts.append(f"on the road to {race_name}")
    if not parts:
        return f"Today's {workout_label} is the work."
    return f"Today's {workout_label} is the work for " + " ".join(parts) + "."


# ─── HRV / sleep / body-battery bucketing ──────────────────────────────────


def _hrv_bucket(snap: "HealthSnapshot | None") -> str:
    if not snap or snap.hrv_score is None:
        return "unknown"
    status = (snap.hrv_status or "").lower() if hasattr(snap, "hrv_status") else ""
    if status in ("low", "unbalanced", "poor"):
        return "low"
    if status in ("high", "well", "balanced"):
        # 'balanced' is the Garmin default — treat as normal, not high.
        return "high" if status == "high" else "normal"
    return "normal"


def _sleep_hours(snap: "HealthSnapshot | None") -> float | None:
    if not snap or not snap.sleep_duration_seconds:
        return None
    return round(snap.sleep_duration_seconds / 3600.0, 1)


def _body_battery_bucket(snap: "HealthSnapshot | None") -> str:
    bb = getattr(snap, "body_battery_start", None) if snap else None
    if bb is None:
        return "unknown"
    if bb < 50:
        return "low"
    if bb < 75:
        return "normal"
    return "high"


# ─── With-snapshot template selection ──────────────────────────────────────


def _with_snapshot_paragraph(
    snap: "HealthSnapshot",
    workout_label: str,
    periodization: str,
    name: str,
) -> str:
    """Pick one of ~6 templates keyed by readiness signal strength.

    Each template:
    - Addresses the athlete by first name.
    - References at least one observed metric value numerically.
    - Names the workout type.
    - Closes with one concrete cue.
    """
    hrv = _hrv_bucket(snap)
    sleep_h = _sleep_hours(snap)
    bb = _body_battery_bucket(snap)
    hrv_score = snap.hrv_score if snap.hrv_score is not None else "—"

    # Template 1 — low HRV (regardless of sleep / body battery)
    if hrv == "low":
        return (
            f"{name}, HRV is sitting at {hrv_score} ms today — below baseline. "
            f"{periodization} I want you running it, but at the easy end of "
            f"effort, not the prescribed end. We don't dig a deeper hole on a "
            f"day the body is asking for room."
        )

    # Template 2 — short sleep, HRV not low
    if sleep_h is not None and sleep_h < 6.5:
        return (
            f"{name}, you slept {sleep_h} hours — not enough. {periodization} "
            f"Run it, but hold the conservative end of the range and judge "
            f"effort by breath, not pace. We can chase numbers on a rested day."
        )

    # Template 3 — low body battery, HRV not low
    if bb == "low":
        bb_val = getattr(snap, "body_battery_start", None)
        return (
            f"{name}, body battery is at {bb_val} this morning — the system "
            f"hasn't fully recharged. {periodization} Run it as written but "
            f"keep the warm-up honest and bail to easy if HR climbs on flat "
            f"ground."
        )

    # Template 4 — high HRV + good sleep (peak readiness)
    if hrv == "high" and sleep_h is not None and sleep_h >= 7.0:
        return (
            f"{name}, HRV is up at {hrv_score} ms and you slept {sleep_h} "
            f"hours. {periodization} Green light — run it as written, just "
            f"don't get greedy in the first segment. The fitness shows up "
            f"in the back half."
        )

    # Template 5 — solid sleep, normal HRV (the bread-and-butter day)
    if sleep_h is not None and sleep_h >= 7.0:
        return (
            f"{name}, sleep was solid at {sleep_h} hours and HRV is steady "
            f"at {hrv_score} ms. {periodization} Run it as written — this is "
            f"the day to execute the plan cleanly."
        )

    # Template 6 — fallback for partial / mixed signals (e.g. snapshot present
    # but sleep is None and bucket is normal). Still reference an observed
    # metric and the periodization.
    return (
        f"{name}, HRV is at {hrv_score} ms and the readiness picture is "
        f"middle-of-the-road today. {periodization} Run the {workout_label} "
        f"as written; bail to easy if HR climbs out of range early."
    )


# ─── Public API ────────────────────────────────────────────────────────────


def rule_based_morning(
    snap: "HealthSnapshot | None",
    planned: "PlannedWorkout | None",
    plan: "TrainingPlan | None",
    goal: "Goal | None",
    athlete_name: str,
) -> str:
    """Return a single coach-voice paragraph for the Today Card.

    Two branches per FR-008a/b:
    - With HealthSnapshot: choose one of ~6 readiness-bucketed templates.
    - Without HealthSnapshot: acknowledge missing data, give a by-feel cue.

    BOTH branches reference the workout type (the "why of the run"); the
    periodization clause appears when TrainingPlan / Goal context is
    available (FR-008c).
    """
    first_name = (athlete_name or "Athlete").split()[0]
    workout_type = getattr(planned, "workout_type", None) if planned else None
    label = _workout_label(workout_type)
    periodization = _periodization_clause(plan, goal, label)

    if snap is not None:
        return _with_snapshot_paragraph(snap, label, periodization, first_name)

    # No-snapshot branch — athlete didn't wear watch overnight, or Garmin
    # 429'd before the snapshot was written. Acknowledge it, still name
    # the workout, and hand them a by-feel cue.
    cue = _by_feel_cue(workout_type)
    return (
        f"{first_name}, I don't have your overnight readings today — no "
        f"watch data came in. {periodization} {cue}"
    )


def rule_based_completed(
    completed_distance_mi: float | None,
    completed_pace_str: str | None,
    target_pace_str: str | None,
    workout_type: str | None,
    athlete_name: str,
) -> str:
    """Return a short coach-voice summary when CompletedWorkout.coach_analysis is empty.

    Used by the COMPLETED state rationale source ladder (FR-006). Compares
    actual vs target pace where both are available; otherwise gives a
    neutral acknowledgement.
    """
    first_name = (athlete_name or "Athlete").split()[0]
    label = _workout_label(workout_type)
    mi_part = (
        f"{completed_distance_mi:.1f} miles"
        if isinstance(completed_distance_mi, (int, float)) and completed_distance_mi
        else "the work"
    )

    if completed_pace_str and target_pace_str:
        return (
            f"{first_name}, you logged {mi_part} on today's {label} at "
            f"{completed_pace_str} (target {target_pace_str}). Data's clean — "
            f"I'll dig into the detail and bring it back tomorrow."
        )
    if completed_pace_str:
        return (
            f"{first_name}, {mi_part} on today's {label} at {completed_pace_str}. "
            f"That's in the books — recovery and food matter more than analysis "
            f"right now."
        )
    return (
        f"{first_name}, today's {label} is done — {mi_part} logged. "
        f"Focus on recovery; I'll have a fuller read tomorrow."
    )
