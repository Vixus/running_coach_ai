"""Unit tests for the coach persona registry (T006)."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from running_coach_ai.coach.personas import (
    DEFAULT_COACH_KEY,
    PERSONAS,
    CoachPersona,
    get_persona,
    is_valid_coach_key,
)


def test_personas_has_exactly_three_keys():
    assert set(PERSONAS.keys()) == {"classic", "maya", "jordan"}


def test_get_persona_classic_returns_coach_alex():
    p = get_persona("classic")
    assert p.name == "Coach Alex"


def test_get_persona_none_returns_default():
    default = get_persona(None)
    assert default == PERSONAS[DEFAULT_COACH_KEY]
    assert default.key == "classic"


def test_get_persona_unknown_key_returns_default():
    p = get_persona("unknown_key")
    assert p == PERSONAS[DEFAULT_COACH_KEY]


def test_is_valid_coach_key_maya():
    assert is_valid_coach_key("maya") is True


def test_is_valid_coach_key_bogus():
    assert is_valid_coach_key("bogus") is False


@pytest.mark.parametrize("key", ["classic", "maya", "jordan"])
def test_all_persona_blocks_contain_coach_switch_tag(key):
    p = PERSONAS[key]
    assert "<coach_switch>" in p.persona_block, (
        f"Persona '{key}' is missing <coach_switch> tag instruction in persona_block"
    )


@pytest.mark.parametrize("key", ["classic", "maya", "jordan"])
def test_all_personas_are_frozen_dataclasses(key):
    p = PERSONAS[key]
    assert isinstance(p, CoachPersona)
    with pytest.raises((AttributeError, TypeError)):
        p.key = "tampered"  # frozen dataclass __setattr__ should raise


def test_default_coach_key_is_classic():
    assert DEFAULT_COACH_KEY == "classic"


@pytest.mark.parametrize("key", ["classic", "maya", "jordan"])
def test_is_valid_coach_key_all_valid(key):
    assert is_valid_coach_key(key) is True


@pytest.mark.parametrize(
    ("legacy", "canonical"),
    [("sofia", "maya"), ("miles", "jordan")],
)
def test_legacy_keys_resolve_to_canonical_personas(legacy, canonical):
    assert is_valid_coach_key(legacy) is True
    assert get_persona(legacy) == get_persona(canonical)


# ---------------------------------------------------------------------------
# Cases 9-11: call-site injection — correct persona_block passed to call_claude
# ---------------------------------------------------------------------------

_ADAPTER = "running_coach_ai.coach.adapter"
_FEEDBACK = "running_coach_ai.coach.feedback"


@patch(f"{_ADAPTER}.extract_and_apply_plan", return_value="morning text")
@patch(f"{_ADAPTER}.call_claude", return_value="morning text")
@patch(f"{_ADAPTER}.scoped_query")
@patch("running_coach_ai.coach.notify.notify")
@patch("running_coach_ai.garmin.parser.parse_health_snapshot")
@patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
@patch("running_coach_ai.garmin.client.get_garmin_client")
@patch("running_coach_ai.weather.client.summarise_forecast", return_value=[])
@patch("running_coach_ai.weather.client.get_forecast", return_value={})
def test_run_morning_checkin_uses_selected_persona(
    mock_forecast, mock_summarise, mock_garmin, mock_health_raw, mock_parse,
    mock_send_dm, mock_scoped, mock_claude, mock_extract,
):
    """Case 9: run_morning_checkin passes athlete's selected persona_block to call_claude."""
    from running_coach_ai.coach.adapter import run_morning_checkin

    snapshot = MagicMock()
    snapshot.date = date.today()
    snapshot.sleep_score = 75
    snapshot.hrv_score = 42
    snapshot.body_battery_start = 80
    snapshot.resting_hr = 51
    snapshot.stress_avg = 19
    snapshot.hrv_status = "BALANCED"
    snapshot.training_readiness = 75  # passes the morning-data gate
    mock_parse.return_value = snapshot
    mock_scoped.return_value.filter.return_value.first.return_value = None

    athlete = MagicMock()
    athlete.id = 1
    athlete.name = "Alex"
    athlete.timezone = "America/New_York"
    athlete.garmin_email = "alex@example.com"
    athlete.garmin_password_encrypted = b"enc"
    athlete.home_lat = 40.7
    athlete.home_lon = -74.0
    athlete.last_morning_checkin_date = None
    athlete.coach_key = "maya"

    # Pin time to a daytime hour so the 06:00 local floor doesn't return early
    from datetime import datetime as _dt, timezone as _tz
    fake_local = _dt(date.today().year, date.today().month, date.today().day, 8, 0, tzinfo=_tz.utc)
    with patch(f"{_ADAPTER}.datetime") as mock_dt:
        mock_dt.now.return_value = fake_local
        mock_dt.side_effect = lambda *args, **kwargs: _dt(*args, **kwargs)
        run_morning_checkin(athlete, MagicMock())

    mock_claude.assert_called_once()
    assert mock_claude.call_args[0][0] == get_persona("maya").persona_block


@patch(f"{_ADAPTER}.extract_and_apply_plan")
@patch("running_coach_ai.coach.persona.call_claude", return_value="adaptation text")
@patch(f"{_ADAPTER}.scoped_query")
def test_adapt_next_week_uses_selected_persona(mock_scoped, mock_claude, mock_extract):
    """Case 10: adapt_next_week passes athlete's selected persona_block to call_claude."""
    from datetime import timedelta

    from running_coach_ai.coach.adapter import adapt_next_week

    mock_workout = MagicMock()
    mock_workout.scheduled_date = date.today() + timedelta(weeks=1)
    mock_workout.workout_type = "easy"
    mock_workout.target_distance_km = 8.0
    mock_scoped.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_workout]

    athlete = MagicMock()
    athlete.id = 1
    athlete.name = "Alex"
    athlete.coach_key = "jordan"

    week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    week_summary = {
        "week_start": week_start,
        "actual_km": 40.0,
        "planned_km": 45.0,
        "completed_sessions": 4,
        "planned_sessions": 5,
        "quality_sessions_completed": 1,
        "quality_sessions_planned": 2,
        "completion_pct": 80.0,
    }

    adapt_next_week(athlete, week_summary, MagicMock())

    mock_claude.assert_called_once()
    assert mock_claude.call_args[0][0] == get_persona("jordan").persona_block


@patch(f"{_FEEDBACK}.call_claude", return_value="feedback text")
@patch("running_coach_ai.coach.conversation.extract_and_save_memories", return_value="feedback text")
@patch("running_coach_ai.coach.notify.notify")
def test_generate_post_run_feedback_uses_selected_persona(mock_send_dm, mock_memories, mock_claude):
    """Case 11: generate_post_run_feedback passes athlete's persona_block to call_claude."""
    from running_coach_ai.coach.feedback import generate_post_run_feedback

    athlete = MagicMock()
    athlete.id = 1
    athlete.name = "Alex"
    athlete.coach_key = "maya"

    completed = MagicMock()
    completed.planned_workout_id = None
    completed.duration_seconds = 2700
    completed.distance_km = 10.0
    completed.avg_pace_min_per_km = 5.5
    completed.avg_hr = 145
    completed.max_hr = 175
    completed.calories = 500

    biomechanics: dict = {
        "zone_confidence": "low",
        "zone_max_hr_run_count": 1,
        "zone_source": "computed_max_hr",
    }

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    generate_post_run_feedback(athlete, completed, biomechanics, db, MagicMock())

    mock_claude.assert_called_once()
    assert mock_claude.call_args[0][0] == get_persona("maya").persona_block
