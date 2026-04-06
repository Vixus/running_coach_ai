"""T060: Tests for multi-goal conflict resolution in generate_plan()."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


TODAY = date.today()


def _make_goal(goal_id, race_date, active=True):
    g = MagicMock()
    g.id = goal_id
    g.race_date = race_date
    g.race_type = "marathon"
    g.target_time_seconds = 14400
    g.current_weekly_mileage_km = 48.0
    g.experience_level = "intermediate"
    g.training_days_per_week = 5
    g.active = active
    return g


def _make_athlete(athlete_id=1):
    a = MagicMock()
    a.id = athlete_id
    a.name = "Test Runner"
    a.age = 35
    return a


class TestMultiGoalConflictResolution:
    """generate_plan must deactivate older goals when race dates are <4 weeks apart."""

    def _make_db(self, conflicting_goals):
        """Return a Session mock that yields conflicting_goals for the overlap query."""
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value.all.return_value = conflicting_goals
        q.filter.return_value.first.return_value = None  # for other queries
        db.query.return_value = q
        return db

    @patch("running_coach_ai.coach.planner.call_claude")
    @patch("running_coach_ai.coach.planner._expand_skeleton")
    def test_conflict_within_4_weeks_deactivates_older_goal(
        self, mock_expand, mock_claude
    ):
        """Two active goals with race dates 3 weeks apart must deactivate the other."""
        mock_claude.return_value = '[{"week": 1, "phase": "base", "km": 50, "sessions": []}]'
        mock_expand.return_value = [
            {
                "start_date": TODAY.isoformat(),
                "end_date": (TODAY + timedelta(days=6)).isoformat(),
                "phase": "base",
                "days": [],
            }
        ]

        new_goal = _make_goal(1, TODAY + timedelta(weeks=16))
        conflicting_goal = _make_goal(2, TODAY + timedelta(weeks=13))  # 3 weeks apart

        db = MagicMock()
        # Query for other active goals returns the conflicting one
        other_goals_q = MagicMock()
        other_goals_q.filter.return_value.all.return_value = [conflicting_goal]

        # Query for TrainingPlan (flush side effects) returns nothing
        plan_q = MagicMock()
        plan_q.filter.return_value.all.return_value = []

        def query_side_effect(model):
            from running_coach_ai.database.models import Goal
            if model is Goal:
                return other_goals_q
            return plan_q

        db.query.side_effect = query_side_effect
        db.flush = MagicMock()
        db.add = MagicMock()
        db.commit = MagicMock()

        athlete = _make_athlete()

        from running_coach_ai.coach.planner import generate_plan

        with patch("running_coach_ai.coach.planner.TrainingPlan") as mock_tp_class:
            mock_tp_instance = MagicMock()
            mock_tp_instance.id = 10
            mock_tp_instance.valid_from = TODAY
            mock_tp_instance.valid_to = new_goal.race_date
            mock_tp_class.return_value = mock_tp_instance

            generate_plan(athlete, new_goal, db)

        assert conflicting_goal.active is False, (
            "Conflicting goal (3 weeks apart) must be deactivated"
        )

    @patch("running_coach_ai.coach.planner.call_claude")
    @patch("running_coach_ai.coach.planner._expand_skeleton")
    def test_no_conflict_outside_4_weeks(self, mock_expand, mock_claude):
        """Goals with race dates 8 weeks apart must NOT trigger conflict deactivation."""
        mock_claude.return_value = '[{"week": 1, "phase": "base", "km": 50, "sessions": []}]'
        mock_expand.return_value = [
            {
                "start_date": TODAY.isoformat(),
                "end_date": (TODAY + timedelta(days=6)).isoformat(),
                "phase": "base",
                "days": [],
            }
        ]

        new_goal = _make_goal(1, TODAY + timedelta(weeks=20))
        other_goal = _make_goal(2, TODAY + timedelta(weeks=12))  # 8 weeks apart
        other_goal.active = True  # start as active

        db = MagicMock()
        other_goals_q = MagicMock()
        other_goals_q.filter.return_value.all.return_value = [other_goal]

        plan_q = MagicMock()
        plan_q.filter.return_value.all.return_value = []

        def query_side_effect(model):
            from running_coach_ai.database.models import Goal
            if model is Goal:
                return other_goals_q
            return plan_q

        db.query.side_effect = query_side_effect
        db.flush = MagicMock()
        db.add = MagicMock()
        db.commit = MagicMock()

        athlete = _make_athlete()

        from running_coach_ai.coach.planner import generate_plan

        with patch("running_coach_ai.coach.planner.TrainingPlan") as mock_tp_class:
            mock_tp_instance = MagicMock()
            mock_tp_instance.id = 10
            mock_tp_instance.valid_from = TODAY
            mock_tp_instance.valid_to = new_goal.race_date
            mock_tp_class.return_value = mock_tp_instance

            generate_plan(athlete, new_goal, db)

        assert other_goal.active is True, (
            "Goal 8 weeks away must remain active — no conflict at this distance"
        )

    @patch("running_coach_ai.coach.planner.call_claude")
    @patch("running_coach_ai.coach.planner._expand_skeleton")
    def test_no_other_active_goals_no_deactivation(self, mock_expand, mock_claude):
        """When there are no other active goals, plan generation must proceed normally."""
        mock_claude.return_value = '[{"week": 1, "phase": "base", "km": 50, "sessions": []}]'
        mock_expand.return_value = [
            {
                "start_date": TODAY.isoformat(),
                "end_date": (TODAY + timedelta(days=6)).isoformat(),
                "phase": "base",
                "days": [],
            }
        ]

        new_goal = _make_goal(1, TODAY + timedelta(weeks=16))

        db = MagicMock()
        other_goals_q = MagicMock()
        other_goals_q.filter.return_value.all.return_value = []  # no other goals

        plan_q = MagicMock()
        plan_q.filter.return_value.all.return_value = []

        def query_side_effect(model):
            from running_coach_ai.database.models import Goal
            if model is Goal:
                return other_goals_q
            return plan_q

        db.query.side_effect = query_side_effect
        db.flush = MagicMock()
        db.add = MagicMock()
        db.commit = MagicMock()

        athlete = _make_athlete()

        from running_coach_ai.coach.planner import generate_plan

        with patch("running_coach_ai.coach.planner.TrainingPlan") as mock_tp_class:
            mock_tp_instance = MagicMock()
            mock_tp_instance.id = 10
            mock_tp_instance.valid_from = TODAY
            mock_tp_instance.valid_to = new_goal.race_date
            mock_tp_class.return_value = mock_tp_instance

            # Should not raise
            generate_plan(athlete, new_goal, db)

        db.commit.assert_called()
