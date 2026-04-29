"""Tests for sync_day_to_garmin — targeted per-day Garmin sync.

sync_day_to_garmin:
  - Skips past dates
  - Uses update_workout (PUT) when the workout already has a garmin_workout_id
  - Falls back to delete+create when the in-place update fails
  - Scans library for stale marker entries on fresh uploads
  - Returns True on success, False on upload failure
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


from running_coach_ai.garmin.workout_builder import sync_day_to_garmin


TODAY = date.today()
_MOD = "running_coach_ai.garmin.workout_builder"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _planned_workout(
    athlete_id: int = 1,
    scheduled_date: date = None,
    workout_type: str = "easy",
    target_distance_km: float = 8.0,
    target_pace_min_per_km: float = 6.0,
    garmin_workout_id: str = None,
    garmin_schedule_id: str = None,
    status: str = "planned",
    description: str = None,
):
    w = MagicMock()
    w.athlete_id = athlete_id
    w.scheduled_date = scheduled_date or (TODAY + timedelta(days=3))
    w.workout_type = workout_type
    w.target_distance_km = target_distance_km
    w.target_pace_min_per_km = target_pace_min_per_km
    w.target_duration_seconds = None
    w.target_zones_json = None
    w.workout_name = None
    w.garmin_workout_id = garmin_workout_id
    w.garmin_schedule_id = garmin_schedule_id
    w.status = status
    w.description = description
    return w


def _db_for_day(athlete_id: int, workouts: list, target_date: date):
    db = MagicMock()
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.limit.return_value = chain
    chain.all.return_value = workouts
    chain.scalar.return_value = None   # no fallback paces
    db.query.return_value = chain
    db.commit.return_value = None
    return db


def _run(workouts, target_date=None, library=None, cal=None,
         update_side_effect=None, upload_return=None):
    """Run sync_day_to_garmin with mocked Garmin API."""
    athlete_id = 1
    target_date = target_date or (TODAY + timedelta(days=3))
    library = library if library is not None else []
    cal = cal if cal is not None else []
    upload_return = upload_return or {"workoutId": 9001}

    db = _db_for_day(athlete_id, workouts, target_date)

    update_kwargs = {"side_effect": update_side_effect} if update_side_effect else {"return_value": {}}

    with patch(f"{_MOD}.get_garmin_client") as mock_auth, \
         patch(f"{_MOD}.get_garmin_workout_library", return_value=library), \
         patch(f"{_MOD}.update_workout", **update_kwargs) as mock_update, \
         patch(f"{_MOD}.upload_workout", return_value=upload_return) as mock_upload, \
         patch(f"{_MOD}.schedule_workout", return_value={"workoutScheduleId": 8001}) as mock_sched, \
         patch(f"{_MOD}.remove_workout_schedule") as mock_rm, \
         patch(f"{_MOD}.delete_workout") as mock_del:
        mock_auth.return_value = MagicMock()
        result = sync_day_to_garmin(
            athlete_id, "test@example.com", b"enc", target_date, db
        )

    return result, mock_update, mock_upload, mock_sched, mock_rm, mock_del


# ---------------------------------------------------------------------------
# Past-date guard
# ---------------------------------------------------------------------------

class TestSyncDayPastDateGuard:

    def test_past_date_returns_true_immediately(self):
        """Past dates are skipped — no Garmin API calls, returns True."""
        yesterday = TODAY - timedelta(days=1)
        result, mock_update, mock_upload, _, _, _ = _run([], target_date=yesterday)
        assert result is True
        mock_update.assert_not_called()
        mock_upload.assert_not_called()

    def test_today_is_not_skipped(self):
        """Today is a valid sync date — must NOT be skipped."""
        w = _planned_workout(scheduled_date=TODAY)
        result, _, mock_upload, _, _, _ = _run([w], target_date=TODAY)
        mock_upload.assert_called_once()

    def test_future_date_is_not_skipped(self):
        """Future dates proceed normally."""
        future = TODAY + timedelta(days=10)
        w = _planned_workout(scheduled_date=future)
        result, _, mock_upload, _, _, _ = _run([w], target_date=future)
        mock_upload.assert_called_once()


# ---------------------------------------------------------------------------
# No workouts guard
# ---------------------------------------------------------------------------

class TestSyncDayNoWorkouts:

    def test_no_workouts_returns_true(self):
        """If no planned workouts for the date, returns True without API calls."""
        result, mock_update, mock_upload, _, _, _ = _run([])
        assert result is True
        mock_update.assert_not_called()
        mock_upload.assert_not_called()


# ---------------------------------------------------------------------------
# In-place update path (workout already has a garmin_workout_id)
# ---------------------------------------------------------------------------

class TestSyncDayInPlaceUpdate:

    def test_update_called_when_workout_has_garmin_id(self):
        """Workout with existing garmin_workout_id → update_workout is called."""
        w = _planned_workout(garmin_workout_id="555")
        result, mock_update, mock_upload, mock_sched, mock_rm, mock_del = _run([w])

        mock_update.assert_called_once()
        mock_upload.assert_not_called()
        mock_sched.assert_not_called()
        assert result is True

    def test_update_called_with_correct_workout_id(self):
        """update_workout must be called with the integer workout ID."""
        w = _planned_workout(garmin_workout_id="42")
        _, mock_update, _, _, _, _ = _run([w])

        call_args = mock_update.call_args
        assert call_args[0][1] == 42  # second positional arg is workout_id

    def test_update_does_not_delete_or_reschedule(self):
        """In-place update must NOT call delete_workout or remove_workout_schedule."""
        w = _planned_workout(garmin_workout_id="555", garmin_schedule_id="666")
        _, _, mock_upload, mock_sched, mock_rm, mock_del = _run([w])

        mock_del.assert_not_called()
        mock_rm.assert_not_called()
        mock_upload.assert_not_called()
        mock_sched.assert_not_called()

    def test_returns_true_on_successful_update(self):
        """Successful in-place update returns True."""
        w = _planned_workout(garmin_workout_id="777")
        result, _, _, _, _, _ = _run([w])
        assert result is True


# ---------------------------------------------------------------------------
# Fallback: delete+create when update fails
# ---------------------------------------------------------------------------

class TestSyncDayUpdateFallback:

    def test_upload_called_on_update_failure(self):
        """If update_workout raises, falls back to delete+create (upload called)."""
        w = _planned_workout(garmin_workout_id="555")
        _, _, mock_upload, _, _, _ = _run([w], update_side_effect=Exception("PUT 404"))

        mock_upload.assert_called_once()

    def test_schedule_called_after_upload_on_fallback(self):
        """After fallback upload, schedule_workout is called."""
        w = _planned_workout(garmin_workout_id="555")
        _, _, _, mock_sched, _, _ = _run([w], update_side_effect=Exception("PUT 404"))

        mock_sched.assert_called_once()

    def test_old_schedule_removed_on_fallback(self):
        """Old schedule entry is removed before re-upload on fallback."""
        w = _planned_workout(garmin_workout_id="555", garmin_schedule_id="999")
        _, _, _, _, mock_rm, _ = _run([w], update_side_effect=Exception("PUT 404"))

        mock_rm.assert_called()


# ---------------------------------------------------------------------------
# Fresh upload path (no garmin_workout_id)
# ---------------------------------------------------------------------------

class TestSyncDayFreshUpload:

    def test_upload_called_when_no_garmin_id(self):
        """Workout with no garmin_workout_id → upload_workout is called."""
        w = _planned_workout(garmin_workout_id=None)
        _, _, mock_upload, _, _, _ = _run([w])

        mock_upload.assert_called_once()

    def test_schedule_called_after_fresh_upload(self):
        """schedule_workout must be called after a successful fresh upload."""
        w = _planned_workout(garmin_workout_id=None)
        _, _, _, mock_sched, _, _ = _run([w])

        mock_sched.assert_called_once()

    def test_workout_id_stored_after_upload(self):
        """garmin_workout_id must be set on the workout object after upload."""
        w = _planned_workout(garmin_workout_id=None)
        _run([w], upload_return={"workoutId": 7777})

        assert w.garmin_workout_id == "7777"

    def test_schedule_id_stored_after_schedule(self):
        """garmin_schedule_id must be set on the workout object after scheduling."""
        w = _planned_workout(garmin_workout_id=None)
        target_date = TODAY + timedelta(days=3)
        db = _db_for_day(1, [w], target_date)

        with patch(f"{_MOD}.get_garmin_client") as mock_auth, \
             patch(f"{_MOD}.get_garmin_workout_library", return_value=[]), \
             patch(f"{_MOD}.update_workout"), \
             patch(f"{_MOD}.upload_workout", return_value={"workoutId": 100}), \
             patch(f"{_MOD}.schedule_workout", return_value={"workoutScheduleId": 200}), \
             patch(f"{_MOD}.remove_workout_schedule"), \
             patch(f"{_MOD}.delete_workout"):
            mock_auth.return_value = MagicMock()
            sync_day_to_garmin(1, "a@b.com", b"enc", target_date, db)

        assert w.garmin_schedule_id == "200"

    def test_returns_true_on_successful_upload(self):
        """Successful fresh upload returns True."""
        w = _planned_workout(garmin_workout_id=None)
        result, _, _, _, _, _ = _run([w])
        assert result is True

    def test_returns_false_on_upload_failure(self):
        """Failed upload returns False."""
        w = _planned_workout(garmin_workout_id=None)
        target_date = TODAY + timedelta(days=3)
        db = _db_for_day(1, [w], target_date)

        with patch(f"{_MOD}.get_garmin_client") as mock_auth, \
             patch(f"{_MOD}.get_garmin_workout_library", return_value=[]), \
             patch(f"{_MOD}.update_workout"), \
             patch(f"{_MOD}.upload_workout", side_effect=Exception("500")), \
             patch(f"{_MOD}.schedule_workout"), \
             patch(f"{_MOD}.remove_workout_schedule"), \
             patch(f"{_MOD}.delete_workout"):
            mock_auth.return_value = MagicMock()
            result = sync_day_to_garmin(1, "a@b.com", b"enc", target_date, db)

        assert result is False


# ---------------------------------------------------------------------------
# Stale marker cleanup on fresh upload
# ---------------------------------------------------------------------------

class TestSyncDayStaleMarkerCleanup:

    def test_stale_marked_entry_deleted_before_fresh_upload(self):
        """A library entry with our marker for this date is deleted before re-upload."""
        target_date = TODAY + timedelta(days=3)
        w = _planned_workout(garmin_workout_id=None, scheduled_date=target_date)
        stale_wid = 777
        library = [{"workoutId": stale_wid, "description": f"[rca:1:{target_date.isoformat()}]"}]

        _, _, _, _, _, mock_del = _run([w], target_date=target_date, library=library)

        deleted_ids = [c[0][1] for c in mock_del.call_args_list]
        assert stale_wid in deleted_ids

    def test_stale_entry_schedule_removed_via_stored_db_id(self):
        """If the workout has a stored schedule ID matching the stale entry, it is removed."""
        target_date = TODAY + timedelta(days=3)
        stale_wid = 777
        stale_sid = 555
        library = [{"workoutId": stale_wid, "description": f"[rca:1:{target_date.isoformat()}]"}]

        # Workout has the stored IDs (update-failure path: the stale entry IS this workout)
        w = _planned_workout(garmin_workout_id=str(stale_wid), garmin_schedule_id=str(stale_sid),
                             scheduled_date=target_date)

        _, _, _, _, mock_rm, _ = _run([w], target_date=target_date, library=library,
                                      update_side_effect=Exception("PUT 404"))

        removed_ids = [c[0][1] for c in mock_rm.call_args_list]
        assert stale_sid in removed_ids

    def test_different_athlete_marker_not_deleted(self):
        """Marker for a different athlete must not be deleted during our sync."""
        target_date = TODAY + timedelta(days=3)
        w = _planned_workout(athlete_id=1, garmin_workout_id=None, scheduled_date=target_date)
        library = [{"workoutId": 88, "description": f"[rca:99:{target_date.isoformat()}]"}]

        _, _, _, _, _, mock_del = _run([w], target_date=target_date, library=library)

        # Our upload will be called but the stale entry for athlete 99 must not be deleted
        deleted_ids = [c[0][1] for c in mock_del.call_args_list]
        assert 88 not in deleted_ids

    def test_fresh_upload_proceeds_after_stale_delete(self):
        """After deleting stale entry, the fresh upload must still be called."""
        target_date = TODAY + timedelta(days=3)
        w = _planned_workout(garmin_workout_id=None, scheduled_date=target_date)
        library = [{"workoutId": 777, "description": f"[rca:1:{target_date.isoformat()}]"}]

        _, _, mock_upload, _, _, _ = _run([w], target_date=target_date, library=library)

        mock_upload.assert_called_once()


# ---------------------------------------------------------------------------
# App marker in uploaded workout description
# ---------------------------------------------------------------------------

class TestSyncDayMarkerEmbedded:

    def test_uploaded_workout_has_app_marker(self):
        """Uploaded workout JSON must contain the [rca:...] marker."""
        import re
        w = _planned_workout(garmin_workout_id=None)
        _, _, mock_upload, _, _, _ = _run([w])

        assert mock_upload.called
        workout_json = mock_upload.call_args[0][1]
        desc = workout_json.get("description", "")
        assert re.search(r'\[rca:\d+:\d{4}-\d{2}-\d{2}\]', desc), (
            f"App marker missing from description: {desc!r}"
        )
