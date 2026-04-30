"""Tests for the rewritten _validate_garmin_sync (Changes 5+7).

The new implementation:
- Makes ONE bulk library scan call (not N per-workout calls)
- Detects three states and repairs each:
    1. Fully synced in DB but missing from library → clear both IDs, queue resync
    2. Library-only orphan (workout_id set, schedule_id NULL) AND in library
       → schedule_existing_workout called to repair without re-uploading
    3. Library-only orphan (workout_id set, schedule_id NULL) but NOT in library
       → clear both IDs, queue resync
- Auto-repair (resync) is bounded to workouts within the next 14 days
- Workouts beyond 14 days have IDs cleared but are left for the daily reconciliation job
- Silently swallows all errors — never blocks a conversation turn
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


TODAY = date.today()
_CONV = "running_coach_ai.coach.conversation"
_CLIENT = "running_coach_ai.garmin.client"
_WB = "running_coach_ai.garmin.workout_builder"

# _validate_garmin_sync uses local imports, so all patches must target source modules
_GET_CLIENT = f"{_CLIENT}.get_garmin_client"
_GET_LIBRARY = f"{_CLIENT}.get_garmin_workout_library"
_SCHED_EXISTING = f"{_CLIENT}.schedule_existing_workout"
_DAY_SYNC = f"{_WB}.sync_day_to_garmin"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _athlete(athlete_id: int = 1):
    a = MagicMock()
    a.id = athlete_id
    a.garmin_email = "a@b.com"
    a.garmin_password_encrypted = b"enc"
    return a


def _workout(
    days_ahead: int,
    athlete_id: int = 1,
    garmin_workout_id: str = None,
    garmin_schedule_id: str = None,
    workout_type: str = "easy",
):
    w = MagicMock()
    w.athlete_id = athlete_id
    w.scheduled_date = TODAY + timedelta(days=days_ahead)
    w.workout_type = workout_type
    w.garmin_workout_id = garmin_workout_id
    w.garmin_schedule_id = garmin_schedule_id
    return w


def _library_entry(athlete_id: int, days_ahead: int, workout_id: int) -> dict:
    d = (TODAY + timedelta(days=days_ahead)).isoformat()
    return {"workoutId": workout_id, "description": f"[rca:{athlete_id}:{d}]"}


def _run(athlete, workouts, library_entries, schedule_existing_return=None, day_sync_ok=True):
    """Run _validate_garmin_sync with mocked Garmin API."""
    from running_coach_ai.coach.conversation import _validate_garmin_sync

    db = MagicMock()
    db.commit.return_value = None

    with patch(_GET_CLIENT) as mock_auth, \
         patch(_GET_LIBRARY, return_value=library_entries), \
         patch(_SCHED_EXISTING, return_value=schedule_existing_return) as mock_sched, \
         patch(_DAY_SYNC, return_value=day_sync_ok) as mock_day_sync:
        mock_auth.return_value = MagicMock()
        _validate_garmin_sync(athlete, workouts, db)

    return db, mock_sched, mock_day_sync


# ---------------------------------------------------------------------------
# Bulk scan — single API call
# ---------------------------------------------------------------------------

class TestBulkLibraryScan:

    def test_single_library_call_regardless_of_workout_count(self):
        """One get_garmin_workout_library call no matter how many workouts."""
        athlete = _athlete()
        workouts = [
            _workout(days_ahead=i + 1, garmin_workout_id=str(100 + i), garmin_schedule_id=str(200 + i))
            for i in range(10)
        ]
        library = [_library_entry(1, i + 1, 100 + i) for i in range(10)]

        from running_coach_ai.coach.conversation import _validate_garmin_sync
        db = MagicMock()
        db.commit.return_value = None

        with patch(_GET_CLIENT) as mock_auth, \
             patch(_GET_LIBRARY, return_value=library) as mock_lib, \
             patch(_SCHED_EXISTING, return_value="801"), \
             patch(_DAY_SYNC, return_value=True):
            mock_auth.return_value = MagicMock()
            _validate_garmin_sync(athlete, workouts, db)

        mock_lib.assert_called_once()

    def test_empty_upcoming_does_nothing(self):
        """No upcoming workouts → no Garmin API calls."""
        athlete = _athlete()
        from running_coach_ai.coach.conversation import _validate_garmin_sync
        db = MagicMock()

        with patch(_GET_CLIENT) as mock_auth, \
             patch(_GET_LIBRARY) as mock_lib:
            mock_auth.return_value = MagicMock()
            _validate_garmin_sync(athlete, [], db)

        mock_lib.assert_not_called()


# ---------------------------------------------------------------------------
# State 1: Fully synced in DB but missing from Garmin library
# ---------------------------------------------------------------------------

class TestStaleSyncedWorkout:

    def test_stale_ids_cleared_when_not_in_library(self):
        """workout_id + schedule_id set in DB but not found in library → both cleared."""
        athlete = _athlete()
        w = _workout(days_ahead=3, garmin_workout_id="100", garmin_schedule_id="200")
        _, _, _ = _run(athlete, [w], library_entries=[])

        assert w.garmin_workout_id is None
        assert w.garmin_schedule_id is None

    def test_stale_workout_within_14_days_triggers_resync(self):
        """Stale workout within 14 days → sync_day_to_garmin called to repair."""
        athlete = _athlete()
        w = _workout(days_ahead=7, garmin_workout_id="100", garmin_schedule_id="200")
        _, _, mock_day_sync = _run(athlete, [w], library_entries=[])

        mock_day_sync.assert_called_once()
        assert mock_day_sync.call_args[0][3] == w.scheduled_date

    def test_stale_workout_beyond_14_days_not_resynced_immediately(self):
        """Stale workout beyond 14 days → IDs cleared but sync_day_to_garmin NOT called.
        The daily reconciliation job handles it."""
        athlete = _athlete()
        w = _workout(days_ahead=20, garmin_workout_id="100", garmin_schedule_id="200")
        _, _, mock_day_sync = _run(athlete, [w], library_entries=[])

        assert w.garmin_workout_id is None
        mock_day_sync.assert_not_called()

    def test_db_committed_after_clearing_ids(self):
        """DB must be committed after clearing stale IDs."""
        athlete = _athlete()
        w = _workout(days_ahead=3, garmin_workout_id="100", garmin_schedule_id="200")
        db, _, _ = _run(athlete, [w], library_entries=[])

        db.commit.assert_called()

    def test_in_sync_workout_not_touched(self):
        """Workout in library with both IDs set → no changes."""
        athlete = _athlete()
        w = _workout(days_ahead=3, garmin_workout_id="100", garmin_schedule_id="200")
        library = [_library_entry(1, 3, 100)]
        _, _, mock_day_sync = _run(athlete, [w], library_entries=library)

        assert w.garmin_workout_id == "100"
        assert w.garmin_schedule_id == "200"
        mock_day_sync.assert_not_called()


# ---------------------------------------------------------------------------
# State 2: Library-only orphan (workout_id set, schedule_id NULL) — in library
# ---------------------------------------------------------------------------

class TestLibraryOnlyOrphanRepair:

    def test_orphan_in_library_triggers_schedule_existing(self):
        """Library-only orphan that is still in the library → schedule_existing_workout called."""
        athlete = _athlete()
        w = _workout(days_ahead=3, garmin_workout_id="100", garmin_schedule_id=None)
        library = [_library_entry(1, 3, 100)]
        _, mock_sched, _ = _run(athlete, [w], library_entries=library, schedule_existing_return="801")

        mock_sched.assert_called_once()
        call_args = mock_sched.call_args[0]
        assert call_args[1] == 100  # workout_id passed as int
        assert call_args[2] == w.scheduled_date.isoformat()

    def test_successful_schedule_sets_schedule_id(self):
        """After schedule_existing_workout succeeds, garmin_schedule_id must be set."""
        athlete = _athlete()
        w = _workout(days_ahead=3, garmin_workout_id="100", garmin_schedule_id=None)
        library = [_library_entry(1, 3, 100)]
        _run(athlete, [w], library_entries=library, schedule_existing_return="801")

        assert w.garmin_schedule_id == "801"
        assert w.garmin_workout_id == "100"  # workout_id preserved

    def test_orphan_repair_only_within_14_days(self):
        """Library-only orphan beyond 14 days is NOT repaired via schedule — left for reconciliation."""
        athlete = _athlete()
        w = _workout(days_ahead=20, garmin_workout_id="100", garmin_schedule_id=None)
        library = [_library_entry(1, 20, 100)]
        _, mock_sched, mock_day_sync = _run(athlete, [w], library_entries=library)

        mock_sched.assert_not_called()
        mock_day_sync.assert_not_called()
        # IDs are not cleared either — just left for reconciliation
        assert w.garmin_workout_id == "100"

    def test_schedule_failure_clears_ids_and_queues_resync(self):
        """If schedule_existing_workout fails, clear IDs and queue a fresh upload via day sync."""
        athlete = _athlete()
        w = _workout(days_ahead=3, garmin_workout_id="100", garmin_schedule_id=None)
        library = [_library_entry(1, 3, 100)]

        from running_coach_ai.coach.conversation import _validate_garmin_sync
        db = MagicMock()
        db.commit.return_value = None

        with patch(_GET_CLIENT) as mock_auth, \
             patch(_GET_LIBRARY, return_value=library), \
             patch(_SCHED_EXISTING, side_effect=Exception("502")), \
             patch(_DAY_SYNC, return_value=True) as mock_day_sync:
            mock_auth.return_value = MagicMock()
            _validate_garmin_sync(athlete, [w], db)

        assert w.garmin_workout_id is None
        assert w.garmin_schedule_id is None
        mock_day_sync.assert_called_once()


# ---------------------------------------------------------------------------
# State 3: Library-only orphan NOT in library
# ---------------------------------------------------------------------------

class TestLibraryOnlyOrphanStale:

    def test_orphan_not_in_library_clears_workout_id(self):
        """Library-only orphan where workout_id is not in library → workout_id cleared."""
        athlete = _athlete()
        w = _workout(days_ahead=3, garmin_workout_id="999", garmin_schedule_id=None)
        # Library is empty — workout 999 doesn't exist there anymore
        _, mock_sched, _ = _run(athlete, [w], library_entries=[])

        assert w.garmin_workout_id is None
        mock_sched.assert_not_called()

    def test_stale_orphan_within_14_days_triggers_resync(self):
        """Stale orphan within 14 days → sync_day_to_garmin called."""
        athlete = _athlete()
        w = _workout(days_ahead=5, garmin_workout_id="999", garmin_schedule_id=None)
        _, _, mock_day_sync = _run(athlete, [w], library_entries=[])

        mock_day_sync.assert_called_once()

    def test_stale_orphan_beyond_14_days_no_immediate_resync(self):
        """Stale orphan beyond 14 days → left for reconciliation, no immediate day sync."""
        athlete = _athlete()
        w = _workout(days_ahead=20, garmin_workout_id="999", garmin_schedule_id=None)
        _, _, mock_day_sync = _run(athlete, [w], library_entries=[])

        mock_day_sync.assert_not_called()


# ---------------------------------------------------------------------------
# Unsynced workouts (both IDs NULL) — not touched
# ---------------------------------------------------------------------------

class TestUnsyncedWorkoutsNotTouched:

    def test_fully_unsynced_workout_not_touched_by_validate(self):
        """Workout with both IDs NULL is already in the correct state — validate ignores it."""
        athlete = _athlete()
        w = _workout(days_ahead=3, garmin_workout_id=None, garmin_schedule_id=None)
        _, mock_sched, mock_day_sync = _run(athlete, [w], library_entries=[])

        # validate does nothing — reconciliation handles it
        mock_sched.assert_not_called()
        mock_day_sync.assert_not_called()
        assert w.garmin_workout_id is None


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------

class TestValidateSyncErrorResilience:

    def test_garmin_api_error_does_not_raise(self):
        """If Garmin API call fails entirely, _validate_garmin_sync must not raise."""
        from running_coach_ai.coach.conversation import _validate_garmin_sync

        athlete = _athlete()
        w = _workout(days_ahead=3, garmin_workout_id="100", garmin_schedule_id="200")
        db = MagicMock()

        with patch(_GET_CLIENT, side_effect=Exception("Auth failed")):
            _validate_garmin_sync(athlete, [w], db)  # must not raise

        # IDs should be unchanged since we never got to the validation logic
        assert w.garmin_workout_id == "100"
        assert w.garmin_schedule_id == "200"

    def test_resync_failure_does_not_raise(self):
        """If sync_day_to_garmin raises during repair, _validate_garmin_sync must not raise."""
        from running_coach_ai.coach.conversation import _validate_garmin_sync

        athlete = _athlete()
        w = _workout(days_ahead=3, garmin_workout_id="100", garmin_schedule_id="200")
        db = MagicMock()
        db.commit.return_value = None

        with patch(_GET_CLIENT) as mock_auth, \
             patch(_GET_LIBRARY, return_value=[]), \
             patch(_DAY_SYNC, side_effect=Exception("Garmin down")), \
             patch(_SCHED_EXISTING, return_value=None):
            mock_auth.return_value = MagicMock()
            _validate_garmin_sync(athlete, [w], db)  # must not raise

    def test_different_athlete_marker_not_matched(self):
        """Library entry with different athlete_id must not match this athlete's workouts."""
        athlete = _athlete(athlete_id=1)
        w = _workout(days_ahead=3, garmin_workout_id="100", garmin_schedule_id="200",
                     athlete_id=1)
        # Library entry has athlete_id=99 — must not be matched
        other_library = [{"workoutId": 100,
                          "description": f"[rca:99:{(TODAY + timedelta(days=3)).isoformat()}]"}]

        # From athlete 1's perspective, the workout is missing from the library
        _, _, _ = _run(athlete, [w], library_entries=other_library)

        # Workout should be treated as missing from library (IDs cleared)
        assert w.garmin_workout_id is None
        assert w.garmin_schedule_id is None
