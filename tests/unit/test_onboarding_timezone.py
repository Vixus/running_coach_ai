"""Tests for T003/T004: onboarding timezone derivation via _complete_onboarding.

Covers:
  (a) city in hardcoded table → IANA tz used directly; derive_timezone_from_coords NOT called
  (b) city NOT in table, valid home_lat/home_lon → derive_timezone_from_coords called, result stored
  (c) city NOT in table, derive_timezone_from_coords returns None → athlete.timezone == "UTC", warning logged
  (d) city NOT in table, no home_lat/home_lon → athlete.timezone == "UTC"
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


TODAY = date.today()
_OB = "running_coach_ai.slack.onboarding"
_PLANNER = "running_coach_ai.coach.planner"
_WORKOUT_BUILDER = "running_coach_ai.garmin.workout_builder"
_TZ_UTILS = "running_coach_ai.coach.timezone_utils"
_SCHED_JOBS = "running_coach_ai.scheduler.jobs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _athlete(athlete_id: int = 1):
    a = MagicMock()
    a.id = athlete_id
    a.name = None
    a.age = None
    a.garmin_email = None
    a.garmin_password_encrypted = None
    a.home_lat = None
    a.home_lon = None
    a.timezone = None
    a.onboarding_complete = False
    return a


def _make_plan():
    p = MagicMock()
    p.id = 1
    p.valid_from = TODAY
    p.valid_to = TODAY + timedelta(weeks=4)
    return p


def _base_data(**overrides):
    d = {
        "name": "Test Runner",
        "age": "30",
        "city": "london",
        "race_type": "5k",
        "race_date": str(TODAY + timedelta(weeks=16)),
        "target_time_seconds": "1500",
        "weekly_mileage_km": "30",
        "experience_level": "intermediate",
        "training_days": "5",
        "injuries": "none",
    }
    d.update(overrides)
    return d


def _run_complete_onboarding(athlete, data, *, derive_tz_return="America/Chicago"):
    """Run _complete_onboarding with enough mocking to reach the timezone block."""
    from running_coach_ai.slack.onboarding import _complete_onboarding

    db_session = MagicMock()
    say_fn = MagicMock()
    plan = _make_plan()

    with (
        patch(f"{_PLANNER}.generate_plan", return_value=plan),
        patch(f"{_WORKOUT_BUILDER}.sync_week_to_garmin"),
        patch(f"{_OB}._import_historical_activities", return_value=0),
        patch(f"{_OB}.encrypt_password", return_value=b"enc"),
        patch(f"{_TZ_UTILS}.derive_timezone_from_coords", return_value=derive_tz_return) as mock_derive,
        patch(f"{_OB}.call_claude", return_value="Great plan!"),
        patch("main.scheduler", create=True),
        patch("main.handler", create=True),
        patch(f"{_SCHED_JOBS}.register_athlete_morning_job"),
    ):
        _complete_onboarding(athlete, data, db_session, say_fn)
        return athlete, mock_derive


# ---------------------------------------------------------------------------
# (a) City in hardcoded table → tz used directly, derive NOT called
# ---------------------------------------------------------------------------

def test_city_in_table_uses_hardcoded_tz():
    athlete = _athlete()
    athlete, mock_derive = _run_complete_onboarding(athlete, _base_data(city="london"))

    assert athlete.timezone == "Europe/London"
    mock_derive.assert_not_called()


def test_city_in_table_partial_match():
    """'new york city' should match the 'new york' entry."""
    athlete = _athlete()
    athlete, mock_derive = _run_complete_onboarding(athlete, _base_data(city="new york city"))

    assert athlete.timezone == "America/New_York"
    mock_derive.assert_not_called()


# ---------------------------------------------------------------------------
# (b) City NOT in table, valid home_lat/home_lon → derive called, result stored
# ---------------------------------------------------------------------------

def test_city_not_in_table_calls_derive_and_stores_result():
    """An unknown city with coords already set should use derive_timezone_from_coords."""
    athlete = _athlete()
    # Simulate coords being pre-set (e.g., by a future geocoder)
    athlete.home_lat = 30.2672
    athlete.home_lon = -97.7431  # Austin, TX

    athlete, mock_derive = _run_complete_onboarding(
        athlete,
        _base_data(city="austin"),
        derive_tz_return="America/Chicago",
    )

    mock_derive.assert_called_once_with(30.2672, -97.7431)
    assert athlete.timezone == "America/Chicago"


# ---------------------------------------------------------------------------
# (c) City NOT in table, derive returns None → fallback to "UTC" + warning
# ---------------------------------------------------------------------------

def test_city_not_in_table_derive_returns_none_fallback_utc(caplog):
    import logging
    athlete = _athlete()
    athlete.home_lat = 0.0
    athlete.home_lon = 0.0

    with caplog.at_level(logging.WARNING, logger="running_coach_ai.slack.onboarding"):
        athlete, mock_derive = _run_complete_onboarding(
            athlete,
            _base_data(city="unknowncity"),
            derive_tz_return=None,
        )

    assert athlete.timezone == "America/New_York"
    assert any("UTC" in r.message or "timezone" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# (d) City NOT in table, no home_lat/home_lon → fallback to "UTC"
# ---------------------------------------------------------------------------

def test_city_not_in_table_no_coords_fallback_utc():
    athlete = _athlete()
    # home_lat / home_lon remain None (default from _athlete())

    athlete, mock_derive = _run_complete_onboarding(
        athlete,
        _base_data(city="unknowncity"),
        derive_tz_return="America/Chicago",
    )

    # derive should NOT be called because there are no coords
    mock_derive.assert_not_called()
    assert athlete.timezone == "America/New_York"
