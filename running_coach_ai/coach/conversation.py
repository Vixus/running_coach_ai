"""Coaching conversation orchestrator.

Owns the per-turn flow: load history, build the system prompt, call Claude,
re-call after a `<garmin_fetch/>` if new activities were just ingested, then
apply each XML side-effect tag and persist messages.

The prompt assembly + context formatting live in ``coach/prompt.py``.
The XML extractors and their helpers live in ``coach/side_effects.py``.
"""

import logging
import re

from sqlalchemy.orm import Session

from running_coach_ai.coach.persona import call_claude
from running_coach_ai.coach.prompt import (
    detect_referenced_workout,
    format_run_analysis_for_context,
    resolve_athlete_max_hr,
    build_system_prompt,
)
from running_coach_ai.coach.side_effects import (
    reconcile_cancelled_garmin_workouts,
    extract_and_apply_plan,
    extract_and_save_memories,
    extract_and_sync_garmin,
    extract_coach_switch,
    extract_prescription_switch,
)
from running_coach_ai.database.models import (
    Athlete,
    CompletedWorkout,
    ConversationMessage,
    WorkoutTelemetry,
)
from running_coach_ai.database.session import scoped_query

logger = logging.getLogger(__name__)


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
# Main conversation handler
# ---------------------------------------------------------------------------

def process_message(
    athlete: Athlete,
    user_text: str,
    db_session: Session,
    source: str = "web",
    coach_key: str | None = None,
) -> str:
    """Process one coaching message turn and return the cleaned response.

    Loads history, builds system prompt (using coach_key override if provided),
    calls Claude, applies XML side effects (<plan>, <remember>, <garmin_sync/>),
    persists ConversationMessage rows with the given source, and returns the
    cleaned response text.
    """
    # Temporarily override athlete.coach_key for system prompt assembly if requested.
    # Only restore in finally if we actually applied an override — otherwise we'd
    # clobber any persisted coach switch made by extract_coach_switch() during
    # message processing (the in-memory revert would be written back to the DB
    # by the outer session's commit-on-exit).
    did_override = bool(coach_key)
    original_coach_key = athlete.coach_key
    if did_override:
        athlete.coach_key = coach_key

    try:
        return _process_message_inner(athlete, user_text, db_session, source=source)
    finally:
        if did_override:
            athlete.coach_key = original_coach_key


def _process_message_inner(
    athlete: Athlete,
    text: str,
    db_session: Session,
    source: str = "web",
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


def _handle_message_core(
    athlete: Athlete,
    text: str,
    history: list,
    db_session: Session,
    source: str = "web",
) -> str:
    """Core message processing logic called by process_message."""
    # Story-interview short-circuit: if there's a pending StoryQuestion for this athlete,
    # capture the user's reply as the answer (and queue the next adaptive question)
    # instead of running a regular coaching turn. Per coach/story.py:capture_chat_answer.
    try:
        from running_coach_ai.coach.story import capture_chat_answer, get_pending_question
        if get_pending_question(athlete.id, db_session) is not None:
            answered = capture_chat_answer(athlete, text, db_session)
            if answered is not None:
                # Persist the user message in the chat history so the thread reads naturally,
                # then return a brief acknowledgement. The next question (if any) is delivered
                # via /api/stories/current's hydrate on the next magazine refresh.
                user_msg = ConversationMessage(
                    athlete_id=athlete.id, role="user", content=text, source=source,
                )
                db_session.add(user_msg)
                ack_text = "Got it — thanks for that."
                ack_msg = ConversationMessage(
                    athlete_id=athlete.id, role="assistant", content=ack_text, source=source,
                )
                db_session.add(ack_msg)
                db_session.commit()
                return ack_text
    except Exception as e:
        logger.warning("Story-answer capture short-circuit failed for athlete %d: %s",
                       athlete.id, e)
        # Fall through to regular coaching turn

    # Build messages for Claude
    messages = [{"role": msg.role, "content": msg.content} for msg in history]
    messages.append({"role": "user", "content": text})

    # Build detailed run telemetry context for data-driven Q&A
    RUNNING_TYPES_CTX = {
        "running", "trail_running", "treadmill_running", "track_running",
        "ultra_running", "virtual_run", "obstacle_run",
    }
    spotlight_analyses: list[str] = []
    athlete_max_hr, max_hr_run_count = resolve_athlete_max_hr(athlete.id, db_session)

    # Always include the most recent run with telemetry
    latest_run = (
        scoped_query(db_session, CompletedWorkout, athlete.id)
        .join(WorkoutTelemetry, WorkoutTelemetry.completed_workout_id == CompletedWorkout.id)
        .filter(CompletedWorkout.activity_type.in_(RUNNING_TYPES_CTX))
        .order_by(CompletedWorkout.date.desc())
        .first()
    )
    if latest_run:
        analysis = format_run_analysis_for_context(latest_run, athlete, athlete_max_hr, max_hr_run_count)
        if analysis:
            spotlight_analyses.append(analysis)

    # On-demand: also include any specific past run the user refers to by date/day
    latest_id = latest_run.id if latest_run else None
    referenced_run = detect_referenced_workout(text, athlete.id, latest_id, db_session)
    if referenced_run:
        ref_analysis = format_run_analysis_for_context(referenced_run, athlete, athlete_max_hr, max_hr_run_count)
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
                a = format_run_analysis_for_context(fresh_latest, athlete, athlete_max_hr, max_hr_run_count)
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
    reconcile_cancelled_garmin_workouts(athlete.id, db_session)
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
