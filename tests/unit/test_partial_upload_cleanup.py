"""Tests for Change 2: partial-upload state cleanup.

When schedule_workout fails after a successful upload_workout, the previously
uploaded library entry must be deleted from Garmin and both garmin_workout_id
and garmin_schedule_id must be set to NULL in the DB. This leaves a clean
unsynced state that the daily reconciliation can repair.

Covers both sync_week_to_garmin and sync_day_to_garmin.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


from running_coach_ai.garmin.workout_builder import sync_week_to_garmin, sync_day_to_garmin

TODAY = date.today()
_MOD = "running_coach_ai.garmin.workout_builder"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _workout(
    athlete_id: int = 1,
    days_ahead: int = 3,
    garmin_workout_id: str = None,
    garmin_schedule_id: str = None,
    workout_type: str = "easy",
):
    w = MagicMock()
    w.athlete_id = athlete_id
    w.scheduled_date = TODAY + timedelta(days=days_ahead)
    w.workout_type = workout_type
    w.target_distance_km = 8.0
    w.target_pace_min_per_km = 6.0
    w.target_duration_seconds = None
    w.target_zones_json = None
    w.description = None
    w.workout_name = None
    w.garmin_workout_id = garmin_workout_id
    w.garmin_schedule_id = garmin_schedule_id
    w.status = "planned"
    return w


def _db(workouts):
    db = MagicMock()
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.limit.return_value = chain
    chain.all.return_value = workouts
    chain.scalar.return_value = None
    db.query.return_value = chain
    db.commit.return_value = None
    return db


# ---------------------------------------------------------------------------
# sync_week_to_garmin — schedule failure
# ---------------------------------------------------------------------------

class TestSyncWeekScheduleFailureCleansUp:

    def _run_week(self, workouts, schedule_side_effect):
        week_start = TODAY - timedelta(days=TODAY.weekday())
        db = _db(workouts)

        with patch(f"{_MOD}.get_garmin_client") as mock_auth, \
             patch(f"{_MOD}.get_garmin_workout_library", return_value=[]), \
             patch(f"{_MOD}.upload_workout", return_value={"workoutId": 9001}) as mock_up, \
             patch(f"{_MOD}.schedule_workout", side_effect=schedule_side_effect) as mock_sched, \
             patch(f"{_MOD}.delete_workout") as mock_del, \
             patch(f"{_MOD}.remove_workout_schedule"):
            mock_auth.return_value = MagicMock()
            uploaded, failed, failed_dates = sync_week_to_garmin(
                1, "a@b.com", b"enc", week_start, db
            )

        return uploaded, failed, failed_dates, mock_up, mock_del, workouts[0]

    def test_schedule_failure_deletes_orphaned_library_entry(self):
        """When schedule fails, the uploaded workout must be deleted from Garmin library."""
        w = _workout()
        _, _, _, _, mock_del, _ = self._run_week(
            [w], schedule_side_effect=Exception("schedule 500")
        )
        mock_del.assert_called_once_with(mock_del.call_args[0][0], 9001)

    def test_schedule_failure_clears_workout_id(self):
        """When schedule fails, garmin_workout_id must be set to None."""
        w = _workout()
        self._run_week([w], schedule_side_effect=Exception("schedule 500"))
        assert w.garmin_workout_id is None

    def test_schedule_failure_clears_schedule_id(self):
        """When schedule fails, garmin_schedule_id must be None (was never set)."""
        w = _workout()
        self._run_week([w], schedule_side_effect=Exception("schedule 500"))
        assert w.garmin_schedule_id is None

    def test_schedule_failure_counts_as_failed(self):
        """A schedule failure must increment the failed counter."""
        w = _workout()
        _, failed, failed_dates, _, _, _ = self._run_week(
            [w], schedule_side_effect=Exception("schedule 500")
        )
        assert failed == 1
        assert w.scheduled_date in failed_dates

    def test_schedule_failure_does_not_count_as_uploaded(self):
        """A workout that failed scheduling must not be counted as uploaded."""
        w = _workout()
        uploaded, _, _, _, _, _ = self._run_week(
            [w], schedule_side_effect=Exception("schedule 500")
        )
        assert uploaded == 0

    def test_partial_failure_one_success_one_fail(self):
        """Two workouts: first schedules OK, second fails. First is uploaded, second is cleaned up."""
        w1 = _workout(days_ahead=1)
        w2 = _workout(days_ahead=2)

        call_count = [0]

        def _sched_side(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("schedule 500")
            return {"workoutScheduleId": 8001}

        week_start = TODAY - timedelta(days=TODAY.weekday())
        db = _db([w1, w2])

        workout_ids = [9001, 9002]
        upload_idx = [0]

        def _upload_side(garmin, wj):
            wid = workout_ids[upload_idx[0]]
            upload_idx[0] += 1
            return {"workoutId": wid}

        with patch(f"{_MOD}.get_garmin_client") as mock_auth, \
             patch(f"{_MOD}.get_garmin_workout_library", return_value=[]), \
             patch(f"{_MOD}.upload_workout", side_effect=_upload_side), \
             patch(f"{_MOD}.schedule_workout", side_effect=_sched_side), \
             patch(f"{_MOD}.delete_workout") as mock_del, \
             patch(f"{_MOD}.remove_workout_schedule"):
            mock_auth.return_value = MagicMock()
            uploaded, failed, _ = sync_week_to_garmin(1, "a@b.com", b"enc", week_start, db)

        assert uploaded == 1
        assert failed == 1
        # The second workout's orphan (9002) must be deleted
        deleted_ids = [c[0][1] for c in mock_del.call_args_list]
        assert 9002 in deleted_ids
        # The first workout's ID is intact
        assert w1.garmin_workout_id == "9001"
        # The second workout's IDs are cleared
        assert w2.garmin_workout_id is None
        assert w2.garmin_schedule_id is None

    def test_orphan_delete_failure_still_clears_db_ids(self):
        """Even if deleting the orphaned library entry fails, DB IDs must still be cleared."""
        w = _workout()
        week_start = TODAY - timedelta(days=TODAY.weekday())
        db = _db([w])

        with patch(f"{_MOD}.get_garmin_client") as mock_auth, \
             patch(f"{_MOD}.get_garmin_workout_library", return_value=[]), \
             patch(f"{_MOD}.upload_workout", return_value={"workoutId": 9001}), \
             patch(f"{_MOD}.schedule_workout", side_effect=Exception("schedule 500")), \
             patch(f"{_MOD}.delete_workout", side_effect=Exception("delete also failed")), \
             patch(f"{_MOD}.remove_workout_schedule"):
            mock_auth.return_value = MagicMock()
            sync_week_to_garmin(1, "a@b.com", b"enc", week_start, db)

        assert w.garmin_workout_id is None
        assert w.garmin_schedule_id is None


# ---------------------------------------------------------------------------
# sync_day_to_garmin — schedule failure
# ---------------------------------------------------------------------------

class TestSyncDayScheduleFailureCleansUp:

    def _run_day(self, workout, schedule_side_effect):
        target_date = workout.scheduled_date
        db = _db([workout])

        with patch(f"{_MOD}.get_garmin_client") as mock_auth, \
             patch(f"{_MOD}.get_garmin_workout_library", return_value=[]), \
             patch(f"{_MOD}.upload_workout", return_value={"workoutId": 9001}) as mock_up, \
             patch(f"{_MOD}.schedule_workout", side_effect=schedule_side_effect), \
             patch(f"{_MOD}.delete_workout") as mock_del, \
             patch(f"{_MOD}.update_workout", side_effect=Exception("no existing id")), \
             patch(f"{_MOD}.remove_workout_schedule"):
            mock_auth.return_value = MagicMock()
            result = sync_day_to_garmin(1, "a@b.com", b"enc", target_date, db)

        return result, mock_del, workout

    def test_day_schedule_failure_deletes_orphan(self):
        """sync_day_to_garmin: schedule failure must delete the orphaned library entry."""
        w = _workout()
        _, mock_del, _ = self._run_day(w, schedule_side_effect=Exception("schedule 500"))
        mock_del.assert_called_once_with(mock_del.call_args[0][0], 9001)

    def test_day_schedule_failure_clears_both_ids(self):
        """sync_day_to_garmin: schedule failure must clear both garmin IDs."""
        w = _workout()
        self._run_day(w, schedule_side_effect=Exception("schedule 500"))
        assert w.garmin_workout_id is None
        assert w.garmin_schedule_id is None

    def test_day_schedule_failure_returns_false(self):
        """sync_day_to_garmin: schedule failure must return False."""
        w = _workout()
        result, _, _ = self._run_day(w, schedule_side_effect=Exception("schedule 500"))
        assert result is False

    def test_day_successful_schedule_sets_both_ids(self):
        """Sanity check: successful schedule sets both IDs."""
        w = _workout()
        target_date = w.scheduled_date
        db = _db([w])

        with patch(f"{_MOD}.get_garmin_client") as mock_auth, \
             patch(f"{_MOD}.get_garmin_workout_library", return_value=[]), \
             patch(f"{_MOD}.upload_workout", return_value={"workoutId": 9001}), \
             patch(f"{_MOD}.schedule_workout", return_value={"workoutScheduleId": 8001}), \
             patch(f"{_MOD}.delete_workout"), \
             patch(f"{_MOD}.update_workout", side_effect=Exception("no id")), \
             patch(f"{_MOD}.remove_workout_schedule"):
            mock_auth.return_value = MagicMock()
            result = sync_day_to_garmin(1, "a@b.com", b"enc", target_date, db)

        assert result is True
        assert w.garmin_workout_id == "9001"
        assert w.garmin_schedule_id == "8001"
