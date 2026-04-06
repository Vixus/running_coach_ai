"""Tests for !admin resync-garmin — marker-based wipe-and-resync behavior.

_resync_garmin now deletes only app-created (marked) workouts from the Garmin
library, not every calendar entry.  This preserves manually created workouts
while still cleaning up everything the coach created.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from running_coach_ai.slack.admin import _resync_garmin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_athlete(slack_user_id="U123"):
    a = MagicMock()
    a.id = 1
    a.slack_user_id = slack_user_id
    a.name = "Test Athlete"
    a.garmin_email = "athlete@example.com"
    a.garmin_password_encrypted = b"encrypted"
    return a


def _make_db(athlete, upcoming_dates, garmin_id_pairs=None):
    """garmin_id_pairs: list of (workout_id, schedule_id) for the DB ID map query."""
    db = MagicMock()
    athlete_query = MagicMock()
    athlete_query.filter.return_value.first.return_value = athlete
    date_rows = [MagicMock(scheduled_date=d) for d in upcoming_dates]
    workout_query = MagicMock()
    workout_query.filter.return_value.distinct.return_value.all.return_value = date_rows
    workout_query.filter.return_value.update.return_value = len(upcoming_dates)
    id_workouts = []
    for wid, sid in (garmin_id_pairs or []):
        w = MagicMock()
        w.garmin_workout_id = str(wid)
        w.garmin_schedule_id = str(sid)
        id_workouts.append(w)
    workout_query.filter.return_value.all.return_value = id_workouts

    def _side(model):
        from running_coach_ai.database.models import Athlete
        return athlete_query if model is Athlete else workout_query

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
    """Run _resync_garmin with mocked Garmin library + DB workout IDs."""
    athlete = _make_athlete()
    # Derive DB workout objects from cal_entries (replacing broken calendar API)
    garmin_id_pairs = [
        (e["workoutId"], e.get("workoutScheduleId", 0))
        for e in cal_entries
        if e.get("workoutId") and e.get("workoutScheduleId")
    ]
    db = _make_db(athlete, upcoming_dates, garmin_id_pairs)

    with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
         patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=library_entries), \
         patch("running_coach_ai.garmin.client.remove_workout_schedule") as mock_rm, \
         patch("running_coach_ai.garmin.client.delete_workout") as mock_del, \
         patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", return_value=sync_return):
        mock_auth.return_value = MagicMock()
        result = _resync_garmin("U123", None, db, confirmed=True)

    return result, mock_rm, mock_del


# ---------------------------------------------------------------------------
# Marker-based deletion: only app-created workouts are removed
# ---------------------------------------------------------------------------

class TestResyncMarkerBased:

    def test_marked_workout_on_active_date_is_deleted(self):
        """Library entry with our marker is deleted — sync then re-uploads it fresh."""
        today = date.today()
        active_date = today + timedelta(days=3)
        library = [_library_entry(1, active_date, 88)]
        cal = [_calendar_entry(88, 99)]

        result, mock_rm, mock_del = _run(library, cal, [active_date])

        mock_rm.assert_called_once()
        mock_del.assert_called_once()
        assert "Deleted 1" in result

    def test_unmarked_calendar_entry_is_not_deleted(self):
        """A calendar entry that has NO library marker must NOT be deleted."""
        today = date.today()
        active_date = today + timedelta(days=3)
        # Calendar has an entry but the library has no matching marked workout
        cal = [{"workoutId": 88, "workoutScheduleId": 99, "date": active_date.isoformat()}]
        library = []  # No marked entries

        result, mock_rm, mock_del = _run(library, cal, [active_date])

        mock_rm.assert_not_called()
        mock_del.assert_not_called()
        assert "Deleted 0" in result

    def test_different_athlete_marker_not_deleted(self):
        """Library entry marked for a different athlete must not be deleted."""
        today = date.today()
        active_date = today + timedelta(days=3)
        # Marker has athlete_id=99, but we're resyncing athlete 1
        library = [{"workoutId": 88, "description": f"[rca:99:{active_date.isoformat()}]"}]
        cal = [_calendar_entry(88, 99)]

        result, mock_rm, mock_del = _run(library, cal, [active_date])

        mock_rm.assert_not_called()
        mock_del.assert_not_called()

    def test_multiple_marked_workouts_all_deleted(self):
        """Multiple marked library entries are all deleted before re-sync."""
        today = date.today()
        d1 = today + timedelta(days=1)
        d2 = today + timedelta(days=3)
        library = [_library_entry(1, d1, 10), _library_entry(1, d2, 20)]
        cal = [_calendar_entry(10, 100), _calendar_entry(20, 200)]

        result, mock_rm, mock_del = _run(library, cal, [d1, d2])

        assert mock_rm.call_count == 2
        assert mock_del.call_count == 2
        assert "Deleted 2" in result

    def test_marked_workout_without_schedule_deletes_library_only(self):
        """Marked workout not on calendar: only delete_workout called, not remove_schedule."""
        today = date.today()
        active_date = today + timedelta(days=3)
        library = [_library_entry(1, active_date, 77)]
        cal = []  # Not on calendar

        result, mock_rm, mock_del = _run(library, cal, [active_date])

        mock_rm.assert_not_called()
        mock_del.assert_called_once()

    def test_delete_failure_does_not_abort_sync(self):
        """A delete failure must not stop the subsequent upload."""
        today = date.today()
        active_date = today + timedelta(days=3)
        library = [_library_entry(1, active_date, 88)]
        cal = [_calendar_entry(88, 99)]
        athlete = _make_athlete()
        db = _make_db(athlete, [active_date], [(88, 99)])

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=library), \
             patch("running_coach_ai.garmin.client.remove_workout_schedule", side_effect=Exception("500")), \
             patch("running_coach_ai.garmin.client.delete_workout", side_effect=Exception("500")), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   return_value=(1, 0, [])) as mock_sync:
            mock_auth.return_value = MagicMock()
            result = _resync_garmin("U123", None, db, confirmed=True)

        mock_sync.assert_called_once()
        assert "Synced 1" in result

    def test_no_marked_workouts_runs_sync_normally(self):
        """Empty library (no marked workouts) — sync still runs for DB workouts."""
        today = date.today()
        result, mock_rm, mock_del = _run([], [], [today + timedelta(days=3)])

        mock_rm.assert_not_called()
        mock_del.assert_not_called()
        assert "Synced" in result

    def test_result_message_includes_deleted_and_synced_counts(self):
        """Response message must report how many were deleted and how many synced."""
        today = date.today()
        d1 = today + timedelta(days=1)
        d2 = today + timedelta(days=3)
        library = [_library_entry(1, d1, 10), _library_entry(1, d2, 20)]
        cal = [_calendar_entry(10, 100), _calendar_entry(20, 200)]

        result, _, _ = _run(library, cal, [d1, d2], sync_return=(2, 0, []))

        assert "2" in result   # deleted count
        assert "2" in result   # synced count
