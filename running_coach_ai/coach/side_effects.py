"""Claude XML side-effect tag handlers.

Every coach response is post-processed for five XML tags before the cleaned
text is returned to the athlete:

| Tag | Function | Effect |
|---|---|---|
| `<plan>...</plan>` | extract_and_apply_plan | Mutate PlannedWorkout rows; resync Garmin |
| `<garmin_sync/>` | extract_and_sync_garmin | Push next plan window to Garmin |
| `<remember>...</remember>` | extract_and_save_memories | Create CoachMemory row |
| `<switch_prescription>...</switch_prescription>` | extract_prescription_switch | Flip athlete.prescription_style + convert upcoming workouts |
| `<coach_switch>...</coach_switch>` | extract_coach_switch | Update athlete.coach_key |

Each function returns the response text with its tag stripped so multiple
extractors can be chained.
"""

import json
import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from running_coach_ai.database.models import (
    Athlete,
    CoachMemory,
    PlannedWorkout,
    TrainingPlan,
)
from running_coach_ai.database.session import scoped_query

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# <plan> — apply mutations to PlannedWorkout rows and resync Garmin
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

    # Track per-session pace shifts for the pace_recalibration story trigger.
    # Each entry is (old_pace_min_per_km, new_pace_min_per_km).
    pace_shifts: list[tuple[float, float]] = []

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
                    old_pace = workout.target_pace_min_per_km
                    new_pace = session["target_pace_min_per_km"]
                    if old_pace and new_pace and new_pace < old_pace:
                        pace_shifts.append((old_pace, new_pace))
                    workout.target_pace_min_per_km = new_pace
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

    # Story trigger: pace_recalibration when ≥3 sessions were faster-recalibrated
    # by ≥10 sec/mi. Wrapped per Constitution V.
    if pace_shifts:
        try:
            from running_coach_ai.coach.story import (
                detect_pace_recalibration, fire_trigger_if_eligible,
            )
            # Convert pace deltas (min/km) to sec/mi for the threshold check
            sec_per_mi_shifts = [(old - new) * 60 * 1.60934 for old, new in pace_shifts]
            avg_shift = sum(sec_per_mi_shifts) / len(sec_per_mi_shifts) if sec_per_mi_shifts else 0
            if detect_pace_recalibration(athlete_id, len(pace_shifts), avg_shift, db_session):
                athlete = db_session.query(Athlete).get(athlete_id)
                if athlete is not None:
                    fire_trigger_if_eligible(
                        athlete, "pace_recalibration",
                        {"sessions_recalibrated": len(pace_shifts),
                         "avg_shift_sec_per_mi": round(avg_shift, 1)},
                        db_session,
                    )
        except Exception as e:
            logger.warning("pace_recalibration trigger failed for athlete %d: %s",
                           athlete_id, e)

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


def reconcile_cancelled_garmin_workouts(athlete_id: int, db_session: Session) -> None:
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


# ---------------------------------------------------------------------------
# <garmin_sync/> — push the upcoming plan window to Garmin
# ---------------------------------------------------------------------------

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
                from running_coach_ai.garmin.admin import _run_garmin_verify
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
            from running_coach_ai.garmin.admin import _run_garmin_verify
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


# ---------------------------------------------------------------------------
# <remember> — persist coaching memories
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# <switch_prescription> — flip distance/time prescription style
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# <coach_switch> — switch active coaching persona
# ---------------------------------------------------------------------------

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
