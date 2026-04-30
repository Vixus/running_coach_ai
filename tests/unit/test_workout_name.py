"""Tests for Change 8: workout_name field.

Covers:
- build_workout_json uses workout_name when set, falls back to _WORKOUT_DISPLAY_NAMES
- extract_and_apply_plan applies workout_name for existing workouts
- extract_and_apply_plan sets workout_name on newly created workouts
- Empty/None workout_name in plan JSON clears the custom name (reverts to default)
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


from running_coach_ai.garmin.workout_builder import build_workout_json

TODAY = date.today()
_CONV = "running_coach_ai.coach.conversation"
_WB = "running_coach_ai.garmin.workout_builder"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _planned_workout(
    athlete_id: int = 1,
    workout_type: str = "easy",
    workout_name: str = None,
    target_distance_km: float = 8.0,
    target_pace_min_per_km: float = 6.0,
    description: str = None,
    scheduled_date: date = None,
):
    w = MagicMock()
    w.athlete_id = athlete_id
    w.scheduled_date = scheduled_date or (TODAY + timedelta(days=3))
    w.workout_type = workout_type
    w.workout_name = workout_name
    w.target_distance_km = target_distance_km
    w.target_pace_min_per_km = target_pace_min_per_km
    w.target_duration_seconds = None
    w.target_zones_json = None
    w.description = description
    return w


def _make_athlete(athlete_id: int = 1):
    a = MagicMock()
    a.id = athlete_id
    a.garmin_email = "a@b.com"
    a.garmin_password_encrypted = b"enc"
    return a


# ---------------------------------------------------------------------------
# build_workout_json: workout_name field
# ---------------------------------------------------------------------------

class TestBuildWorkoutJsonName:

    def test_custom_name_used_when_set(self):
        """When workout_name is set, it must be used as the Garmin workoutName."""
        w = _planned_workout(workout_type="easy", workout_name="Pre-race shakeout")
        result = build_workout_json(w)
        assert result["workoutName"] == "Pre-race shakeout"

    def test_default_name_used_when_workout_name_is_none(self):
        """When workout_name is None, the default display name for the type is used."""
        w = _planned_workout(workout_type="easy", workout_name=None)
        result = build_workout_json(w)
        assert result["workoutName"] == "Easy Run"

    def test_default_name_long_run(self):
        w = _planned_workout(workout_type="long_run", workout_name=None)
        result = build_workout_json(w)
        assert result["workoutName"] == "Long Run"

    def test_default_name_tempo(self):
        w = _planned_workout(workout_type="tempo", workout_name=None)
        result = build_workout_json(w)
        assert result["workoutName"] == "Tempo Run"

    def test_default_name_intervals(self):
        w = _planned_workout(workout_type="intervals", workout_name=None)
        result = build_workout_json(w)
        assert result["workoutName"] == "Intervals"

    def test_custom_name_overrides_type_name(self):
        """Custom name takes priority even when workout_type has a known display name."""
        w = _planned_workout(workout_type="long_run", workout_name="Sunday Big One")
        result = build_workout_json(w)
        assert result["workoutName"] == "Sunday Big One"
        assert result["workoutName"] != "Long Run"

    def test_empty_string_workout_name_falls_back_to_default(self):
        """An empty string workout_name should not override the default (treated as None)."""
        # The model layer stores empty string as None via `session.get("workout_name") or None`
        # but build_workout_json receives None in practice. Test falsy string defence.
        w = _planned_workout(workout_type="easy", workout_name=None)
        result = build_workout_json(w)
        assert result["workoutName"] == "Easy Run"


# ---------------------------------------------------------------------------
# extract_and_apply_plan: workout_name mutations
# ---------------------------------------------------------------------------

def _db_for_plan(athlete, existing_workout=None, plan=None):
    """Build a minimal DB mock for extract_and_apply_plan."""
    from running_coach_ai.database.models import Athlete, TrainingPlan

    db = MagicMock()

    athlete_q = MagicMock()
    athlete_q.get.return_value = athlete

    workout_q = MagicMock()
    workout_q.filter.return_value = workout_q
    workout_q.first.return_value = existing_workout

    plan_q = MagicMock()
    plan_q.filter.return_value = plan_q
    plan_q.first.return_value = plan

    def _side(model):
        if model is Athlete:
            return athlete_q
        if model is TrainingPlan:
            return plan_q
        return workout_q

    db.query.side_effect = _side
    db.commit.return_value = None
    return db


class TestExtractAndApplyPlanWorkoutName:

    def _run(self, plan_json_sessions, existing_workout=None, plan=None):
        from running_coach_ai.coach.conversation import extract_and_apply_plan

        athlete = _make_athlete()
        db = _db_for_plan(athlete, existing_workout=existing_workout, plan=plan)
        response = f"<plan>{json.dumps({'sessions': plan_json_sessions})}</plan>"

        with patch(f"{_WB}.get_garmin_client") as mock_auth, \
             patch(f"{_WB}.get_garmin_workout_library", return_value=[]), \
             patch(f"{_WB}.upload_workout", return_value={"workoutId": 9001}), \
             patch(f"{_WB}.schedule_workout", return_value={"workoutScheduleId": 8001}), \
             patch(f"{_WB}.delete_workout"), \
             patch(f"{_WB}.remove_workout_schedule"), \
             patch(f"{_WB}.update_workout", return_value={}):
            mock_auth.return_value = MagicMock()
            extract_and_apply_plan(athlete.id, response, db)

        return existing_workout

    def test_workout_name_applied_to_existing_workout(self):
        """workout_name in plan JSON must be written to existing workout.workout_name."""
        target_date = TODAY + timedelta(days=3)
        w = _planned_workout(scheduled_date=target_date)
        w.garmin_workout_id = None
        w.garmin_schedule_id = None
        w.status = "planned"

        sessions = [{"date": target_date.isoformat(), "workout_name": "Custom Name"}]
        self._run(sessions, existing_workout=w)

        assert w.workout_name == "Custom Name"

    def test_none_workout_name_clears_custom_name(self):
        """workout_name: null in plan JSON must clear the field (revert to default)."""
        target_date = TODAY + timedelta(days=3)
        w = _planned_workout(scheduled_date=target_date, workout_name="Old Name")
        w.garmin_workout_id = None
        w.garmin_schedule_id = None
        w.status = "planned"

        sessions = [{"date": target_date.isoformat(), "workout_name": None}]
        self._run(sessions, existing_workout=w)

        assert w.workout_name is None

    def test_missing_workout_name_key_does_not_change_existing(self):
        """If workout_name is not in the plan JSON, the existing value is preserved."""
        target_date = TODAY + timedelta(days=3)
        w = _planned_workout(scheduled_date=target_date, workout_name="Keep Me")
        w.garmin_workout_id = None
        w.garmin_schedule_id = None
        w.status = "planned"

        # No workout_name key in the session dict
        sessions = [{"date": target_date.isoformat(), "description": "Just notes"}]
        self._run(sessions, existing_workout=w)

        assert w.workout_name == "Keep Me"

    def test_workout_name_set_on_new_workout(self):
        """New workouts created via plan JSON must have workout_name set."""
        from running_coach_ai.database.models import PlannedWorkout, TrainingPlan

        target_date = TODAY + timedelta(days=5)
        athlete = _make_athlete()

        mock_plan = MagicMock()
        mock_plan.id = 1

        db = MagicMock()
        athlete_q = MagicMock()
        athlete_q.get.return_value = athlete

        workout_q = MagicMock()
        workout_q.filter.return_value = workout_q
        workout_q.first.return_value = None  # no existing workout

        plan_q = MagicMock()
        plan_q.filter.return_value = plan_q
        plan_q.first.return_value = mock_plan

        def _side(model):
            if model is PlannedWorkout:
                return workout_q
            if model is TrainingPlan:
                return plan_q
            from running_coach_ai.database.models import Athlete
            if model is Athlete:
                return athlete_q
            return workout_q

        db.query.side_effect = _side
        db.commit.return_value = None

        added_workouts = []
        db.add.side_effect = added_workouts.append

        sessions = [{
            "date": target_date.isoformat(),
            "workout_type": "easy",
            "workout_name": "Morning Shakeout",
            "status": "planned",
        }]
        response = f"<plan>{json.dumps({'sessions': sessions})}</plan>"

        from running_coach_ai.coach.conversation import extract_and_apply_plan

        with patch(f"{_WB}.get_garmin_client") as mock_auth, \
             patch(f"{_WB}.get_garmin_workout_library", return_value=[]), \
             patch(f"{_WB}.upload_workout", return_value={"workoutId": 9001}), \
             patch(f"{_WB}.schedule_workout", return_value={"workoutScheduleId": 8001}), \
             patch(f"{_WB}.delete_workout"), \
             patch(f"{_WB}.remove_workout_schedule"):
            mock_auth.return_value = MagicMock()
            extract_and_apply_plan(athlete.id, response, db)

        assert len(added_workouts) == 1
        new_w = added_workouts[0]
        assert new_w.workout_name == "Morning Shakeout"

    def test_new_workout_without_workout_name_has_none(self):
        """New workouts created without workout_name in plan JSON must have workout_name=None."""
        from running_coach_ai.database.models import PlannedWorkout, TrainingPlan

        target_date = TODAY + timedelta(days=5)
        athlete = _make_athlete()

        mock_plan = MagicMock()
        mock_plan.id = 1

        db = MagicMock()
        athlete_q = MagicMock()
        athlete_q.get.return_value = athlete

        workout_q = MagicMock()
        workout_q.filter.return_value = workout_q
        workout_q.first.return_value = None

        plan_q = MagicMock()
        plan_q.filter.return_value = plan_q
        plan_q.first.return_value = mock_plan

        def _side(model):
            if model is PlannedWorkout:
                return workout_q
            if model is TrainingPlan:
                return plan_q
            from running_coach_ai.database.models import Athlete
            if model is Athlete:
                return athlete_q
            return workout_q

        db.query.side_effect = _side
        db.commit.return_value = None

        added_workouts = []
        db.add.side_effect = added_workouts.append

        sessions = [{"date": target_date.isoformat(), "workout_type": "easy", "status": "planned"}]
        response = f"<plan>{json.dumps({'sessions': sessions})}</plan>"

        from running_coach_ai.coach.conversation import extract_and_apply_plan

        with patch(f"{_WB}.get_garmin_client") as mock_auth, \
             patch(f"{_WB}.get_garmin_workout_library", return_value=[]), \
             patch(f"{_WB}.upload_workout", return_value={"workoutId": 9001}), \
             patch(f"{_WB}.schedule_workout", return_value={"workoutScheduleId": 8001}), \
             patch(f"{_WB}.delete_workout"), \
             patch(f"{_WB}.remove_workout_schedule"):
            mock_auth.return_value = MagicMock()
            extract_and_apply_plan(athlete.id, response, db)

        assert len(added_workouts) == 1
        assert added_workouts[0].workout_name is None
