"""Tests for Change 3: onboarding syncs all plan weeks, not just week 1.

_complete_onboarding must call sync_week_to_garmin once per week from
plan.valid_from to plan.valid_to (inclusive), so the full calendar is
populated the moment the athlete completes onboarding.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


TODAY = date.today()
_OB = "running_coach_ai.slack.onboarding"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _athlete(athlete_id: int = 1):
    a = MagicMock()
    a.id = athlete_id
    a.name = "Alice"
    a.age = 30
    a.garmin_email = "alice@example.com"
    a.garmin_password_encrypted = b"enc"
    a.home_lat = 51.5
    a.home_lon = -0.1
    a.timezone = "Europe/London"
    a.onboarding_complete = False
    return a


def _make_plan(weeks: int = 16):
    p = MagicMock()
    p.id = 1
    p.valid_from = TODAY - timedelta(days=TODAY.weekday())  # this Monday
    # valid_to is the Sunday of the last week (not the Monday after), so the
    # loop `while week <= valid_to` iterates exactly `weeks` times.
    p.valid_to = p.valid_from + timedelta(weeks=weeks, days=-1)
    return p


def _make_goal():
    g = MagicMock()
    g.id = 1
    g.race_type = "marathon"
    g.race_date = TODAY + timedelta(weeks=16)
    g.target_time_seconds = 14400
    g.training_days_per_week = 5
    return g


def _run_complete_onboarding(plan, weeks_in_plan=16):
    """Run _complete_onboarding with a mocked plan and Garmin sync."""
    from running_coach_ai.slack.onboarding import _complete_onboarding

    athlete = _athlete()
    db = MagicMock()
    db.flush.return_value = None
    db.commit.return_value = None

    goal = _make_goal()
    goal_q = MagicMock()
    goal_q.filter.return_value.first.return_value = goal

    data = {
        "name": "Alice",
        "age": 30,
        "race_type": "marathon",
        "race_date": (TODAY + timedelta(weeks=weeks_in_plan)).isoformat(),
        "target_time_seconds": 14400,
        "weekly_mileage_km": 40.0,
        "training_days": 5,
        "city": "london",
        "garmin_email": "alice@example.com",
        "garmin_password": "pass",
        "injuries": "none",
    }

    say_calls = []
    say_fn = lambda msg: say_calls.append(msg)

    with patch("running_coach_ai.coach.planner.generate_plan", return_value=plan), \
         patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", return_value=(5, 0, [])) as mock_sync, \
         patch(f"{_OB}._import_historical_activities", return_value=0), \
         patch(f"{_OB}._send_week1_summary"), \
         patch(f"{_OB}.encrypt_password", return_value=b"enc"), \
         patch(f"{_OB}._parse_city_to_coords", return_value=(51.5, -0.1, "Europe/London")):
        try:
            with patch("running_coach_ai.slack.onboarding.register_athlete_morning_job"):
                _complete_onboarding(athlete, data, db, say_fn)
        except Exception:
            # main module may not be importable in test env — that's fine
            _complete_onboarding(athlete, data, db, say_fn)

    return mock_sync, athlete


# ---------------------------------------------------------------------------
# All weeks synced
# ---------------------------------------------------------------------------

class TestOnboardingFullPlanSync:

    def test_sync_called_for_every_week(self):
        """sync_week_to_garmin must be called once for every week from valid_from to valid_to."""
        plan = _make_plan(weeks=4)
        mock_sync, _ = _run_complete_onboarding(plan, weeks_in_plan=4)

        # 4-week plan → 4 weekly sync calls (weeks 0, 1, 2, 3)
        assert mock_sync.call_count == 4

    def test_sync_starts_at_valid_from(self):
        """First sync call must use plan.valid_from as week_start."""
        plan = _make_plan(weeks=4)
        mock_sync, _ = _run_complete_onboarding(plan, weeks_in_plan=4)

        first_call_week = mock_sync.call_args_list[0][0][3]
        assert first_call_week == plan.valid_from

    def test_sync_covers_all_weeks_sequentially(self):
        """Each call must advance by 7 days from the previous."""
        plan = _make_plan(weeks=4)
        mock_sync, _ = _run_complete_onboarding(plan, weeks_in_plan=4)

        week_starts = [c[0][3] for c in mock_sync.call_args_list]
        for i in range(1, len(week_starts)):
            assert week_starts[i] == week_starts[i - 1] + timedelta(weeks=1), (
                f"Week {i} start {week_starts[i]} is not 7 days after {week_starts[i-1]}"
            )

    def test_longer_plan_more_sync_calls(self):
        """A 16-week plan requires 16 sync calls, not just 1."""
        plan = _make_plan(weeks=16)
        mock_sync, _ = _run_complete_onboarding(plan, weeks_in_plan=16)
        assert mock_sync.call_count == 16

    def test_athlete_id_passed_to_every_sync_call(self):
        """All sync calls must use the correct athlete ID."""
        plan = _make_plan(weeks=3)
        mock_sync, athlete = _run_complete_onboarding(plan, weeks_in_plan=3)

        for c in mock_sync.call_args_list:
            assert c[0][0] == athlete.id

    def test_sync_failure_on_one_week_does_not_abort_others(self):
        """A Garmin error on one week must not stop the sync for subsequent weeks."""
        from running_coach_ai.slack.onboarding import _complete_onboarding

        plan = _make_plan(weeks=4)
        athlete = _athlete()
        db = MagicMock()
        db.flush.return_value = None
        db.commit.return_value = None

        data = {
            "name": "Alice", "age": 30, "race_type": "marathon",
            "race_date": (TODAY + timedelta(weeks=4)).isoformat(),
            "target_time_seconds": 14400, "weekly_mileage_km": 40.0,
            "training_days": 5, "city": "london",
            "garmin_email": "alice@example.com", "garmin_password": "pass",
            "injuries": "none",
        }

        call_count = [0]

        def _flaky_sync(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("Garmin 500")
            return (5, 0, [])

        with patch("running_coach_ai.coach.planner.generate_plan", return_value=plan), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", side_effect=_flaky_sync) as mock_sync, \
             patch(f"{_OB}._import_historical_activities", return_value=0), \
             patch(f"{_OB}._send_week1_summary"), \
             patch(f"{_OB}.encrypt_password", return_value=b"enc"), \
             patch(f"{_OB}._parse_city_to_coords", return_value=(51.5, -0.1, "Europe/London")):
            say_calls = []
            _complete_onboarding(athlete, data, db, lambda m: say_calls.append(m))

        # All 4 weeks attempted
        assert mock_sync.call_count == 4

    def test_no_garmin_credentials_skips_sync(self):
        """If athlete has no Garmin credentials, sync_week_to_garmin must NOT be called."""
        from running_coach_ai.slack.onboarding import _complete_onboarding

        plan = _make_plan(weeks=4)
        athlete = _athlete()
        # Clear credentials so the sync guard fires
        athlete.garmin_email = None
        athlete.garmin_password_encrypted = None

        data = {
            "name": "Alice", "age": 30, "race_type": "marathon",
            "race_date": (TODAY + timedelta(weeks=4)).isoformat(),
            "target_time_seconds": 14400, "weekly_mileage_km": 40.0,
            "training_days": 5, "city": "london",
            # No garmin_email or garmin_password
            "injuries": "none",
        }

        db = MagicMock()
        db.flush.return_value = None
        db.commit.return_value = None

        with patch("running_coach_ai.coach.planner.generate_plan", return_value=plan), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin") as mock_sync, \
             patch(f"{_OB}._import_historical_activities", return_value=0), \
             patch(f"{_OB}._send_week1_summary"), \
             patch(f"{_OB}.encrypt_password", return_value=b"enc"), \
             patch(f"{_OB}._parse_city_to_coords", return_value=(51.5, -0.1, "Europe/London")):
            _complete_onboarding(athlete, data, db, lambda m: None)

        mock_sync.assert_not_called()
