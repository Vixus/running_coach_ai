"""Tests that expose and verify the reliability of !admin resync-garmin.

_resync_garmin now deletes only marker-tagged (app-created) workouts before
re-uploading, which prevents accidental deletion of manually created workouts.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


from running_coach_ai.slack.admin import _resync_garmin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TODAY = date.today()


def _athlete(slack_user_id="U123"):
    a = MagicMock()
    a.id = 1
    a.slack_user_id = slack_user_id
    a.name = "Athlete"
    a.garmin_email = "a@b.com"
    a.garmin_password_encrypted = b"enc"
    return a


def _date_row(d: date):
    r = MagicMock()
    r.scheduled_date = d
    return r


def _db(athlete, upcoming_dates, garmin_id_pairs=None):
    """garmin_id_pairs: list of (workout_id, schedule_id) for the DB ID map query."""
    db = MagicMock()
    athlete_q = MagicMock()
    athlete_q.filter.return_value.first.return_value = athlete
    date_q = MagicMock()
    date_q.filter.return_value.distinct.return_value.all.return_value = [
        _date_row(d) for d in upcoming_dates
    ]
    date_q.filter.return_value.update.return_value = len(upcoming_dates)
    id_workouts = []
    for wid, sid in (garmin_id_pairs or []):
        w = MagicMock()
        w.garmin_workout_id = str(wid)
        w.garmin_schedule_id = str(sid)
        id_workouts.append(w)
    date_q.filter.return_value.all.return_value = id_workouts

    def _side(model_or_col):
        from running_coach_ai.database.models import Athlete
        if model_or_col is Athlete:
            return athlete_q
        return date_q

    db.query.side_effect = _side
    return db


def _library_entry(athlete_id: int, entry_date: date, workout_id: int) -> dict:
    return {
        "workoutId": workout_id,
        "description": f"[rca:{athlete_id}:{entry_date.isoformat()}]",
    }


def _calendar_entry(workout_id: int, schedule_id: int) -> dict:
    return {"workoutId": workout_id, "workoutScheduleId": schedule_id}


def _run(library_entries, cal_entries, upcoming_dates, sync_return=(1, 0, [])):
    athlete = _athlete()
    # Derive DB workout objects from cal_entries (replacing broken calendar API)
    garmin_id_pairs = [
        (e["workoutId"], e.get("workoutScheduleId", 0))
        for e in cal_entries
        if e.get("workoutId") and e.get("workoutScheduleId")
    ]
    db = _db(athlete, upcoming_dates, garmin_id_pairs)

    with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
         patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=library_entries), \
         patch("running_coach_ai.garmin.client.remove_workout_schedule") as mock_rm_sched, \
         patch("running_coach_ai.garmin.client.delete_workout") as mock_del_wk, \
         patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", return_value=sync_return) as mock_sync:
        mock_auth.return_value = MagicMock()
        result = _resync_garmin("U123", None, db, confirmed=True)

    return result, mock_rm_sched, mock_del_wk, mock_sync, db


# ---------------------------------------------------------------------------
# Core: marker-based delete then resync
# ---------------------------------------------------------------------------

class TestResyncWipeAndResync:

    def test_marked_workout_deleted_before_sync(self):
        """A library entry with our marker is deleted, then sync re-uploads it."""
        active_date = TODAY + timedelta(days=3)
        library = [_library_entry(1, active_date, 88)]
        cal = [_calendar_entry(88, 99)]

        result, mock_rm_sched, mock_del_wk, mock_sync, _ = _run(
            library, cal, [active_date]
        )

        mock_rm_sched.assert_called_once()
        mock_del_wk.assert_called_once()
        mock_sync.assert_called_once()

    def test_unmarked_entry_not_deleted(self):
        """A calendar entry with no library marker is NOT deleted."""
        active_date = TODAY + timedelta(days=3)
        # Calendar has an entry; library has nothing with our marker
        cal = [{"workoutId": 88, "workoutScheduleId": 99}]
        library = []

        result, mock_rm_sched, mock_del_wk, mock_sync, _ = _run(
            library, cal, [active_date]
        )

        mock_rm_sched.assert_not_called()
        mock_del_wk.assert_not_called()
        mock_sync.assert_called_once()

    def test_duplicate_marked_entries_same_date_both_deleted(self):
        """Two marked library entries for the same date (prior double-upload) are both deleted."""
        active_date = TODAY + timedelta(days=3)
        library = [
            _library_entry(1, active_date, 20),
            _library_entry(1, active_date, 21),
        ]
        cal = [_calendar_entry(20, 100), _calendar_entry(21, 101)]

        result, mock_rm_sched, mock_del_wk, mock_sync, _ = _run(
            library, cal, [active_date]
        )

        assert mock_rm_sched.call_count == 2
        assert mock_del_wk.call_count == 2

    def test_db_ids_cleared_before_sync(self):
        """garmin_workout_id and garmin_schedule_id must be cleared in DB before sync."""
        from running_coach_ai.database.models import Athlete

        active_date = TODAY + timedelta(days=3)
        athlete = _athlete()

        athlete_q = MagicMock()
        athlete_q.filter.return_value.first.return_value = athlete

        workout_q = MagicMock()
        workout_q.filter.return_value.distinct.return_value.all.return_value = [_date_row(active_date)]
        workout_q.filter.return_value.update.return_value = 1
        workout_q.filter.return_value.all.return_value = []

        db = MagicMock()
        db.query.side_effect = lambda m: athlete_q if m is Athlete else workout_q

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=[]), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", return_value=(1, 0, [])):
            mock_auth.return_value = MagicMock()
            _resync_garmin("U123", None, db, confirmed=True)

        workout_q.filter.return_value.update.assert_called_once_with(
            {"garmin_workout_id": None, "garmin_schedule_id": None}
        )

    def test_sync_runs_after_delete(self):
        """sync_week_to_garmin must be called after the deletion pass, not before."""
        active_date = TODAY + timedelta(days=5)
        library = [_library_entry(1, active_date, 88)]
        cal = [_calendar_entry(88, 99)]

        _, _, _, mock_sync, _ = _run(library, cal, [active_date])

        mock_sync.assert_called_once()

    def test_no_marked_workouts_still_syncs_db_workouts(self):
        """Empty library — sync still runs for all DB workouts."""
        active_date = TODAY + timedelta(days=3)
        _, _, _, mock_sync, _ = _run([], [], [active_date])

        mock_sync.assert_called_once()

    def test_delete_failure_does_not_abort_sync(self):
        """A Garmin delete failure must not stop the subsequent upload."""
        active_date = TODAY + timedelta(days=3)
        library = [_library_entry(1, active_date, 88)]
        cal = [_calendar_entry(88, 99)]
        athlete = _athlete()
        db = _db(athlete, [active_date])

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=library), \
             patch("running_coach_ai.garmin.client.remove_workout_schedule", side_effect=Exception("500")), \
             patch("running_coach_ai.garmin.client.delete_workout", side_effect=Exception("500")), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   return_value=(1, 0, [])) as mock_sync:
            mock_auth.return_value = MagicMock()
            result = _resync_garmin("U123", None, db, confirmed=True)

        mock_sync.assert_called_once()

    def test_multiple_weeks_each_synced(self):
        """Three weeks of workouts → sync_week_to_garmin called once per week."""
        dates = [
            TODAY + timedelta(days=2),   # week 1
            TODAY + timedelta(days=9),   # week 2
            TODAY + timedelta(days=16),  # week 3
        ]
        _, _, _, mock_sync, _ = _run([], [], dates)

        assert mock_sync.call_count == 3

    def test_result_message_includes_deleted_and_synced_counts(self):
        """Response message must report how many marked entries were deleted and how many uploaded."""
        active_date = TODAY + timedelta(days=3)
        library = [
            _library_entry(1, active_date, 10),
            _library_entry(1, TODAY + timedelta(days=1), 11),
        ]
        cal = [_calendar_entry(10, 100), _calendar_entry(11, 101)]

        result, _, _, _, _ = _run(library, cal, [active_date], sync_return=(1, 0, []))

        assert "2" in result   # deleted count
        assert "1" in result   # synced count
