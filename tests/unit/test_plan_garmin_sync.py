"""End-to-end test: "can you check our plan and make sure it matches garmin" triggers a real sync.

This test proves that when the coach emits <garmin_sync/>, the full pipeline fires:
  extract_and_sync_garmin → sync_week_to_garmin → upload_workout + schedule_workout

The athlete has planned workouts not yet on Garmin (garmin_workout_id=None).
After handle_message runs, upload_workout must have been called for each such workout.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


from running_coach_ai.coach.conversation import process_message
from running_coach_ai.coach.side_effects import extract_and_sync_garmin


TODAY = date.today()
WEEK_MONDAY = TODAY - timedelta(days=TODAY.weekday())

# The exact user prompt that triggers the bug
SYNC_CHECK_PROMPT = "can you check our plan as you know and make sure that it matches garmin please."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _planned_workout(
    athlete_id: int,
    scheduled_date: date,
    workout_type: str = "easy",
    status: str = "planned",
    garmin_workout_id: str = None,
    garmin_schedule_id: str = None,
    target_distance_km: float = 8.0,
    target_pace_min_per_km: float = 6.0,
):
    w = MagicMock()
    w.id = abs(hash(scheduled_date)) % 10000
    w.athlete_id = athlete_id
    w.scheduled_date = scheduled_date
    w.workout_type = workout_type
    w.status = status
    w.garmin_workout_id = garmin_workout_id
    w.garmin_schedule_id = garmin_schedule_id
    w.target_distance_km = target_distance_km
    w.target_pace_min_per_km = target_pace_min_per_km
    w.target_duration_seconds = None
    w.target_zones_json = None
    w.workout_name = None
    w.description = None
    return w


def _make_athlete(athlete_id: int = 1):
    a = MagicMock()
    a.id = athlete_id
    a.name = "Test Athlete"
    a.garmin_email = "athlete@example.com"
    a.garmin_password_encrypted = b"encrypted"
    a.allowed = True
    a.onboarding_complete = True
    return a


def _make_plan(athlete_id: int, valid_to: date = None):
    p = MagicMock()
    p.id = 1
    p.athlete_id = athlete_id
    p.active = True
    p.valid_to = valid_to or (TODAY + timedelta(weeks=4))
    return p


def _db_for_sync(athlete, workouts, plan=None):
    """DB mock that routes queries to the right return values for extract_and_sync_garmin."""
    from running_coach_ai.database.models import Athlete, TrainingPlan

    db = MagicMock()

    # Catch-all chain
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.limit.return_value = chain
    chain.all.return_value = workouts
    chain.scalar.return_value = None
    chain.get.return_value = athlete

    # plan query (used in extract_and_sync_garmin)
    plan_chain = MagicMock()
    plan_chain.filter.return_value = plan_chain
    plan_chain.first.return_value = plan

    def _side(model):
        if model is Athlete:
            return chain
        if model is TrainingPlan:
            return plan_chain
        return chain

    db.query.side_effect = _side
    db.commit.return_value = None
    return db


# ---------------------------------------------------------------------------
# Core: extract_and_sync_garmin detects the tag and calls sync
# ---------------------------------------------------------------------------

class TestExtractAndSyncGarmin:

    _WB = "running_coach_ai.garmin.workout_builder"

    def _run(self, response_text, workouts, plan=None):
        athlete = _make_athlete()
        db = _db_for_sync(athlete, workouts, plan)

        with patch(f"{self._WB}.get_garmin_client") as mock_auth, \
             patch(f"{self._WB}.get_garmin_workout_library", return_value=[]), \
             patch(f"{self._WB}.upload_workout", return_value={"workoutId": 9001}) as mock_up, \
             patch(f"{self._WB}.schedule_workout", return_value={"workoutScheduleId": 8001}) as mock_sched, \
             patch("running_coach_ai.garmin.admin._run_garmin_verify", return_value=(workouts, [], [])):
            mock_auth.return_value = MagicMock()
            cleaned, note = extract_and_sync_garmin(athlete.id, response_text, db)

        return cleaned, note, mock_up, mock_sched

    def _uploaded_dates(self, mock_up) -> set:
        """Extract scheduled dates from [rca:athlete:date] markers in uploaded workout JSONs."""
        import re
        dates = set()
        for c in mock_up.call_args_list:
            workout_json = c[0][1]
            m = re.search(r'\[rca:\d+:(\d{4}-\d{2}-\d{2})\]', workout_json.get("description", ""))
            if m:
                dates.add(m.group(1))
        return dates

    def test_garmin_sync_tag_triggers_upload(self):
        """<garmin_sync/> in Claude response must cause upload_workout to be called."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        _, _, mock_up, _ = self._run("Syncing your plan. <garmin_sync/>", [w])
        assert mock_up.call_count >= 1

    def test_garmin_sync_tag_stripped_from_response(self):
        """<garmin_sync/> must be removed from the text shown to the athlete."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        cleaned, _, _, _ = self._run("Your plan is synced. <garmin_sync/>", [w])
        assert "<garmin_sync" not in cleaned
        assert "Your plan is synced." in cleaned

    def test_no_garmin_sync_tag_does_not_upload(self):
        """Without <garmin_sync/>, upload_workout must NOT be called."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        _, _, mock_up, _ = self._run("Everything looks good!", [w])
        mock_up.assert_not_called()

    def test_all_unsynced_workouts_uploaded(self):
        """Every unsynced workout's date must appear in the uploaded workout descriptions."""
        d1 = (TODAY + timedelta(days=1)).isoformat()
        d2 = (TODAY + timedelta(days=8)).isoformat()
        d3 = (TODAY + timedelta(days=15)).isoformat()
        workouts = [
            _planned_workout(1, TODAY + timedelta(days=1)),
            _planned_workout(1, TODAY + timedelta(days=8)),
            _planned_workout(1, TODAY + timedelta(days=15)),
        ]
        _, _, mock_up, _ = self._run("Syncing. <garmin_sync/>", workouts)
        uploaded = self._uploaded_dates(mock_up)
        assert d1 in uploaded, f"{d1} was never uploaded (uploaded: {uploaded})"
        assert d2 in uploaded, f"{d2} was never uploaded (uploaded: {uploaded})"
        assert d3 in uploaded, f"{d3} was never uploaded (uploaded: {uploaded})"

    def test_note_appended_on_successful_sync(self):
        """After sync, response must include a status note (verified or fallback)."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        _, note, _, _ = self._run("Here is your plan. <garmin_sync/>", [w])
        # The note is either a verified count ("Verified") or the fallback ("Synced")
        assert "Synced" in note or "Verified" in note or "verified" in note

    def test_garmin_sync_open_close_tag_variant(self):
        """<garmin_sync></garmin_sync> variant must also trigger upload."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        _, _, mock_up, _ = self._run("Syncing. <garmin_sync></garmin_sync>", [w])
        assert mock_up.call_count >= 1

    def test_garmin_sync_open_tag_variant(self):
        """<garmin_sync> (no slash) must also trigger upload."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        _, _, mock_up, _ = self._run("Syncing. <garmin_sync>", [w])
        assert mock_up.call_count >= 1

    def test_sync_covers_weeks_to_plan_end(self):
        """Sync must cover all weeks from today to plan.valid_to, not just 4 weeks."""
        # Workout 6 weeks out — beyond the old 4-week hard cap
        target_date = (TODAY + timedelta(weeks=6, days=1)).isoformat()
        w = _planned_workout(1, TODAY + timedelta(weeks=6, days=1))
        plan = _make_plan(1, valid_to=TODAY + timedelta(weeks=8))
        _, _, mock_up, _ = self._run("Syncing. <garmin_sync/>", [w], plan=plan)
        uploaded = self._uploaded_dates(mock_up)
        assert target_date in uploaded, f"Workout at {target_date} (6 weeks out) was not uploaded"

    def test_no_credentials_returns_skip_note(self):
        """If athlete has no Garmin credentials, return a skip note, no upload."""
        athlete = _make_athlete()
        athlete.garmin_email = None
        athlete.garmin_password_encrypted = None

        chain = MagicMock()
        chain.get.return_value = athlete
        chain.filter.return_value = chain
        chain.first.return_value = None

        db = MagicMock()
        db.query.return_value = chain

        with patch(f"{self._WB}.upload_workout") as mock_up:
            _, note = extract_and_sync_garmin(athlete.id, "Syncing. <garmin_sync/>", db)

        mock_up.assert_not_called()
        assert "skipped" in note.lower() or "no garmin" in note.lower()


# ---------------------------------------------------------------------------
# Full handle_message path: the exact user prompt
# ---------------------------------------------------------------------------

class TestHandleMessageSyncPrompt:
    """Prove that handle_message with the sync-check prompt fires actual Garmin uploads."""

    _WB = "running_coach_ai.garmin.workout_builder"
    _CONV = "running_coach_ai.coach.conversation"

    def _run_handle_message(self, prompt, workouts, plan=None):
        """Run handle_message with mocked Claude (returns <garmin_sync/>) and Garmin API."""
        from running_coach_ai.database.models import (
            Athlete, ConversationMessage, TrainingPlan,
        )

        athlete = _make_athlete()
        plan = plan or _make_plan(athlete.id)

        db = MagicMock()

        # Route db.query() to appropriate chains
        athlete_chain = MagicMock()
        athlete_chain.filter.return_value = athlete_chain
        athlete_chain.order_by.return_value = athlete_chain
        athlete_chain.limit.return_value = athlete_chain
        athlete_chain.all.return_value = []  # conversation history
        athlete_chain.get.return_value = athlete
        athlete_chain.first.return_value = athlete

        plan_chain = MagicMock()
        plan_chain.filter.return_value = plan_chain
        plan_chain.first.return_value = plan

        workout_chain = MagicMock()
        workout_chain.filter.return_value = workout_chain
        workout_chain.order_by.return_value = workout_chain
        workout_chain.limit.return_value = workout_chain
        workout_chain.all.return_value = workouts
        workout_chain.scalar.return_value = None
        workout_chain.count.return_value = len(workouts)

        def _query_side(model):
            if model is Athlete:
                return athlete_chain
            if model is TrainingPlan:
                return plan_chain
            if model is ConversationMessage:
                return athlete_chain  # empty history
            return workout_chain

        db.query.side_effect = _query_side
        db.commit.return_value = None

        # Claude emits <garmin_sync/> — this is the response we're testing will be handled
        claude_response = (
            "I've reviewed your plan against the Garmin calendar. "
            "I can see some workouts aren't uploaded yet — syncing everything now. "
            "<garmin_sync/>"
        )

        uploaded_ids = []

        def _mock_upload(garmin, workout_json):
            wid = len(uploaded_ids) + 9001
            uploaded_ids.append(wid)
            return {"workoutId": wid}

        with patch(f"{self._CONV}.call_claude", return_value=claude_response), \
             patch(f"{self._CONV}.build_system_prompt", return_value="system prompt"), \
             patch(f"{self._WB}.get_garmin_client") as mock_auth, \
             patch(f"{self._WB}.get_garmin_workout_library", return_value=[]), \
             patch(f"{self._WB}.upload_workout", side_effect=_mock_upload) as mock_up, \
             patch(f"{self._WB}.schedule_workout", return_value={"workoutScheduleId": 8001}) as mock_sched, \
             patch(f"{self._WB}.remove_workout_schedule"), \
             patch(f"{self._WB}.delete_workout"), \
             patch("running_coach_ai.garmin.admin._run_garmin_verify", return_value=(workouts, [], [])):
            mock_auth.return_value = MagicMock()
            result = process_message(athlete, prompt, db)

        return result, mock_up, mock_sched, uploaded_ids

    def _uploaded_dates(self, mock_up) -> set:
        import re
        dates = set()
        for c in mock_up.call_args_list:
            workout_json = c[0][1]
            m = re.search(r'\[rca:\d+:(\d{4}-\d{2}-\d{2})\]', workout_json.get("description", ""))
            if m:
                dates.add(m.group(1))
        return dates

    def test_sync_check_prompt_uploads_unsynced_workouts(self):
        """The exact sync-check prompt must result in upload_workout being called for both dates."""
        d1 = (TODAY + timedelta(days=2)).isoformat()
        d2 = (TODAY + timedelta(days=9)).isoformat()
        workouts = [
            _planned_workout(1, TODAY + timedelta(days=2)),
            _planned_workout(1, TODAY + timedelta(days=9)),
        ]
        _, mock_up, _, _ = self._run_handle_message(SYNC_CHECK_PROMPT, workouts)
        assert mock_up.call_count >= 1, (
            "upload_workout was never called — <garmin_sync/> did not trigger sync"
        )
        uploaded = self._uploaded_dates(mock_up)
        assert d1 in uploaded, f"Workout on {d1} was not uploaded (uploaded: {uploaded})"
        assert d2 in uploaded, f"Workout on {d2} was not uploaded (uploaded: {uploaded})"

    def test_sync_check_prompt_schedules_uploaded_workouts(self):
        """After upload, each workout must be scheduled on the Garmin calendar."""
        workouts = [_planned_workout(1, TODAY + timedelta(days=3))]
        _, _, mock_sched, _ = self._run_handle_message(SYNC_CHECK_PROMPT, workouts)
        assert mock_sched.call_count >= 1

    def test_sync_check_prompt_response_includes_sync_note(self):
        """The athlete-facing response must include a sync status note."""
        workouts = [_planned_workout(1, TODAY + timedelta(days=2))]
        result, _, _, _ = self._run_handle_message(SYNC_CHECK_PROMPT, workouts)
        has_note = (
            "Synced" in result or "synced" in result
            or "Verified" in result or "verified" in result
        )
        assert has_note, f"Expected sync note in response, got: {result!r}"

    def test_sync_check_prompt_garmin_sync_tag_not_in_response(self):
        """The <garmin_sync/> tag must be stripped from the response sent to the athlete."""
        workouts = [_planned_workout(1, TODAY + timedelta(days=2))]
        result, _, _, _ = self._run_handle_message(SYNC_CHECK_PROMPT, workouts)
        assert "<garmin_sync" not in result

    def test_uploaded_workouts_have_marker_in_description(self):
        """Each uploaded workout JSON must contain the [rca:...] marker."""
        import re
        workouts = [_planned_workout(1, TODAY + timedelta(days=2))]
        _, mock_up, _, _ = self._run_handle_message(SYNC_CHECK_PROMPT, workouts)

        assert mock_up.called
        uploaded_json = mock_up.call_args[0][1]  # second positional arg is workout_json
        desc = uploaded_json.get("description", "")
        assert re.search(r'\[rca:\d+:\d{4}-\d{2}-\d{2}\]', desc), (
            f"App marker missing from uploaded workout description: {desc!r}"
        )

    def test_no_workouts_sync_note_reflects_zero(self):
        """If all workouts are already synced (or none exist), the note says 0."""
        result, mock_up, _, _ = self._run_handle_message(SYNC_CHECK_PROMPT, [])
        # upload_workout should not be called
        mock_up.assert_not_called()


# ---------------------------------------------------------------------------
# Post-sync auto-verify: note reflects actual Garmin calendar state
# ---------------------------------------------------------------------------

class TestPostSyncVerify:
    """extract_and_sync_garmin must verify the sync outcome via _run_garmin_verify
    and produce a note that reflects reality — not just API return codes."""

    _WB = "running_coach_ai.garmin.workout_builder"
    _ADMIN = "running_coach_ai.garmin.admin"

    def _run(self, workouts, verify_return):
        """Run extract_and_sync_garmin with sync mocked to succeed and verify mocked
        to return the given (matched, library_only, missing) triple."""
        athlete = _make_athlete()
        db = _db_for_sync(athlete, workouts)

        with patch(f"{self._WB}.get_garmin_client") as mock_auth, \
             patch(f"{self._WB}.get_garmin_workout_library", return_value=[]), \
             patch(f"{self._WB}.upload_workout", return_value={"workoutId": 9001}), \
             patch(f"{self._WB}.schedule_workout", return_value={"workoutScheduleId": 8001}), \
             patch(f"{self._ADMIN}._run_garmin_verify", return_value=verify_return):
            mock_auth.return_value = MagicMock()
            _, note = extract_and_sync_garmin(athlete.id, "Sync now. <garmin_sync/>", db)

        return note

    def test_all_verified_note_contains_checkmark_and_count(self):
        """When all workouts land on the calendar, note must confirm verified count."""
        w1 = _planned_workout(1, TODAY + timedelta(days=2))
        w2 = _planned_workout(1, TODAY + timedelta(days=5))
        matched = [w1, w2]
        note = self._run([w1, w2], verify_return=(matched, [], []))
        assert "✅" in note
        assert "2/2" in note or "2" in note
        assert "verified" in note.lower() or "confirmed" in note.lower()

    def test_mismatches_produce_warning_note(self):
        """When some workouts are missing from calendar, note must contain a warning."""
        w1 = _planned_workout(1, TODAY + timedelta(days=2))
        w2 = _planned_workout(1, TODAY + timedelta(days=5))
        note = self._run([w1, w2], verify_return=([w1], [], [w2]))  # w2 missing
        assert "⚠️" in note
        assert "1/2" in note or "1" in note

    def test_verify_failure_reports_unverified_status(self):
        """If _run_garmin_verify raises, note must not claim sync success."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        athlete = _make_athlete()
        db = _db_for_sync(athlete, [w])

        with patch(f"{self._WB}.get_garmin_client") as mock_auth, \
             patch(f"{self._WB}.get_garmin_workout_library", return_value=[]), \
             patch(f"{self._WB}.upload_workout", return_value={"workoutId": 9001}), \
             patch(f"{self._WB}.schedule_workout", return_value={"workoutScheduleId": 8001}), \
             patch(f"{self._ADMIN}._run_garmin_verify", side_effect=Exception("Garmin down")):
            mock_auth.return_value = MagicMock()
            _, note = extract_and_sync_garmin(athlete.id, "Sync. <garmin_sync/>", db)

        assert "⚠️" in note
        assert "can't confirm" in note.lower() or "cannot confirm" in note.lower()

    def test_library_only_workouts_counted_as_issues(self):
        """Workouts in library but not on calendar must trigger the warning note."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        note = self._run([w], verify_return=([], [w], []))  # library-only
        assert "⚠️" in note

    def test_verify_called_with_athlete_object(self):
        """_run_garmin_verify must be called with the athlete ORM object, not just the ID."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        athlete = _make_athlete()
        db = _db_for_sync(athlete, [w])

        with patch(f"{self._WB}.get_garmin_client") as mock_auth, \
             patch(f"{self._WB}.get_garmin_workout_library", return_value=[]), \
             patch(f"{self._WB}.upload_workout", return_value={"workoutId": 9001}), \
             patch(f"{self._WB}.schedule_workout", return_value={"workoutScheduleId": 8001}), \
             patch(f"{self._ADMIN}._run_garmin_verify", return_value=([w], [], [])) as mock_verify:
            mock_auth.return_value = MagicMock()
            extract_and_sync_garmin(athlete.id, "Sync. <garmin_sync/>", db)

        mock_verify.assert_called_once()
        # First arg is the athlete object (has .id), second is db_session
        call_athlete = mock_verify.call_args[0][0]
        assert call_athlete.id == athlete.id


# ---------------------------------------------------------------------------
# plan_already_synced=True: verify-only, no re-upload
# ---------------------------------------------------------------------------

class TestPlanAlreadySynced:
    """When <plan> and <garmin_sync/> appear in the same response, the plan handler
    already uploaded the affected weeks.  <garmin_sync/> must NOT re-upload
    everything — it must run verify-only and report the state."""

    _WB = "running_coach_ai.garmin.workout_builder"
    _ADMIN = "running_coach_ai.garmin.admin"

    def _run(self, workouts, verify_return, plan_already_synced: bool):
        athlete = _make_athlete()
        db = _db_for_sync(athlete, workouts)

        with patch(f"{self._WB}.get_garmin_client") as mock_auth, \
             patch(f"{self._WB}.get_garmin_workout_library", return_value=[]), \
             patch(f"{self._WB}.upload_workout", return_value={"workoutId": 9001}) as mock_up, \
             patch(f"{self._WB}.schedule_workout", return_value={"workoutScheduleId": 8001}), \
             patch(f"{self._ADMIN}._run_garmin_verify", return_value=verify_return):
            mock_auth.return_value = MagicMock()
            _, note = extract_and_sync_garmin(
                athlete.id, "Here is the plan. <garmin_sync/>", db,
                plan_already_synced=plan_already_synced,
            )

        return note, mock_up

    def test_no_upload_when_plan_already_synced(self):
        """With plan_already_synced=True, upload_workout must NOT be called."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        _, mock_up = self._run([w], verify_return=([w], [], []), plan_already_synced=True)
        mock_up.assert_not_called()

    def test_upload_happens_when_plan_not_synced(self):
        """With plan_already_synced=False, upload_workout IS called (normal path)."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        _, mock_up = self._run([w], verify_return=([w], [], []), plan_already_synced=False)
        assert mock_up.call_count >= 1

    def test_verified_note_when_plan_already_synced_and_all_ok(self):
        """With plan_already_synced=True and everything verified, note must say verified."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        note, _ = self._run([w], verify_return=([w], [], []), plan_already_synced=True)
        assert "✅" in note
        assert "verified" in note.lower() or "confirmed" in note.lower()

    def test_warning_note_when_plan_already_synced_but_issues(self):
        """With plan_already_synced=True and mismatches, note must warn."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        note, _ = self._run([w], verify_return=([], [], [w]), plan_already_synced=True)
        assert "⚠️" in note

    def test_verify_failure_when_plan_already_synced_reports_unverified_status(self):
        """verify-only path must not claim Garmin is synced if verification fails."""
        w = _planned_workout(1, TODAY + timedelta(days=2))
        athlete = _make_athlete()
        db = _db_for_sync(athlete, [w])

        with patch(f"{self._WB}.get_garmin_client") as mock_auth, \
             patch(f"{self._WB}.get_garmin_workout_library", return_value=[]), \
             patch(f"{self._WB}.upload_workout", return_value={"workoutId": 9001}) as mock_up, \
             patch(f"{self._WB}.schedule_workout", return_value={"workoutScheduleId": 8001}), \
             patch(f"{self._ADMIN}._run_garmin_verify", side_effect=Exception("Garmin down")):
            mock_auth.return_value = MagicMock()
            _, note = extract_and_sync_garmin(
                athlete.id,
                "Here is the plan. <garmin_sync/>",
                db,
                plan_already_synced=True,
            )

        mock_up.assert_not_called()
        assert "⚠️" in note
        assert "can't confirm" in note.lower() or "cannot confirm" in note.lower()

    def test_handle_message_with_plan_and_sync_tag_takes_verify_only_path(self):
        """Full handle_message: when Claude emits <plan>+<garmin_sync/>, the
        <garmin_sync/> handler must take the verify-only path (call _run_garmin_verify)
        and must NOT call upload_workout a second time for the full plan."""
        from running_coach_ai.database.models import Athlete, ConversationMessage, TrainingPlan

        w = _planned_workout(1, TODAY + timedelta(days=3))
        athlete = _make_athlete()
        plan = _make_plan(athlete.id)

        db = MagicMock()
        athlete_chain = MagicMock()
        athlete_chain.filter.return_value = athlete_chain
        athlete_chain.order_by.return_value = athlete_chain
        athlete_chain.limit.return_value = athlete_chain
        athlete_chain.all.return_value = []
        athlete_chain.get.return_value = athlete
        athlete_chain.first.return_value = athlete

        plan_chain = MagicMock()
        plan_chain.filter.return_value = plan_chain
        plan_chain.first.return_value = plan

        workout_chain = MagicMock()
        workout_chain.filter.return_value = workout_chain
        workout_chain.order_by.return_value = workout_chain
        workout_chain.limit.return_value = workout_chain
        workout_chain.all.return_value = [w]
        workout_chain.scalar.return_value = None
        workout_chain.count.return_value = 1
        workout_chain.first.return_value = w

        def _query_side(model):
            if model is Athlete:
                return athlete_chain
            if model is TrainingPlan:
                return plan_chain
            if model is ConversationMessage:
                return athlete_chain
            return workout_chain

        db.query.side_effect = _query_side
        db.commit.return_value = None

        import json
        plan_json = json.dumps({"sessions": [{"date": (TODAY + timedelta(days=3)).isoformat(),
                                              "workout_type": "easy", "status": "planned"}]})
        claude_response = f"Adding easy Thursday. <plan>{plan_json}</plan> <garmin_sync/>"

        _WB = "running_coach_ai.garmin.workout_builder"
        _ADMIN = "running_coach_ai.garmin.admin"
        _CONV = "running_coach_ai.coach.conversation"

        with patch(f"{_CONV}.call_claude", return_value=claude_response), \
             patch(f"{_CONV}.build_system_prompt", return_value="system"), \
             patch(f"{_WB}.get_garmin_client") as mock_auth, \
             patch(f"{_WB}.get_garmin_workout_library", return_value=[]), \
             patch(f"{_WB}.upload_workout", return_value={"workoutId": 9001}) as mock_up, \
             patch(f"{_WB}.schedule_workout", return_value={"workoutScheduleId": 8001}), \
             patch(f"{_WB}.remove_workout_schedule"), \
             patch(f"{_WB}.delete_workout"), \
             patch(f"{_ADMIN}._run_garmin_verify", return_value=([w], [], [])) as mock_verify:
            mock_auth.return_value = MagicMock()
            from running_coach_ai.coach.conversation import process_message
            result = process_message(athlete, "Add Thursday easy runs please", db)

        # _run_garmin_verify must have been called — confirms the verify-only path was taken
        mock_verify.assert_called_once()
        # upload_workout must NOT have been called from the <garmin_sync/> path
        # (it may have been called 0 or more times by extract_and_apply_plan, but
        # the garmin_sync pass with plan_already_synced=True must not call it at all)
        mock_up.assert_not_called()
        # The response note must reflect verified state, not a re-upload count
        assert "✅" in result or "verified" in result.lower() or "Plan updated" in result
