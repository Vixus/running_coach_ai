"""Unit tests for coach_key handling in _complete_onboarding (T007)."""

from datetime import date
from unittest.mock import MagicMock, patch


from running_coach_ai.database.models import Athlete
from running_coach_ai.slack.onboarding import _complete_onboarding


def _make_athlete():
    athlete = MagicMock(spec=Athlete)
    athlete.id = 1
    athlete.slack_user_id = "U123"
    athlete.name = None
    athlete.age = None
    athlete.home_lat = None
    athlete.home_lon = None
    athlete.timezone = None
    athlete.garmin_email = None
    athlete.garmin_password_encrypted = None
    athlete.onboarding_complete = False
    athlete.onboarding_step = 0
    athlete.coach_key = None
    return athlete


def _base_data(**overrides) -> dict:
    data = {
        "name": "Test Runner",
        "age": 30,
        "race_type": "marathon",
        "race_name": None,
        "race_date": (date.today() + __import__("datetime").timedelta(weeks=16)).isoformat(),
        "target_time_seconds": 14400,
        "weekly_mileage_km": 50.0,
        "training_days": 5,
        "experience_level": "intermediate",
        "injuries": "none",
        "city": "Boston",
    }
    data.update(overrides)
    return data


@patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", autospec=True)
@patch("running_coach_ai.coach.planner.generate_plan", autospec=True)
def test_complete_onboarding_sets_maya_coach_key(mock_gen_plan, mock_sync):
    athlete = _make_athlete()
    db = MagicMock()
    say_fn = MagicMock()

    mock_plan = MagicMock()
    mock_plan.valid_from = date.today()
    mock_plan.valid_to = date.today()
    mock_gen_plan.return_value = mock_plan

    data = _base_data(coach_key="maya")

    _complete_onboarding(athlete, data, db, say_fn)

    assert athlete.coach_key == "maya"


@patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", autospec=True)
@patch("running_coach_ai.coach.planner.generate_plan", autospec=True)
def test_complete_onboarding_defaults_to_classic_when_coach_key_absent(mock_gen_plan, mock_sync):
    athlete = _make_athlete()
    db = MagicMock()
    say_fn = MagicMock()

    mock_plan = MagicMock()
    mock_plan.valid_from = date.today()
    mock_plan.valid_to = date.today()
    mock_gen_plan.return_value = mock_plan

    data = _base_data()  # no coach_key field

    _complete_onboarding(athlete, data, db, say_fn)

    assert athlete.coach_key == "classic"


@patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", autospec=True)
@patch("running_coach_ai.coach.planner.generate_plan", autospec=True)
def test_complete_onboarding_falls_back_to_classic_for_invalid_key(mock_gen_plan, mock_sync):
    athlete = _make_athlete()
    db = MagicMock()
    say_fn = MagicMock()

    mock_plan = MagicMock()
    mock_plan.valid_from = date.today()
    mock_plan.valid_to = date.today()
    mock_gen_plan.return_value = mock_plan

    data = _base_data(coach_key="nonexistent_coach")

    _complete_onboarding(athlete, data, db, say_fn)

    assert athlete.coach_key == "classic"


@patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", autospec=True)
@patch("running_coach_ai.coach.planner.generate_plan", autospec=True)
def test_complete_onboarding_sets_jordan_coach_key(mock_gen_plan, mock_sync):
    athlete = _make_athlete()
    db = MagicMock()
    say_fn = MagicMock()

    mock_plan = MagicMock()
    mock_plan.valid_from = date.today()
    mock_plan.valid_to = date.today()
    mock_gen_plan.return_value = mock_plan

    data = _base_data(coach_key="jordan")

    _complete_onboarding(athlete, data, db, say_fn)

    assert athlete.coach_key == "jordan"


def test_build_onboarding_system_prompt_contains_all_coaches():
    """Case 5: _build_onboarding_system_prompt() must mention all 3 coach names and the default key."""
    from running_coach_ai.slack.onboarding import _build_onboarding_system_prompt  # noqa: PLC0415

    prompt = _build_onboarding_system_prompt()
    assert "Coach Alex" in prompt
    assert "Coach Maya" in prompt
    assert "Coach Jordan" in prompt
    assert "classic" in prompt

