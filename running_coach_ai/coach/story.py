"""Athlete-story orchestration.

Owns:
- Trigger detection helpers (pr_set, race_complete, race_upcoming, pace_recalibration, difficult_week)
- Eligibility gating (opt-in, intro modal, active-session, priority dedup)
- Adaptive Q&A via Claude (C-001 prompt)
- Editorial generation (C-002 prompt) + regeneration with cooldown
- Conversation-hook helper that captures free-text answers to pending questions

Per the constitution, all queries are athlete-scoped via scoped_query and Claude
failures never abort the host pipeline (try/except around trigger detection +
generation in callers; this module raises specific exceptions for callers to log).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import string
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from running_coach_ai.coach.persona import call_claude, format_miles, format_pace_mi
from running_coach_ai.coach.story_templates import (
    DEFAULT_TEMPLATE_KEY,
    fallback_for_trigger,
    is_registered,
    list_templates,
)
from running_coach_ai.database.models import (
    Athlete,
    AthleteStory,
    CompletedWorkout,
    Goal,
    HealthSnapshot,
    Notification,
    PlannedWorkout,
    StoryInterviewSession,
    StoryQuestion,
)
from running_coach_ai.database.session import scoped_query

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trigger registry + priority order (FR-S028 + spec Edge Cases)
# ---------------------------------------------------------------------------

INTERVIEW_TRIGGERS = (
    "pr_set",
    "race_upcoming",
    "race_complete",
    "pace_recalibration",
    "difficult_week",
)

# Priority order for collision dedup (highest first). Lower-priority triggers
# fired in the same 24h window are suppressed when a higher-priority one fires.
TRIGGER_PRIORITY: dict[str, int] = {
    "race_complete": 5,
    "race_upcoming": 4,
    "pr_set": 3,
    "pace_recalibration": 2,
    "difficult_week": 1,
}

# Versioned prompt constants — bump when the template changes substantively.
QUESTION_PROMPT_VERSION = "v1"
EDITORIAL_PROMPT_VERSION = "v1"

# Running-activity types (mirrors the prompt module's set; duplicated to avoid
# importing prompt.py here)
_RUNNING_TYPES = {
    "running", "trail_running", "treadmill_running", "track_running",
    "ultra_running", "virtual_run", "obstacle_run",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RegenerationCooldownError(Exception):
    """Raised when an athlete tries to regenerate within the 1h cooldown."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Regeneration cooldown active; retry in {retry_after_seconds}s")


class StoryGenerationError(Exception):
    """Raised when Claude generation fails after retries."""


# ---------------------------------------------------------------------------
# Helpers — trigger labels, share tokens
# ---------------------------------------------------------------------------


def format_trigger_label(trigger_kind: str, context: dict[str, Any], athlete: Athlete,
                         db: Session) -> str:
    """Render a one-line natural-language label for a trigger event.

    Pure Python (no Claude call). Callers pass `context` from the trigger detector
    so we can reach the underlying CompletedWorkout / Goal row when relevant.
    """
    if trigger_kind == "race_complete":
        goal_id = context.get("goal_id")
        goal = db.get(Goal, goal_id) if goal_id else None
        race_name = getattr(goal, "race_name", None) if goal else None
        race_type = getattr(goal, "race_type", "race") if goal else "race"
        if race_name:
            return f"{race_name}"
        return f"your {race_type}"
    if trigger_kind == "race_upcoming":
        goal_id = context.get("goal_id")
        goal = db.get(Goal, goal_id) if goal_id else None
        race_name = getattr(goal, "race_name", None) if goal else None
        if race_name:
            return f"the upcoming {race_name}"
        return "your upcoming race"
    if trigger_kind == "pr_set":
        cw_id = context.get("completed_workout_id")
        cw = db.get(CompletedWorkout, cw_id) if cw_id else None
        if cw and cw.distance_km:
            mi = cw.distance_km / 1.60934
            label_dist = f"{int(round(mi))}-mile" if mi >= 4 else f"{mi:.1f}-mile"
            return f"your fastest {label_dist} so far"
        return "your new personal best"
    if trigger_kind == "pace_recalibration":
        return "the pace bump you just locked in"
    if trigger_kind == "difficult_week":
        return "this past week of training"
    return f"your recent {trigger_kind.replace('_', ' ')}"


def _new_share_token() -> str:
    """12 URL-safe characters; ~72 bits of entropy."""
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(12))


# ---------------------------------------------------------------------------
# Trigger detection helpers (FR-S028 + research.md R-004)
# ---------------------------------------------------------------------------


def detect_pr_set(completed: CompletedWorkout, db: Session) -> bool:
    """Return True if this completed workout is a new PR for its distance bucket.

    PR = pace beats every prior workout of comparable distance (within ±10%) by ≥1%.
    Skips non-running activity types.
    """
    if (completed.activity_type or "running") not in _RUNNING_TYPES:
        return False
    if not completed.distance_km or not completed.avg_pace_min_per_km:
        return False

    dist = completed.distance_km
    bucket_low = dist * 0.9
    bucket_high = dist * 1.1

    prior = (
        db.query(CompletedWorkout)
        .filter(
            CompletedWorkout.athlete_id == completed.athlete_id,
            CompletedWorkout.id != completed.id,
            CompletedWorkout.distance_km.isnot(None),
            CompletedWorkout.avg_pace_min_per_km.isnot(None),
            CompletedWorkout.distance_km >= bucket_low,
            CompletedWorkout.distance_km <= bucket_high,
            CompletedWorkout.activity_type.in_(_RUNNING_TYPES),
        )
        .all()
    )
    if not prior:
        # No comparable prior runs — not enough history to call this a PR
        return False

    best_prior_pace = min(p.avg_pace_min_per_km for p in prior)
    # ≥1% improvement = current pace is at least 1% faster (lower)
    return completed.avg_pace_min_per_km <= best_prior_pace * 0.99


def detect_race_complete(completed: CompletedWorkout, db: Session) -> Goal | None:
    """Return the matching Goal if this workout's date is on/around an active race date."""
    if not completed.date:
        return None
    goals = (
        scoped_query(db, Goal, completed.athlete_id)
        .filter(Goal.active == True, Goal.race_date.isnot(None))
        .all()
    )
    for g in goals:
        race_d = g.race_date if isinstance(g.race_date, date) else date.fromisoformat(str(g.race_date))
        if abs((completed.date - race_d).days) <= 1:
            return g
    return None


def detect_race_upcoming(athlete: Athlete, today: date, db: Session) -> Goal | None:
    """Return a Goal whose race_date is exactly 7 days from today."""
    target = today + timedelta(days=7)
    return (
        scoped_query(db, Goal, athlete.id)
        .filter(Goal.active == True, Goal.race_date == target)
        .first()
    )


def detect_pace_recalibration(athlete_id: int, applied_session_count: int,
                               avg_pace_shift_sec_per_mi: float, db: Session) -> bool:
    """True when a <plan> mutation faster-recalibrated ≥3 sessions by ≥10 sec/mi."""
    return applied_session_count >= 3 and avg_pace_shift_sec_per_mi >= 10.0


def detect_difficult_week(athlete: Athlete, db: Session, today: date | None = None) -> bool:
    """True if the athlete has had a hard 7-day window per the criteria in research R-004."""
    if today is None:
        today = date.today()

    # Criterion 1: HRV declining ≥3 consecutive days
    recent_hrv = (
        scoped_query(db, HealthSnapshot, athlete.id)
        .filter(
            HealthSnapshot.date >= today - timedelta(days=7),
            HealthSnapshot.hrv_score.isnot(None),
        )
        .order_by(HealthSnapshot.date.desc())
        .all()
    )
    if len(recent_hrv) >= 3:
        # Walk newest → oldest looking for a 3-day decline streak
        consecutive_decline = 0
        prev: float | None = None
        for h in recent_hrv:
            if prev is not None and h.hrv_score is not None and h.hrv_score < prev:
                consecutive_decline += 1
                if consecutive_decline >= 3:
                    return True
            else:
                consecutive_decline = 0
            prev = h.hrv_score

    # Criterion 2: ≥2 missed planned sessions in the last 7 days
    missed = (
        scoped_query(db, PlannedWorkout, athlete.id)
        .filter(
            PlannedWorkout.scheduled_date >= today - timedelta(days=7),
            PlannedWorkout.scheduled_date < today,
            PlannedWorkout.status.in_(["skipped", "cancelled"]),
        )
        .count()
    )
    if missed >= 2:
        return True

    # Criterion 3: any run with HR drift > 15% in the last 7 days. We approximate
    # via the RunFeedback / WorkoutTelemetry layer if available; otherwise skip.
    # For v1 we rely on completed workouts that have hr_drift_pct via biomechanics.
    # No direct column on CompletedWorkout — leave this criterion as a future hook.
    return False


# ---------------------------------------------------------------------------
# Eligibility gating
# ---------------------------------------------------------------------------


def _has_active_session(athlete_id: int, db: Session) -> bool:
    return (
        scoped_query(db, StoryInterviewSession, athlete_id)
        .filter(StoryInterviewSession.completed_at.is_(None))
        .first()
        is not None
    )


def _recent_higher_priority_session(athlete_id: int, trigger_kind: str,
                                    db: Session) -> bool:
    """True if a higher- or equal-priority session was opened in the last 24h."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    incoming = TRIGGER_PRIORITY.get(trigger_kind, 0)
    recent = (
        scoped_query(db, StoryInterviewSession, athlete_id)
        .filter(StoryInterviewSession.started_at >= cutoff)
        .all()
    )
    for s in recent:
        if TRIGGER_PRIORITY.get(s.trigger_kind, 0) >= incoming:
            return True
    return False


def fire_trigger_if_eligible(athlete: Athlete, trigger_kind: str,
                             context: dict[str, Any], db: Session,
                             *, bypass_intro: bool = False) -> StoryInterviewSession | None:
    """Open a `StoryInterviewSession` for the athlete on this trigger if all gates pass.

    Returns the new session row on success, None on any blocked condition.
    Per FR-S001 + FR-S027 + FR-S003 + spec Edge Cases.
    """
    if trigger_kind not in INTERVIEW_TRIGGERS:
        logger.warning("Unknown trigger_kind %r for athlete %d — ignored", trigger_kind, athlete.id)
        return None

    if not athlete.story_opt_in:
        return None
    if not bypass_intro and athlete.story_intro_seen_at is None:
        # Intro modal not yet acknowledged — silently drop the trigger
        return None
    if _has_active_session(athlete.id, db):
        return None
    if _recent_higher_priority_session(athlete.id, trigger_kind, db):
        return None

    session = StoryInterviewSession(
        athlete_id=athlete.id,
        trigger_kind=trigger_kind,
        trigger_context_json=context or {},
        started_at=datetime.utcnow(),
    )
    db.add(session)
    db.flush()  # populate session.id

    # First question = the consent prompt
    label = format_trigger_label(trigger_kind, context, athlete, db)
    consent_q = StoryQuestion(
        session_id=session.id,
        athlete_id=athlete.id,
        question_index=1,
        question=(
            f"I'd like to capture something for your story — "
            f"can I ask a few questions about {label}?"
        ),
        options_json=["Yes, go ahead", "Maybe later", "Skip"],
        asked_at=datetime.utcnow(),
    )
    db.add(consent_q)
    db.commit()
    logger.info(
        "Story interview session opened for athlete %d trigger=%s session_id=%d",
        athlete.id, trigger_kind, session.id,
    )
    return session


def close_session(session: StoryInterviewSession, db: Session, *, skipped: bool = False) -> None:
    """Mark a session complete. Idempotent."""
    if session.completed_at is None:
        session.completed_at = datetime.utcnow()
        session.skipped = skipped
        db.commit()


# ---------------------------------------------------------------------------
# Adaptive question generation (C-001)
# ---------------------------------------------------------------------------


def _build_question_prompt(session: StoryInterviewSession, athlete: Athlete,
                           db: Session) -> str:
    label = format_trigger_label(session.trigger_kind, session.trigger_context_json or {}, athlete, db)
    history = (
        db.query(StoryQuestion)
        .filter(StoryQuestion.session_id == session.id)
        .order_by(StoryQuestion.question_index.asc())
        .all()
    )
    transcript_lines: list[str] = []
    for q in history:
        if q.answer:
            transcript_lines.append(f"Q{q.question_index}: {q.question}")
            transcript_lines.append(f"A{q.question_index}: {q.answer}")

    transcript_block = "\n".join(transcript_lines) if transcript_lines else "(no questions answered yet)"

    profile = (
        f"- Name: {athlete.name or '(unknown)'}\n"
        f"- Age: {athlete.age or '?'}\n"
    )
    return (
        "You are a veteran magazine interviewer building a positive narrative about an "
        "athlete's running. You are part-way through an interview triggered by: "
        f"{session.trigger_kind}.\n\n"
        f"Athlete profile:\n{profile}\n"
        f"Trigger context: {label}\n\n"
        f"Session transcript so far:\n{transcript_block}\n\n"
        "Ask ONE more question that builds on what they just told you. Specific, warm, "
        "curious — not generic. Output ONLY a JSON object, no prose:\n"
        '{"question":"<the question>","options":["<option 1>","<option 2>","<option 3>","<option 4>"],"session_complete":false}\n'
        "Provide 2–4 options. Set session_complete=true ONLY when you have collected "
        "enough material for a 400–600 word editorial (typically 5–8 answered questions) "
        "AND another question would be padding."
    )


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from a Claude response."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(text)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _validate_question_payload(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if not isinstance(payload.get("question"), str) or not payload["question"].strip():
        return False
    options = payload.get("options")
    if not isinstance(options, list) or not (2 <= len(options) <= 4):
        return False
    if not all(isinstance(o, str) and o.strip() for o in options):
        return False
    if not isinstance(payload.get("session_complete"), bool):
        return False
    return True


def next_question(session: StoryInterviewSession, db: Session) -> StoryQuestion | None:
    """Generate the next adaptive question via Claude, or close the session.

    Returns the new StoryQuestion row, or None if the session has been closed
    (either because Claude marked it complete or the question count reached 10).
    """
    answered = (
        db.query(StoryQuestion)
        .filter(StoryQuestion.session_id == session.id,
                StoryQuestion.answered_at.isnot(None))
        .count()
    )
    if answered >= 10:
        close_session(session, db, skipped=False)
        return None

    athlete = db.get(Athlete, session.athlete_id)
    if athlete is None:
        close_session(session, db, skipped=True)
        return None

    prompt = _build_question_prompt(session, athlete, db)

    payload: dict[str, Any] | None = None
    for attempt in range(2):
        try:
            response = call_claude(prompt, [{"role": "user", "content": "Ask the next question."}],
                                   max_tokens=512)
        except Exception as e:
            logger.warning("Claude question call failed for session %d (attempt %d): %s",
                           session.id, attempt + 1, e)
            continue
        candidate = _extract_json(response)
        if candidate and _validate_question_payload(candidate):
            payload = candidate
            break
        logger.warning(
            "Claude returned malformed question JSON for session %d (attempt %d)",
            session.id, attempt + 1,
        )

    if payload is None:
        # Abort the session and write a story_failed notification
        close_session(session, db, skipped=False)
        try:
            from running_coach_ai.coach.notify import notify
            notify(db, athlete, kind="story_failed", title="Story interview hit a snag",
                   body="I couldn't generate the next question — let's try again next time.",
                   action_path="/#mystory")
            db.commit()
        except Exception as e:
            logger.error("Failed to write story_failed notification: %s", e)
        return None

    if payload["session_complete"] and answered >= 5:
        close_session(session, db, skipped=False)
        return None

    last_index = (
        db.query(StoryQuestion)
        .filter(StoryQuestion.session_id == session.id)
        .count()
    )
    new_q = StoryQuestion(
        session_id=session.id,
        athlete_id=session.athlete_id,
        question_index=last_index + 1,
        question=payload["question"].strip(),
        options_json=[o.strip() for o in payload["options"]],
        asked_at=datetime.utcnow(),
    )
    db.add(new_q)
    db.commit()
    return new_q


# ---------------------------------------------------------------------------
# Conversation hook — capture free-text answers (T022)
# ---------------------------------------------------------------------------


def get_pending_question(athlete_id: int, db: Session) -> StoryQuestion | None:
    """Return the oldest unanswered question for this athlete (across all sessions)."""
    return (
        scoped_query(db, StoryQuestion, athlete_id)
        .join(StoryInterviewSession, StoryInterviewSession.id == StoryQuestion.session_id)
        .filter(
            StoryQuestion.answered_at.is_(None),
            StoryInterviewSession.completed_at.is_(None),
        )
        .order_by(StoryQuestion.asked_at.asc())
        .first()
    )


def capture_chat_answer(athlete: Athlete, text: str, db: Session) -> StoryQuestion | None:
    """If a question is pending, record `text` as a free-text answer and queue the next.

    Returns the answered question on success, or None if no question was pending.
    Caller should short-circuit the regular Claude turn when this returns non-None.
    """
    pending = get_pending_question(athlete.id, db)
    if pending is None:
        return None

    pending.answer = text.strip()
    pending.is_custom_answer = True
    pending.answered_at = datetime.utcnow()
    db.commit()

    # If this was the consent prompt and the athlete declined, close the session.
    if pending.question_index == 1:
        lower = text.strip().lower()
        if lower in {"no", "not now", "skip", "later"} or "not now" in lower:
            session = db.get(StoryInterviewSession, pending.session_id)
            if session:
                close_session(session, db, skipped=True)
            return pending

    # Otherwise generate the next question
    session = db.get(StoryInterviewSession, pending.session_id)
    if session and session.completed_at is None:
        try:
            next_question(session, db)
        except Exception as e:
            logger.warning("next_question failed after capture for session %d: %s",
                           pending.session_id, e)

    return pending


def record_option_answer(question: StoryQuestion, chosen_option: str, db: Session) -> None:
    """Record a multiple-choice answer (button click). Caller chains next_question."""
    question.answer = chosen_option
    question.is_custom_answer = False
    question.answered_at = datetime.utcnow()
    db.commit()


# ---------------------------------------------------------------------------
# Editorial generation (C-002) — implemented in Phase 4 (US3); stub for now
# ---------------------------------------------------------------------------


def _gather_training_stats(athlete: Athlete, milestone_type: str, db: Session) -> dict[str, Any]:
    """Collect mileage, race time, weeks trained, HRV trend for the editorial prompt."""
    today = date.today()
    cutoff = today - timedelta(days=120)
    runs = (
        scoped_query(db, CompletedWorkout, athlete.id)
        .filter(CompletedWorkout.date >= cutoff,
                CompletedWorkout.activity_type.in_(_RUNNING_TYPES))
        .all()
    )
    total_km = sum(r.distance_km or 0 for r in runs)
    total_miles = round(total_km / 1.60934, 1)

    # Race time = duration of the most recent race-day completed workout
    race_time: str | None = None
    if milestone_type == "race_complete":
        for r in sorted(runs, key=lambda x: x.date or date.min, reverse=True):
            if r.duration_seconds:
                hours = r.duration_seconds // 3600
                minutes = (r.duration_seconds % 3600) // 60
                seconds = r.duration_seconds % 60
                race_time = f"{hours}:{minutes:02d}:{seconds:02d}"
                break

    weeks_trained = max(1, (today - cutoff).days // 7)

    hrv_rows = (
        scoped_query(db, HealthSnapshot, athlete.id)
        .filter(HealthSnapshot.date >= today - timedelta(days=14),
                HealthSnapshot.hrv_score.isnot(None))
        .all()
    )
    if hrv_rows:
        hrv_values = [h.hrv_score for h in hrv_rows]
        hrv_trend = f"avg {sum(hrv_values) / len(hrv_values):.0f} (last 14 days)"
    else:
        hrv_trend = "no recent HRV data"

    return {
        "total_miles": total_miles,
        "race_time": race_time,
        "weeks_trained": weeks_trained,
        "hrv_trend": hrv_trend,
    }


def _collect_qa_pairs(athlete: Athlete, db: Session) -> list[tuple[str, str]]:
    """All answered Q&A pairs from completed sessions for this athlete since the last
    published story (or all-time if no published story exists yet)."""
    last_pub = (
        scoped_query(db, AthleteStory, athlete.id)
        .filter(AthleteStory.published_at.isnot(None),
                AthleteStory.deleted_at.is_(None))
        .order_by(AthleteStory.published_at.desc())
        .first()
    )
    cutoff = last_pub.published_at if last_pub else None

    sessions_q = (
        scoped_query(db, StoryInterviewSession, athlete.id)
        .filter(StoryInterviewSession.completed_at.isnot(None),
                StoryInterviewSession.skipped == False)
    )
    if cutoff is not None:
        sessions_q = sessions_q.filter(StoryInterviewSession.started_at > cutoff)
    sessions = sessions_q.all()

    pairs: list[tuple[str, str]] = []
    for s in sessions:
        for q in sorted(s.questions, key=lambda x: x.question_index):
            if q.question_index == 1:
                continue  # skip consent prompt
            if q.answer:
                pairs.append((q.question, q.answer))
    return pairs


def _build_editorial_prompt(athlete: Athlete, milestone_type: str,
                            stats: dict[str, Any], pairs: list[tuple[str, str]],
                            *, locked_voice: str | None = None) -> str:
    qa_block = "\n".join(f"Q: {q}\nA: {a}" for q, a in pairs) if pairs \
        else "(athlete answered no questions — rely on training stats and milestone context)"

    profile = (
        f"- Name: {athlete.name or '(unknown athlete)'}\n"
        f"- Age: {athlete.age or '?'}\n"
        f"- Goal: {milestone_type}\n"
    )
    stats_block = (
        f"- Total miles (last 120 days): {stats['total_miles']}\n"
        f"- Race time: {stats['race_time'] or 'N/A'}\n"
        f"- Weeks trained: {stats['weeks_trained']}\n"
        f"- HRV trend: {stats['hrv_trend']}\n"
    )

    if locked_voice is not None:
        return (
            "You are a magazine editor writing a 400–600 word editorial profile of an "
            "athlete after a key training milestone.\n\n"
            f"Athlete profile:\n{profile}\n"
            f"Training stats:\n{stats_block}\n"
            f"Interview answers:\n{qa_block}\n\n"
            "Write in this voice:\n"
            f"{locked_voice}\n\n"
            "Output ONLY a JSON object:\n"
            '{"editorial_body":"<400–600 word prose, 3–5 paragraphs>"}\n'
        )

    templates_block_lines = []
    for tpl in list_templates():
        affinities = ", ".join(tpl.trigger_affinities) if tpl.trigger_affinities else "(none)"
        templates_block_lines.append(
            f"- {tpl.key} ({tpl.display_name}): {tpl.voice_description} [affinities: {affinities}]"
        )
    templates_block = "\n".join(templates_block_lines)

    return (
        "You are a magazine editor writing a 400–600 word editorial profile of an athlete "
        "after a key training milestone.\n\n"
        f"Athlete profile:\n{profile}\n"
        f"Training stats:\n{stats_block}\n"
        f"Interview answers:\n{qa_block}\n\n"
        "Available magazine templates (pick exactly one):\n"
        f"{templates_block}\n\n"
        "Choose the template whose voice best fits THIS athlete's answers and arc — don't "
        "just match the trigger. Then write the editorial in the chosen template's voice.\n\n"
        "Output ONLY a JSON object:\n"
        '{"editorial_body":"<400–600 word prose, 3–5 paragraphs>","template_key":"<one of the registered keys>"}\n'
    )


def _validate_editorial_payload(payload: Any, *, locked: bool) -> bool:
    if not isinstance(payload, dict):
        return False
    body = payload.get("editorial_body")
    if not isinstance(body, str) or not body.strip():
        return False
    word_count = len(body.split())
    # Tolerance band around 400–600
    if word_count < 350 or word_count > 700:
        return False
    if not locked:
        if not isinstance(payload.get("template_key"), str) or not payload["template_key"].strip():
            return False
    return True


def _write_notification(db: Session, athlete: Athlete, *, kind: str, title: str,
                        body: str, action_path: str = "/#mystory") -> None:
    try:
        from running_coach_ai.coach.notify import notify
        notify(db, athlete, kind=kind, title=title, body=body, action_path=action_path)
        db.commit()
    except Exception as e:
        logger.error("Failed to write %s notification for athlete %d: %s", kind, athlete.id, e)


def generate_story(athlete: Athlete, milestone_type: str, db: Session) -> AthleteStory:
    """Generate an AthleteStory editorial via Claude on a milestone.

    Multi-template path: Claude votes a template_key in the same call as the editorial.
    Locked-voice branch is added by US6 / T068 (template_locked_by_athlete defaults False
    at creation, so the locked branch is unreachable here — kept for symmetry with
    regenerate_story).
    """
    pairs = _collect_qa_pairs(athlete, db)
    stats = _gather_training_stats(athlete, milestone_type, db)
    prompt = _build_editorial_prompt(athlete, milestone_type, stats, pairs, locked_voice=None)

    payload: dict[str, Any] | None = None
    for attempt in range(2):
        try:
            response = call_claude(prompt, [{"role": "user", "content": "Write the editorial."}],
                                   max_tokens=4096)
        except Exception as e:
            logger.error("Claude editorial call failed for athlete %d (attempt %d): %s",
                         athlete.id, attempt + 1, e)
            continue
        candidate = _extract_json(response)
        if candidate and _validate_editorial_payload(candidate, locked=False):
            payload = candidate
            break
        logger.warning("Claude editorial JSON malformed for athlete %d (attempt %d)",
                       athlete.id, attempt + 1)

    if payload is None:
        _write_notification(db, athlete, kind="story_failed",
                            title="Story generation hit a snag",
                            body="I couldn't write your story this time — try again from your magazine.")
        raise StoryGenerationError("Editorial generation failed after retries")

    template_key = payload.get("template_key", DEFAULT_TEMPLATE_KEY)
    if not is_registered(template_key):
        fallback = fallback_for_trigger(milestone_type)
        logger.warning("Claude voted unregistered template %r — falling back to %r",
                       template_key, fallback)
        template_key = fallback

    title = _format_title(athlete, milestone_type, db)

    story = AthleteStory(
        athlete_id=athlete.id,
        milestone_type=milestone_type,
        title=title,
        editorial_body=payload["editorial_body"].strip(),
        template_key=template_key,
        template_locked_by_athlete=False,
        share_token=_new_share_token(),
        regeneration_count=0,
        last_regenerated_at=None,
        created_at=datetime.utcnow(),
    )
    db.add(story)
    db.flush()  # populate story.id

    # Link all sessions feeding this story
    pairs_count = 0
    sessions_q = (
        scoped_query(db, StoryInterviewSession, athlete.id)
        .filter(StoryInterviewSession.completed_at.isnot(None),
                StoryInterviewSession.skipped == False,
                StoryInterviewSession.story_id.is_(None))
    )
    for s in sessions_q.all():
        s.story_id = story.id
        for q in s.questions:
            q.story_id = story.id
            if q.answer:
                pairs_count += 1
    db.commit()

    _write_notification(db, athlete, kind="story_ready",
                        title="Your story is ready",
                        body=f"Preview your magazine spread — {pairs_count} answers shaped it.",
                        action_path="/#mystory")
    logger.info("Story %d generated for athlete %d (template=%s, %d Q&As)",
                story.id, athlete.id, template_key, pairs_count)
    return story


def _format_title(athlete: Athlete, milestone_type: str, db: Session) -> str:
    first_name = (athlete.name or "Athlete").split()[0]
    if milestone_type == "race_complete":
        # Find the most recent race goal name
        goal = (
            scoped_query(db, Goal, athlete.id)
            .filter(Goal.race_date.isnot(None))
            .order_by(Goal.race_date.desc())
            .first()
        )
        race_name = getattr(goal, "race_name", None) if goal else None
        if race_name:
            return f"{first_name} · {race_name}"
        return f"{first_name} · Race Day"
    return f"{first_name} · {milestone_type.replace('_', ' ').title()}"


def regenerate_story(story: AthleteStory, db: Session, *, by_admin: bool = False) -> AthleteStory:
    """Re-run Claude to overwrite the editorial body. Athlete-scoped 1h cooldown.

    Per FR-S021 / FR-S035. The locked-voice branch (template_locked_by_athlete=True)
    is added by US6 / T068; at US3 time the lock flag is always False.
    """
    if not by_admin and story.last_regenerated_at is not None:
        elapsed = (datetime.utcnow() - story.last_regenerated_at).total_seconds()
        if elapsed < 3600:
            raise RegenerationCooldownError(int(3600 - elapsed))

    athlete = db.get(Athlete, story.athlete_id)
    if athlete is None:
        raise StoryGenerationError("Athlete row missing")

    locked_voice: str | None = None
    if story.template_locked_by_athlete:
        # Set by US6 / T068 — fetch the locked template's voice
        from running_coach_ai.coach.story_templates import get_template
        try:
            tpl = get_template(story.template_key)
            locked_voice = tpl.voice_description
        except Exception:
            locked_voice = None

    pairs = _collect_qa_pairs(athlete, db)
    stats = _gather_training_stats(athlete, story.milestone_type, db)
    prompt = _build_editorial_prompt(athlete, story.milestone_type, stats, pairs,
                                     locked_voice=locked_voice)

    payload: dict[str, Any] | None = None
    for attempt in range(2):
        try:
            response = call_claude(prompt, [{"role": "user", "content": "Write the editorial."}],
                                   max_tokens=4096)
        except Exception as e:
            logger.error("Claude regeneration call failed for story %d (attempt %d): %s",
                         story.id, attempt + 1, e)
            continue
        candidate = _extract_json(response)
        if candidate and _validate_editorial_payload(candidate, locked=locked_voice is not None):
            payload = candidate
            break

    if payload is None:
        _write_notification(db, athlete, kind="story_failed",
                            title="Story regeneration hit a snag",
                            body="I couldn't rewrite your story this time — try again later.")
        raise StoryGenerationError("Regeneration failed after retries")

    story.editorial_body = payload["editorial_body"].strip()
    story.regeneration_count += 1
    story.last_regenerated_at = datetime.utcnow()

    if locked_voice is None:
        # Multi-template path — Claude voted a key
        new_key = payload.get("template_key", story.template_key)
        if is_registered(new_key):
            story.template_key = new_key

    db.commit()
    _write_notification(db, athlete, kind="story_ready",
                        title="Your story has been rewritten",
                        body="Preview the new version from your magazine.",
                        action_path="/#mystory")
    return story


def refresh_cover_image_path(story: AthleteStory, db: Session) -> None:
    """Recompute `cover_image_path` from the lowest-sort_order StoryImage."""
    if not story.images:
        story.cover_image_path = None
    else:
        first = sorted(story.images, key=lambda x: (x.sort_order, x.id))[0]
        story.cover_image_path = first.filename
    db.commit()
