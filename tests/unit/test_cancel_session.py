"""Tests for session cancellation → Garmin deletion flow."""

import json
from datetime import date
from unittest.mock import MagicMock, patch


from running_coach_ai.coach.conversation import (
    _delete_garmin_workout,
    _reconcile_cancelled_garmin_workouts,
    extract_and_apply_plan,
)


def _make_workout(status="planned", garmin_workout_id="111", garmin_schedule_id="222"):
    w = MagicMock()
    w.status = status
    w.garmin_workout_id = garmin_workout_id
    w.garmin_schedule_id = garmin_schedule_id
    w.scheduled_date = date(2026, 4, 10)
    return w


def _make_db(workout):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = workout
    return db


def _plan_response(session_date: str, status: str, reason: str = "athlete request") -> str:
    payload = {
        "sessions": [
            {
                "date": session_date,
                "status": status,
                "reason": reason,
            }
        ]
    }
    return f"Sure, I've cancelled that session.\n<plan>{json.dumps(payload)}</plan>"


# ---------------------------------------------------------------------------
# The core deletion path (via extract_and_apply_plan)
# ---------------------------------------------------------------------------

class TestCancelDeletesFromGarmin:

    def test_cancelled_status_triggers_garmin_deletion(self):
        """When Claude sets status='cancelled', the workout must be removed from Garmin."""
        workout = _make_workout(status="planned")
        db = _make_db(workout)
        response = _plan_response("2026-04-10", "cancelled")

        with patch("running_coach_ai.coach.conversation._delete_garmin_workout") as mock_delete, \
             patch("running_coach_ai.coach.conversation._resync_garmin_workout") as mock_resync:
            extract_and_apply_plan(1, response, db)

        mock_delete.assert_called_once()
        mock_resync.assert_not_called()

    def test_skipped_status_triggers_garmin_deletion(self):
        """When Claude sets status='skipped', the workout must be removed from Garmin."""
        workout = _make_workout(status="planned")
        db = _make_db(workout)
        response = _plan_response("2026-04-10", "skipped")

        with patch("running_coach_ai.coach.conversation._delete_garmin_workout") as mock_delete, \
             patch("running_coach_ai.coach.conversation._resync_garmin_workout") as mock_resync:
            extract_and_apply_plan(1, response, db)

        mock_delete.assert_called_once()
        mock_resync.assert_not_called()

    def test_modified_status_resyncs_not_deletes(self):
        """Modifying a session should trigger a targeted day sync, not a deletion."""
        workout = _make_workout(status="planned")
        athlete = MagicMock()
        athlete.garmin_email = "a@b.com"
        athlete.garmin_password_encrypted = b"enc"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = workout
        db.query.return_value.get.return_value = athlete

        payload = {"sessions": [{"date": "2026-04-10", "status": "modified", "target_distance_km": 8.0}]}
        response = f"Adjusted your session.\n<plan>{json.dumps(payload)}</plan>"

        with patch("running_coach_ai.coach.conversation._delete_garmin_workout") as mock_delete, \
             patch("running_coach_ai.garmin.workout_builder.sync_day_to_garmin", return_value=True) as mock_sync:
            extract_and_apply_plan(1, response, db)

        mock_delete.assert_not_called()
        mock_sync.assert_called_once()


# ---------------------------------------------------------------------------
# Garmin ID edge cases
# ---------------------------------------------------------------------------

class TestGarminIdGating:

    def test_deletion_fires_when_only_schedule_id_set(self):
        """Workout on calendar (schedule_id only, no workout_id) must still be deleted."""
        workout = _make_workout(status="planned", garmin_workout_id=None, garmin_schedule_id="222")
        db = _make_db(workout)
        response = _plan_response("2026-04-10", "cancelled")

        with patch("running_coach_ai.coach.conversation._delete_garmin_workout") as mock_delete:
            extract_and_apply_plan(1, response, db)

        mock_delete.assert_called_once()

    def test_no_garmin_ids_skips_garmin_call(self):
        """Workout never uploaded to Garmin — no delete attempt needed."""
        workout = _make_workout(status="planned", garmin_workout_id=None, garmin_schedule_id=None)
        db = _make_db(workout)
        response = _plan_response("2026-04-10", "cancelled")

        with patch("running_coach_ai.coach.conversation._delete_garmin_workout") as mock_delete:
            extract_and_apply_plan(1, response, db)

        mock_delete.assert_not_called()


# ---------------------------------------------------------------------------
# _delete_garmin_workout: IDs only cleared on success or 404
# ---------------------------------------------------------------------------

class TestDeleteGarminWorkoutIdRetention:

    def _make_athlete(self):
        a = MagicMock()
        a.garmin_email = "test@example.com"
        a.garmin_password_encrypted = b"encrypted"
        return a

    def test_ids_cleared_on_success(self):
        """Both IDs must be cleared when API calls succeed."""
        workout = _make_workout()
        db = MagicMock()
        db.query.return_value.get.return_value = self._make_athlete()

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_ggc, \
             patch("running_coach_ai.garmin.client.remove_workout_schedule"), \
             patch("running_coach_ai.garmin.client.delete_workout"):
            mock_ggc.return_value = MagicMock()
            _delete_garmin_workout(1, workout, db)

        assert workout.garmin_schedule_id is None
        assert workout.garmin_workout_id is None

    def test_ids_retained_on_api_failure(self):
        """When Garmin API calls fail (non-404), IDs must be kept for retry."""
        workout = _make_workout()
        db = MagicMock()
        db.query.return_value.get.return_value = self._make_athlete()

        def boom(*a, **kw):
            raise Exception("500 Server Error")

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_ggc, \
             patch("running_coach_ai.garmin.client.remove_workout_schedule", side_effect=boom), \
             patch("running_coach_ai.garmin.client.delete_workout", side_effect=boom):
            mock_ggc.return_value = MagicMock()
            _delete_garmin_workout(1, workout, db)

        assert workout.garmin_schedule_id == "222"
        assert workout.garmin_workout_id == "111"

    def test_ids_cleared_on_404(self):
        """404 means already gone — IDs should be cleared even though the call 'failed'."""
        workout = _make_workout()
        db = MagicMock()
        db.query.return_value.get.return_value = self._make_athlete()

        def not_found(*a, **kw):
            raise Exception("404 Not Found")

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_ggc, \
             patch("running_coach_ai.garmin.client.remove_workout_schedule", side_effect=not_found), \
             patch("running_coach_ai.garmin.client.delete_workout", side_effect=not_found):
            mock_ggc.return_value = MagicMock()
            _delete_garmin_workout(1, workout, db)

        assert workout.garmin_schedule_id is None
        assert workout.garmin_workout_id is None


# ---------------------------------------------------------------------------
# _reconcile_cancelled_garmin_workouts: retries lingering IDs
# ---------------------------------------------------------------------------

class TestReconciliation:

    def test_reconcile_deletes_lingering_cancelled_workouts(self):
        """Cancelled workouts with Garmin IDs still set must be retried."""
        w1 = _make_workout(status="cancelled", garmin_workout_id="111", garmin_schedule_id="222")
        w2 = _make_workout(status="skipped", garmin_workout_id="333", garmin_schedule_id=None)

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [w1, w2]

        with patch("running_coach_ai.coach.conversation._delete_garmin_workout") as mock_delete:
            _reconcile_cancelled_garmin_workouts(1, db)

        assert mock_delete.call_count == 2

    def test_reconcile_no_op_when_nothing_lingering(self):
        """If no cancelled workouts have Garmin IDs, nothing should happen."""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        with patch("running_coach_ai.coach.conversation._delete_garmin_workout") as mock_delete:
            _reconcile_cancelled_garmin_workouts(1, db)

        mock_delete.assert_not_called()
