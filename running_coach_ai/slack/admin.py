"""Admin commands: add/remove/list athletes."""

import logging
import os
import re
import shutil
from datetime import date

from sqlalchemy.orm import Session

from running_coach_ai.config import settings
from running_coach_ai.database.models import (
    Athlete,
    CoachMemory,
    CompletedWorkout,
    ConversationMessage,
    Goal,
    PlannedWorkout,
    RunningProfile,
    TrainingPlan,
)

# In-memory test mode registry: admin_slack_user_id → test Athlete.id
# Activated by !admin test-start; cleared by !admin test-stop.
_test_mode: dict[str, int] = {}


def get_test_athlete_id(admin_slack_id: str) -> int | None:
    """Return the test athlete ID if the admin is currently in test mode, else None."""
    return _test_mode.get(admin_slack_id)

logger = logging.getLogger(__name__)


def handle_admin_command(
    sender_id: str, text: str, db_session: Session, *, channel: str | None = None
) -> str | None:
    """Parse and execute an admin command.

    Only executes if sender_id matches ADMIN_SLACK_USER_ID.
    Returns a response string, or None to silently ignore.

    Commands:
      !admin add <user_id>                               — grant access
      !admin remove <user_id>                            — revoke access (data retained)
      !admin list                                        — list all athletes
      !admin resync-garmin [<uid>]                       — show resync warning
      !admin resync-garmin [<uid>] --confirm             — execute resync
      !admin resync-garmin [<uid>] --confirm --verify    — resync then verify
      !admin clean-garmin [<uid>]                        — show clean warning
      !admin clean-garmin [<uid>] --confirm              — execute clean
      !admin verify-garmin [<uid>]                       — compare DB plan vs live Garmin
      !admin test-start                                  — become a fresh new runner for testing
      !admin test-stop                                   — return to normal admin account
    """
    if sender_id != settings.ADMIN_SLACK_USER_ID:
        return None  # Silently ignore non-admin

    text = text.strip()

    # Only the !admin prefix is valid — silently ignore /admin, /slash, etc.
    if not text.lower().startswith("!admin"):
        return None

    confirmed = "--confirm" in text.lower()
    verify = "--verify" in text.lower()

    add_match = re.match(r"!admin\s+add\s+(<@)?([A-Z0-9]+)>?", text, re.IGNORECASE)
    remove_match = re.match(r"!admin\s+remove\s+(<@)?([A-Z0-9]+)>?", text, re.IGNORECASE)
    list_match = re.match(r"!admin\s+list", text, re.IGNORECASE)
    resync_match = re.match(r"!admin\s+resync-garmin(?:\s+(<@)?([A-Z0-9]+)>?)?", text, re.IGNORECASE)
    clean_match = re.match(r"!admin\s+clean-garmin(?:\s+(<@)?([A-Z0-9]+)>?)?", text, re.IGNORECASE)
    verify_match = re.match(r"!admin\s+verify-garmin(?:\s+(<@)?([A-Z0-9]+)>?)?", text, re.IGNORECASE)
    reset_match = re.match(r"!admin\s+reset-onboarding(?:\s+(<@)?([A-Z0-9]+)>?)?", text, re.IGNORECASE)
    test_start_match = re.match(r"!admin\s+test-start", text, re.IGNORECASE)
    test_stop_match = re.match(r"!admin\s+test-stop", text, re.IGNORECASE)

    if add_match:
        user_id = add_match.group(2)
        return _add_athlete(user_id, db_session)
    elif remove_match:
        user_id = remove_match.group(2)
        return _remove_athlete(user_id, db_session)
    elif list_match:
        return _list_athletes(db_session)
    elif resync_match:
        user_id = resync_match.group(2) if resync_match.group(2) else None
        return _resync_garmin(sender_id, user_id, db_session, confirmed=confirmed, verify=verify)
    elif clean_match:
        user_id = clean_match.group(2) if clean_match.group(2) else None
        return _clean_garmin(sender_id, user_id, db_session, confirmed=confirmed)
    elif verify_match:
        user_id = verify_match.group(2) if verify_match.group(2) else None
        return _verify_garmin(sender_id, user_id, db_session)
    elif reset_match:
        user_id = reset_match.group(2) if reset_match.group(2) else None
        if not user_id:
            return "Usage: `!admin reset-onboarding <user_id>` — uid is required."
        return _reset_onboarding_athlete(user_id, db_session)
    elif test_start_match:
        return _test_start(sender_id, channel, db_session)
    elif test_stop_match:
        return _test_stop(sender_id)
    else:
        return ("Unknown admin command. Try: `!admin add <user_id>`, `!admin remove <user_id>`, "
                "`!admin list`, `!admin resync-garmin [<user_id>]`, `!admin clean-garmin [<user_id>]`, "
                "`!admin verify-garmin [<user_id>]`, `!admin reset-onboarding <user_id>`, "
                "`!admin test-start`, `!admin test-stop`")


def _add_athlete(slack_user_id: str, db_session: Session) -> str:
    """Grant access to an athlete. Creates row if new, re-enables if revoked."""
    athlete = (
        db_session.query(Athlete)
        .filter(Athlete.slack_user_id == slack_user_id)
        .first()
    )

    if athlete:
        if athlete.allowed:
            return f"<@{slack_user_id}> is already on the allowed list."
        athlete.allowed = True
        db_session.commit()
        status = "re-enabled"
        onboarding_note = " Their data is intact — they won't need to re-onboard." if athlete.onboarding_complete else ""
        return f"<@{slack_user_id}> has been {status}.{onboarding_note}"
    else:
        new_athlete = Athlete(
            slack_user_id=slack_user_id,
            allowed=True,
            onboarding_complete=False,
            onboarding_step=0,
        )
        db_session.add(new_athlete)
        db_session.commit()
        logger.info("Admin added new athlete: %s", slack_user_id)
        return f"<@{slack_user_id}> has been added. They can start onboarding by messaging the bot."


def _remove_athlete(slack_user_id: str, db_session: Session) -> str:
    """Revoke access. Data is retained — athlete can be re-added later."""
    athlete = (
        db_session.query(Athlete)
        .filter(Athlete.slack_user_id == slack_user_id)
        .first()
    )

    if not athlete:
        return f"No athlete found with Slack ID {slack_user_id}."

    if not athlete.allowed:
        return f"<@{slack_user_id}> is already removed."

    athlete.allowed = False
    db_session.commit()
    logger.info("Admin removed athlete: %s", slack_user_id)
    return f"<@{slack_user_id}> has been removed. Their data is retained — use `!admin add` to restore access."


def _resync_garmin(
    sender_id: str,
    target_user_id: str | None,
    db_session: Session,
    confirmed: bool = False,
    verify: bool = False,
) -> str:
    """Wipe the Garmin calendar for the plan date range and re-upload from DB.

    Without --confirm: shows a warning describing exactly what will happen.
    With --confirm: executes the operation.
    With --confirm --verify: executes then runs a sync verification pass.
    """
    from datetime import timedelta
    from running_coach_ai.garmin.client import (
        delete_workout, get_garmin_client, remove_workout_schedule,
    )
    from running_coach_ai.garmin.workout_builder import sync_week_to_garmin

    lookup_id = target_user_id or sender_id
    athlete = (
        db_session.query(Athlete)
        .filter(Athlete.slack_user_id == lookup_id)
        .first()
    )

    if not athlete:
        return f"No athlete found with Slack ID {lookup_id}."
    if not athlete.garmin_email or not athlete.garmin_password_encrypted:
        return f"{athlete.name or lookup_id} has no Garmin credentials stored — they'll need to re-onboard."

    name = athlete.name or lookup_id
    today = date.today()

    # All upcoming active workout dates from DB (needed for both warning and execution)
    upcoming = (
        db_session.query(PlannedWorkout.scheduled_date)
        .filter(
            PlannedWorkout.athlete_id == athlete.id,
            PlannedWorkout.scheduled_date >= today,
            PlannedWorkout.status.in_(["planned", "modified"]),
            PlannedWorkout.workout_type != "rest",
        )
        .distinct()
        .all()
    )

    if not upcoming:
        return f"No upcoming planned workouts found for {name}."

    last_date = max(d.scheduled_date for d in upcoming)
    confirm_cmd = f"`!admin resync-garmin{' <@' + target_user_id + '>' if target_user_id else ''} --confirm`"

    # --- Warning (no --confirm) ---
    if not confirmed:
        return (
            f"⚠️ *Garmin resync for {name} — confirm required*\n\n"
            f"This will:\n"
            f"• Delete app-created Garmin workouts from *today → {last_date}* (manual workouts preserved)\n"
            f"• Clear stored Garmin IDs from *{len(upcoming)} planned workout(s)* in the DB\n"
            f"• Re-upload all {len(upcoming)} upcoming workout(s) fresh to Garmin\n\n"
            f"✅ Past workouts, completed activities, and manually created Garmin workouts are *not* affected.\n"
            f"ℹ️ To wipe everything including manual workouts, use `!admin clean-garmin`.\n\n"
            f"To proceed, reply: {confirm_cmd}"
        )

    # --- Execution (--confirm provided) ---
    try:
        garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)
    except Exception as e:
        return f"Could not authenticate with Garmin for {name}: {e}"

    lines = [f"*Garmin resync for {name}:*"]

    # Step 1: Delete only app-created (marked) workouts from the date range.
    # This preserves manually created Garmin workouts — use !admin clean-garmin to wipe everything.
    deleted = 0
    delete_failed = 0
    try:
        from running_coach_ai.garmin.client import get_garmin_workout_library
        from running_coach_ai.garmin.workout_builder import APP_MARKER_RE

        library = get_garmin_workout_library(garmin)

        # Collect workout IDs with our marker that fall within the plan date range
        marked_wids: list[int] = []
        for entry in library:
            m = APP_MARKER_RE.search(entry.get("description") or "")
            if m and int(m.group(1)) == athlete.id:
                try:
                    from datetime import date as _date
                    entry_date = _date.fromisoformat(m.group(2))
                    if today <= entry_date <= last_date:
                        marked_wids.append(int(entry["workoutId"]))
                except (ValueError, KeyError):
                    pass

        # Build schedule ID map from DB before clearing IDs.
        # The Garmin calendar date-range API is unreliable (returns 404 for empty ranges).
        schedule_by_wid: dict[int, int] = {}
        for w in (
            db_session.query(PlannedWorkout)
            .filter(
                PlannedWorkout.athlete_id == athlete.id,
                PlannedWorkout.scheduled_date >= today,
                PlannedWorkout.scheduled_date <= last_date,
                PlannedWorkout.garmin_workout_id.isnot(None),
                PlannedWorkout.garmin_schedule_id.isnot(None),
            )
            .all()
        ):
            try:
                schedule_by_wid[int(w.garmin_workout_id)] = int(w.garmin_schedule_id)
            except (ValueError, TypeError):
                pass

        for wid in marked_wids:
            try:
                if wid in schedule_by_wid:
                    remove_workout_schedule(garmin, schedule_by_wid[wid])
                delete_workout(garmin, wid)
                logger.info("Deleted marked workout %s for athlete %d", wid, athlete.id)
                deleted += 1
            except Exception as e:
                logger.warning(
                    "Could not delete marked workout %s for athlete %d: %s", wid, athlete.id, e,
                )
                delete_failed += 1

    except Exception as e:
        logger.warning("Garmin library scan failed for athlete %d: %s", athlete.id, e)

    lines.append(
        f"• Deleted {deleted} app-created workout(s) from Garmin"
        + (f" ({delete_failed} failed)" if delete_failed else "") + "."
    )

    # Step 2: Clear stored Garmin IDs for the affected range (future only)
    cleared = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete.id,
            PlannedWorkout.scheduled_date >= today,
            PlannedWorkout.scheduled_date <= last_date,
        )
        .update({"garmin_workout_id": None, "garmin_schedule_id": None})
    )
    db_session.commit()
    logger.info("Cleared Garmin IDs from %d planned workout row(s) for athlete %d", cleared, athlete.id)

    # Step 3: Re-upload all active workouts week by week
    week_starts = sorted({
        d.scheduled_date - timedelta(days=d.scheduled_date.weekday())
        for d in upcoming
    })

    total_uploaded = 0
    total_failed = 0
    all_failed_dates: list = []
    errored_weeks = []
    for week_start in week_starts:
        try:
            up, fail, failed_dates = sync_week_to_garmin(
                athlete.id,
                athlete.garmin_email,
                athlete.garmin_password_encrypted,
                week_start,
                db_session,
            )
            total_uploaded += up
            total_failed += fail
            all_failed_dates.extend(failed_dates)
        except Exception as e:
            logger.error("Resync failed for athlete %d week %s: %s", athlete.id, week_start, e)
            errored_weeks.append(str(week_start))

    if errored_weeks:
        lines.append(
            f"• Synced {total_uploaded}/{total_uploaded + total_failed} workout(s). "
            f"Weeks with errors: {', '.join(errored_weeks)}."
        )
    elif total_failed:
        lines.append(
            f"• Synced {total_uploaded}/{total_uploaded + total_failed} workout(s) "
            f"({total_failed} failed — check logs)."
        )
        for d in all_failed_dates:
            lines.append(f"  • Failed to sync: {d} (see logs)")
    else:
        lines.append(f"• Synced {total_uploaded} workout(s) to Garmin.")

    if verify:
        lines.append("")
        lines.append(_verify_garmin(sender_id, target_user_id, db_session))

    return "\n".join(lines)


def _clean_garmin(
    sender_id: str,
    target_user_id: str | None,
    db_session: Session,
    confirmed: bool = False,
) -> str:
    """Wipe the entire Garmin workout library and re-sync upcoming workouts from DB.

    Without --confirm: shows a warning describing exactly what will happen.
    With --confirm: executes the operation.

    Note: only future planned workout IDs are cleared from the DB — past workout
    rows retain their IDs so historical context is preserved.
    """
    from datetime import timedelta
    from running_coach_ai.garmin.client import (
        delete_workout, get_garmin_client, get_garmin_workout_library,
    )
    from running_coach_ai.garmin.workout_builder import sync_week_to_garmin

    lookup_id = target_user_id or sender_id
    athlete = (
        db_session.query(Athlete)
        .filter(Athlete.slack_user_id == lookup_id)
        .first()
    )

    if not athlete:
        return f"No athlete found with Slack ID {lookup_id}."
    if not athlete.garmin_email or not athlete.garmin_password_encrypted:
        return f"{athlete.name or lookup_id} has no Garmin credentials stored."

    name = athlete.name or lookup_id
    today = date.today()
    confirm_cmd = f"`!admin clean-garmin{' <@' + target_user_id + '>' if target_user_id else ''} --confirm`"

    # Count upcoming workouts for the warning (no Garmin API call needed)
    upcoming_count = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete.id,
            PlannedWorkout.scheduled_date >= today,
            PlannedWorkout.status.in_(["planned", "modified"]),
            PlannedWorkout.workout_type != "rest",
        )
        .count()
    )

    # --- Warning (no --confirm) ---
    if not confirmed:
        return (
            f"⚠️ *Garmin clean for {name} — confirm required*\n\n"
            f"This will:\n"
            f"• Delete *every workout in the Garmin workout library* (including any manually created workouts)\n"
            f"• Clear stored Garmin IDs from future planned workout rows in the DB\n"
            f"• Re-upload *{upcoming_count} upcoming workout(s)* to Garmin from scratch\n\n"
            f"✅ Past workout rows in the DB are not modified.\n"
            f"✅ Completed activities (your run history) are *not* deleted.\n"
            f"⚠️ Any workouts you added to Garmin manually will be permanently deleted.\n\n"
            f"Only use this when resync hasn't fixed the problem. "
            f"For most cases, `!admin resync-garmin` is sufficient.\n\n"
            f"To proceed, reply: {confirm_cmd}"
        )

    # --- Execution (--confirm provided) ---
    try:
        garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)
    except Exception as e:
        return f"Could not authenticate with Garmin for {name}: {e}"

    lines = [f"*Garmin clean for {name}:*"]

    # Step 1: Delete entire Garmin workout library
    library = get_garmin_workout_library(garmin)
    deleted = 0
    failed = 0
    for entry in library:
        wid = entry.get("workoutId")
        if not wid:
            continue
        try:
            delete_workout(garmin, int(wid))
            deleted += 1
        except Exception as e:
            logger.warning("Could not delete Garmin workout %s: %s", wid, e)
            failed += 1
    lines.append(f"• Deleted {deleted} workout(s) from Garmin library ({failed} failed).")

    # Step 2: Clear Garmin IDs from future planned workout rows only
    # Past workout rows are left untouched — they hold historical context.
    cleared = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete.id,
            PlannedWorkout.scheduled_date >= today,
        )
        .update({"garmin_workout_id": None, "garmin_schedule_id": None})
    )
    db_session.commit()
    lines.append(f"• Cleared Garmin IDs from {cleared} future planned workout row(s) in DB.")

    # Step 3: Re-sync all upcoming active workouts
    upcoming_dates = (
        db_session.query(PlannedWorkout.scheduled_date)
        .filter(
            PlannedWorkout.athlete_id == athlete.id,
            PlannedWorkout.scheduled_date >= today,
            PlannedWorkout.status.in_(["planned", "modified"]),
            PlannedWorkout.workout_type != "rest",
        )
        .distinct()
        .all()
    )
    week_starts = sorted({
        d.scheduled_date - timedelta(days=d.scheduled_date.weekday())
        for d in upcoming_dates
    })

    total_up = 0
    total_fail = 0
    all_failed_dates: list = []
    for week_start in week_starts:
        try:
            up, fail, failed_dates = sync_week_to_garmin(
                athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted,
                week_start, db_session,
            )
            total_up += up
            total_fail += fail
            all_failed_dates.extend(failed_dates)
        except Exception as e:
            logger.error("Re-sync failed for week %s: %s", week_start, e)
            total_fail += 1

    if all_failed_dates:
        lines.append(f"• Re-synced {total_up} workout(s) to Garmin ({total_fail} failed).")
        for d in all_failed_dates:
            lines.append(f"  • Failed to sync: {d} (see logs)")
    else:
        lines.append(f"• Re-synced {total_up} workout(s) to Garmin ({total_fail} failed).")
    return "\n".join(lines)


def _run_garmin_verify(athlete, db_session: Session):
    """Core Garmin verification logic — no Slack formatting.

    Compares every upcoming planned workout in the DB against the live Garmin
    library and calendar.  Returns three lists of PlannedWorkout objects:

      matched      — in library AND on calendar for the correct date  ✅
      library_only — in library but NOT on calendar                   ⚠️
      missing      — not in the library at all                        ❌

    Raises exceptions on auth or API failure; callers should handle them.
    """
    from running_coach_ai.garmin.client import (
        get_garmin_client, get_garmin_workout_library,
    )
    from running_coach_ai.garmin.workout_builder import APP_MARKER_RE

    today = date.today()

    upcoming = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete.id,
            PlannedWorkout.scheduled_date >= today,
            PlannedWorkout.status.in_(["planned", "modified"]),
            PlannedWorkout.workout_type != "rest",
        )
        .order_by(PlannedWorkout.scheduled_date)
        .all()
    )

    if not upcoming:
        return [], [], []

    garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)

    # Scan library for [rca:{athlete_id}:{date}] markers → date_iso: [workout_id, ...]
    library_by_date: dict[str, list[int]] = {}
    for entry in get_garmin_workout_library(garmin):
        m = APP_MARKER_RE.search(entry.get("description") or "")
        if m and int(m.group(1)) == athlete.id:
            library_by_date.setdefault(m.group(2), []).append(int(entry["workoutId"]))

    # Determine sync status using the library scan + DB schedule IDs.
    # The Garmin calendar date-range API (/workout-service/schedule/{start}/{end}) is
    # unreliable — it returns 404 for empty ranges, making every workout appear
    # "in library but not on calendar".  DB garmin_schedule_id is the ground truth.
    matched, library_only, missing = [], [], []
    for w in upcoming:
        date_iso = w.scheduled_date.isoformat()
        lib_ids = library_by_date.get(date_iso, [])
        if not lib_ids:
            missing.append(w)
        elif w.garmin_schedule_id:
            matched.append(w)
        else:
            library_only.append(w)

    return matched, library_only, missing


def _verify_garmin(
    sender_id: str,
    target_user_id: str | None,
    db_session: Session,
) -> str:
    """Compare the DB plan against the live Garmin library and calendar.

    For each upcoming planned workout, checks whether it is:
      ✅ in Garmin library AND scheduled on the calendar for the right date
      ⚠️  in the library but NOT on the calendar (uploaded but not scheduled)
      ❌  missing from Garmin entirely
    """
    lookup_id = target_user_id or sender_id
    athlete = (
        db_session.query(Athlete)
        .filter(Athlete.slack_user_id == lookup_id)
        .first()
    )

    if not athlete:
        return f"No athlete found with Slack ID {lookup_id}."
    if not athlete.garmin_email or not athlete.garmin_password_encrypted:
        return f"{athlete.name or lookup_id} has no Garmin credentials stored."

    name = athlete.name or lookup_id

    try:
        matched, library_only, missing = _run_garmin_verify(athlete, db_session)
    except Exception as e:
        return f"Could not verify Garmin for {name}: {e}"

    upcoming_count = len(matched) + len(library_only) + len(missing)
    if upcoming_count == 0:
        return f"No upcoming planned workouts found for {name}."

    lines = [f"*Garmin verification for {name}* — {upcoming_count} upcoming workout(s):"]

    if matched:
        lines.append(f"✅ *{len(matched)}* in sync (library + calendar)")
    if library_only:
        lines.append(f"⚠️ *{len(library_only)}* in library but not on calendar:")
        for w in library_only:
            lines.append(f"  • {w.scheduled_date} {w.workout_type}")
    if missing:
        lines.append(f"❌ *{len(missing)}* missing from Garmin entirely:")
        for w in missing:
            lines.append(f"  • {w.scheduled_date} {w.workout_type}")

    if not library_only and not missing:
        lines.append("✅ *All workouts are in sync — DB matches Garmin exactly.*")
    else:
        total_issues = len(library_only) + len(missing)
        lines.append(
            f"\n{total_issues} mismatch(es) found. "
            f"Run `!admin resync-garmin --confirm` to fix."
        )

    return "\n".join(lines)


def _list_athletes(db_session: Session) -> str:
    """Return a formatted list of all athletes."""
    athletes = db_session.query(Athlete).order_by(Athlete.created_at).all()

    if not athletes:
        return "No athletes registered yet."

    lines = ["*Registered athletes:*"]
    for a in athletes:
        status = "✅ active" if a.allowed and a.onboarding_complete else \
                 "⏳ onboarding" if a.allowed else "🚫 removed"
        name = a.name or "(unnamed)"
        lines.append(f"• <@{a.slack_user_id}> — {name} — {status}")

    return "\n".join(lines)


def _test_start(admin_slack_id: str, channel: str | None, db_session: Session) -> str:
    """Create (or reset) a shadow test athlete and activate test mode for the admin.

    The test athlete uses a synthetic slack_user_id so it never collides with a real
    athlete. Its slack_dm_channel_id is set to the admin's real DM channel so all
    bot replies appear in the admin's conversation window.
    """
    if not channel:
        return "Could not determine your DM channel — try sending this from the DM with the bot."

    test_user_id = f"__test_{admin_slack_id}__"

    athlete = (
        db_session.query(Athlete)
        .filter(Athlete.slack_user_id == test_user_id)
        .first()
    )

    if athlete:
        # Full wipe: unlink completed workouts first (FK is nullable)
        db_session.query(CompletedWorkout).filter(
            CompletedWorkout.athlete_id == athlete.id
        ).update({"planned_workout_id": None})

        db_session.query(PlannedWorkout).filter(PlannedWorkout.athlete_id == athlete.id).delete()
        db_session.query(TrainingPlan).filter(TrainingPlan.athlete_id == athlete.id).delete()
        db_session.query(Goal).filter(Goal.athlete_id == athlete.id).delete()
        db_session.query(CoachMemory).filter(CoachMemory.athlete_id == athlete.id).delete()
        db_session.query(RunningProfile).filter(RunningProfile.athlete_id == athlete.id).delete()
        db_session.query(ConversationMessage).filter(ConversationMessage.athlete_id == athlete.id).delete()

        token_path = os.path.join(settings.GARMIN_SESSION_DIR, str(athlete.id))
        if os.path.isdir(token_path):
            try:
                shutil.rmtree(token_path)
            except Exception as e:
                logger.warning("Could not delete test Garmin session dir %s: %s", token_path, e)

        athlete.name = None
        athlete.age = None
        athlete.home_lat = None
        athlete.home_lon = None
        athlete.timezone = None
        athlete.garmin_email = None
        athlete.garmin_password_encrypted = None
        athlete.lthr_bpm = None
        athlete.coach_key = None
        athlete.last_morning_checkin_date = None
        athlete.pending_onboarding_data = None
        athlete.pending_onboarding_data_created_at = None
        athlete.onboarding_complete = False
        athlete.onboarding_step = 0
        athlete.slack_dm_channel_id = channel
        logger.info("Test athlete reset for admin %s (id=%d)", admin_slack_id, athlete.id)
    else:
        athlete = Athlete(
            slack_user_id=test_user_id,
            slack_dm_channel_id=channel,
            allowed=True,
            onboarding_complete=False,
            onboarding_step=0,
        )
        db_session.add(athlete)
        db_session.flush()
        logger.info("Test athlete created for admin %s (id=%d)", admin_slack_id, athlete.id)

    db_session.commit()
    _test_mode[admin_slack_id] = athlete.id

    return (
        "*Test mode ON* — you're now a fresh new runner. "
        "Send me any message to start onboarding. "
        "Run `!admin test-stop` when you're done."
    )


def _test_stop(admin_slack_id: str) -> str:
    """Deactivate test mode — admin messages route back to their real athlete record."""
    if admin_slack_id not in _test_mode:
        return "Test mode is not currently active."
    del _test_mode[admin_slack_id]
    logger.info("Test mode deactivated for admin %s", admin_slack_id)
    return "*Test mode OFF* — you're back to your normal account."


def _reset_onboarding_athlete(slack_user_id: str, db_session: Session) -> str:
    """Reset an athlete's onboarding state so they can re-onboard from scratch."""
    athlete = (
        db_session.query(Athlete)
        .filter(Athlete.slack_user_id == slack_user_id)
        .first()
    )

    if not athlete:
        return f"No athlete found with Slack ID {slack_user_id}."

    deleted = (
        db_session.query(ConversationMessage)
        .filter(ConversationMessage.athlete_id == athlete.id)
        .delete()
    )

    athlete.pending_onboarding_data = None
    athlete.pending_onboarding_data_created_at = None
    athlete.onboarding_complete = False
    athlete.onboarding_step = 0
    db_session.commit()

    logger.info("Admin reset onboarding for athlete %s (id=%d); deleted %d conversation messages",
                slack_user_id, athlete.id, deleted)
    return (
        f"Onboarding reset for <@{slack_user_id}>. "
        f"Deleted {deleted} conversation message(s). "
        f"They can re-onboard by messaging the bot."
    )
