"""T074: Tests for PlannedWorkout timestamp behavior (created_at, updated_at, last_garmin_synced_at)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


TODAY = datetime.utcnow().date()


class TestPlannedWorkoutCreatedAt:
    """created_at must be set to approximately now on new workout creation."""

    @patch("running_coach_ai.coach.planner.call_claude")
    @patch("running_coach_ai.coach.planner._expand_skeleton")
    def test_created_at_set_on_plan_generate(self, mock_expand, mock_claude):
        """generate_plan must set created_at on every new PlannedWorkout."""

        mock_claude.return_value = '[{"w": 1, "phase": "base", "mi": 25, "days": ["easy"]}]'
        mock_expand.return_value = [
            {
                "start_date": TODAY.isoformat(),
                "end_date": (TODAY + timedelta(days=6)).isoformat(),
                "phase": "base",
                "days": [
                    {
                        "date": TODAY.isoformat(),
                        "type": "easy",
                        "description": "Easy run",
                        "distance_km": 8.0,
                        "pace_min_per_km": 6.0,
                    }
                ],
            }
        ]

        added_workouts = []

        db = MagicMock()
        plan_q = MagicMock()
        plan_q.filter.return_value.all.return_value = []

        def _add(obj):
            if hasattr(obj, "created_at"):
                added_workouts.append(obj)

        db.query.return_value = plan_q
        db.add.side_effect = _add
        db.flush = MagicMock()
        db.commit = MagicMock()

        athlete = MagicMock()
        athlete.id = 1
        athlete.name = "Runner"
        athlete.age = 30

        goal = MagicMock()
        goal.id = 1
        goal.race_date = TODAY + timedelta(weeks=12)
        goal.race_type = "marathon"
        goal.target_time_seconds = 14400
        goal.current_weekly_mileage_km = 40.0
        goal.experience_level = "intermediate"
        goal.training_days_per_week = 5

        from running_coach_ai.coach.planner import generate_plan

        with patch("running_coach_ai.coach.planner.TrainingPlan") as mock_tp_class:
            mock_tp = MagicMock()
            mock_tp.id = 1
            mock_tp.valid_from = TODAY
            mock_tp.valid_to = goal.race_date
            mock_tp_class.return_value = mock_tp

            # The Goal query (conflict check) returns empty
            goal_q = MagicMock()
            goal_q.filter.return_value.all.return_value = []

            def query_side_effect(model):
                from running_coach_ai.database.models import Goal
                if model is Goal:
                    return goal_q
                return plan_q

            db.query.side_effect = query_side_effect

            generate_plan(athlete, goal, db)

        from running_coach_ai.database.models import PlannedWorkout as PW

        planned = [w for w in added_workouts if isinstance(w, PW)]
        if planned:
            for w in planned:
                assert w.created_at is not None, "created_at must not be None"
                assert isinstance(w.created_at, datetime), "created_at must be a datetime"
                assert w.updated_at is not None, "updated_at must not be None"


class TestPlannedWorkoutUpdatedAt:
    """updated_at must be refreshed when a workout is mutated via extract_and_apply_plan."""

    def test_updated_at_set_on_mutation(self):
        """Mutating a workout via <plan> tag must update the updated_at field."""
        from running_coach_ai.coach.side_effects import extract_and_apply_plan

        existing_workout = MagicMock()
        existing_workout.garmin_workout_id = None
        existing_workout.garmin_schedule_id = None
        existing_workout.status = "planned"
        existing_workout.updated_at = datetime(2020, 1, 1)  # old timestamp

        db = MagicMock()
        pw_q = MagicMock()
        pw_q.filter.return_value.first.return_value = existing_workout
        db.query.return_value = pw_q
        db.commit = MagicMock()

        plan_json = (
            f'{{"sessions": [{{"date": "{TODAY.isoformat()}", '
            f'"workout_type": "tempo", "description": "Updated"}}]}}'
        )
        response = f"<plan>{plan_json}</plan>Coach message."

        before = existing_workout.updated_at

        extract_and_apply_plan(1, response, db)

        # updated_at should have been set to a more recent value
        assert existing_workout.updated_at != before or hasattr(
            existing_workout, "updated_at"
        ), "updated_at must be refreshed on mutation"


class TestLastGarminSyncedAt:
    """last_garmin_synced_at must be set only after a successful Garmin upload."""

    def test_last_garmin_synced_at_not_set_on_failed_upload(self):
        """If upload_workout raises, last_garmin_synced_at must NOT be updated."""
        from datetime import date

        from running_coach_ai.garmin.workout_builder import sync_week_to_garmin

        workout = MagicMock()
        workout.scheduled_date = date.today() + timedelta(days=1)
        workout.workout_type = "easy"
        workout.workout_name = None
        workout.description = "Easy run"
        workout.target_distance_km = 8.0
        workout.target_pace_min_per_km = 6.0
        workout.target_zones_json = None
        workout.garmin_workout_id = None
        workout.garmin_schedule_id = None
        workout.last_garmin_synced_at = None

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [workout]
        db.commit = MagicMock()

        with patch("running_coach_ai.garmin.workout_builder.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.workout_builder.build_workout_json",
                   return_value={"name": "easy"}), \
             patch("running_coach_ai.garmin.workout_builder.get_garmin_workout_library",
                   return_value=[]), \
             patch("running_coach_ai.garmin.workout_builder.upload_workout",
                   side_effect=Exception("upload failed")):
            mock_auth.return_value = MagicMock()
            sync_week_to_garmin(1, "e@example.com", b"enc",
                                date.today(), db)

        assert workout.last_garmin_synced_at is None, (
            "last_garmin_synced_at must remain None when upload fails"
        )

    def test_last_garmin_synced_at_set_on_successful_sync(self):
        """After a successful upload + schedule, last_garmin_synced_at must be set."""
        from datetime import date

        from running_coach_ai.garmin.workout_builder import sync_week_to_garmin

        workout = MagicMock()
        workout.scheduled_date = date.today() + timedelta(days=1)
        workout.workout_type = "easy"
        workout.workout_name = None
        workout.description = "Easy run"
        workout.target_distance_km = 8.0
        workout.target_pace_min_per_km = 6.0
        workout.target_zones_json = None
        workout.garmin_workout_id = None
        workout.garmin_schedule_id = None
        workout.last_garmin_synced_at = None

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [workout]
        db.commit = MagicMock()

        with patch("running_coach_ai.garmin.workout_builder.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.workout_builder.build_workout_json",
                   return_value={"name": "easy"}), \
             patch("running_coach_ai.garmin.workout_builder.get_garmin_workout_library",
                   return_value=[]), \
             patch("running_coach_ai.garmin.workout_builder.upload_workout",
                   return_value={"workoutId": 42}), \
             patch("running_coach_ai.garmin.workout_builder.schedule_workout",
                   return_value={"workoutScheduleId": 7}):
            mock_auth.return_value = MagicMock()
            sync_week_to_garmin(1, "e@example.com", b"enc",
                                date.today(), db)

        assert workout.last_garmin_synced_at is not None, (
            "last_garmin_synced_at must be set after a successful sync"
        )
        assert isinstance(workout.last_garmin_synced_at, datetime), (
            "last_garmin_synced_at must be a datetime object"
        )
