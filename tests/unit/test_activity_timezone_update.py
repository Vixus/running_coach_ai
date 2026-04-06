"""Tests for T006/T007/T008: GPS extraction and timezone update during activity ingestion.

Covers:
  (a) extract_activity_start_coords: summaryDTO path present → correct (lat, lon)
  (b) extract_activity_start_coords: summaryDTO absent, lapDTOs[0] present → first-lap coords
  (c) extract_activity_start_coords: both paths null/zero → None
  (d) _ingest_and_feedback end-to-end: travel activity updates athlete.timezone,
      calls register_athlete_morning_job, logs INFO transition
  (e) _ingest_and_feedback: treadmill run (no GPS) → timezone unchanged,
      register_athlete_morning_job NOT called
"""

from unittest.mock import MagicMock, patch

_JOBS = "running_coach_ai.scheduler.jobs"
_PARSER = "running_coach_ai.garmin.parser"
_TELEMETRY = "running_coach_ai.garmin.telemetry"
_BIOMECH = "running_coach_ai.coach.biomechanics"
_FEEDBACK = "running_coach_ai.coach.feedback"
_TZ_UTILS = "running_coach_ai.coach.timezone_utils"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detail(summary_lat=None, summary_lon=None, lap_lat=None, lap_lon=None):
    """Build a minimal get_activity_details response."""
    summary_dto = {}
    if summary_lat is not None:
        summary_dto["startLatitude"] = summary_lat
    if summary_lon is not None:
        summary_dto["startLongitude"] = summary_lon

    laps = []
    if lap_lat is not None or lap_lon is not None:
        laps = [{"startLatitude": lap_lat, "startLongitude": lap_lon}]

    return {
        "activityDetail": {
            "activity": {
                "activityId": "123",
                "summaryDTO": summary_dto,
                "lapDTOs": laps,
            }
        }
    }


def _athlete_mock(timezone="America/New_York"):
    a = MagicMock()
    a.id = 1
    a.timezone = timezone
    a.garmin_email = "test@example.com"
    a.garmin_password_encrypted = b"enc"
    return a


# ---------------------------------------------------------------------------
# (a) summaryDTO path present
# ---------------------------------------------------------------------------

def test_extract_coords_from_summary_dto():
    from running_coach_ai.garmin.parser import extract_activity_start_coords

    detail = _detail(summary_lat=37.5665, summary_lon=126.9780)
    result = extract_activity_start_coords(detail)
    assert result == (37.5665, 126.9780)


def test_extract_coords_summary_dto_negative_coords():
    """Negative valid coords (e.g., southern hemisphere) should be returned."""
    from running_coach_ai.garmin.parser import extract_activity_start_coords

    detail = _detail(summary_lat=-33.8688, summary_lon=151.2093)
    result = extract_activity_start_coords(detail)
    assert result == (-33.8688, 151.2093)


# ---------------------------------------------------------------------------
# (b) summaryDTO absent, lapDTOs fallback
# ---------------------------------------------------------------------------

def test_extract_coords_falls_back_to_first_lap():
    from running_coach_ai.garmin.parser import extract_activity_start_coords

    detail = _detail(lap_lat=48.8566, lap_lon=2.3522)
    result = extract_activity_start_coords(detail)
    assert result == (48.8566, 2.3522)


def test_extract_coords_summary_zero_falls_back_to_lap():
    """Zero summaryDTO coords should fall back to lap data."""
    from running_coach_ai.garmin.parser import extract_activity_start_coords

    detail = _detail(summary_lat=0.0, summary_lon=0.0, lap_lat=51.5074, lap_lon=-0.1278)
    result = extract_activity_start_coords(detail)
    assert result == (51.5074, -0.1278)


# ---------------------------------------------------------------------------
# (c) Both paths null or zero → None (treadmill)
# ---------------------------------------------------------------------------

def test_extract_coords_no_gps_returns_none():
    from running_coach_ai.garmin.parser import extract_activity_start_coords

    detail = _detail()  # no coords at all
    result = extract_activity_start_coords(detail)
    assert result is None


def test_extract_coords_all_zero_returns_none():
    from running_coach_ai.garmin.parser import extract_activity_start_coords

    detail = _detail(summary_lat=0.0, summary_lon=0.0, lap_lat=0.0, lap_lon=0.0)
    result = extract_activity_start_coords(detail)
    assert result is None


def test_extract_coords_missing_activitydetail_returns_none():
    from running_coach_ai.garmin.parser import extract_activity_start_coords

    result = extract_activity_start_coords({})
    assert result is None


# ---------------------------------------------------------------------------
# (d) End-to-end: travel activity updates timezone + calls register_morning_job
# ---------------------------------------------------------------------------

def test_ingest_and_feedback_travel_updates_timezone_and_reregisters_job():
    from running_coach_ai.scheduler.jobs import _ingest_and_feedback

    athlete = _athlete_mock(timezone="America/New_York")
    db_session = MagicMock()
    slack_client = MagicMock()
    scheduler = MagicMock()

    # Seoul coordinates → "Asia/Seoul"
    detail = _detail(summary_lat=37.5665, summary_lon=126.9780)

    garmin = MagicMock()
    garmin.get_activity_details.return_value = detail

    completed = MagicMock()
    completed.id = 10
    telemetry = MagicMock()

    with (
        patch(f"{_PARSER}.parse_activity_summary", return_value=completed),
        patch(f"{_TELEMETRY}.extract_telemetry", return_value=telemetry),
        patch(f"{_TELEMETRY}.ingest_lap_splits"),
        patch(f"{_BIOMECH}.analyse_workout", return_value=MagicMock()),
        patch(f"{_BIOMECH}.update_running_profile"),
        patch(f"{_FEEDBACK}.generate_post_run_feedback"),
        patch(f"{_PARSER}.extract_activity_start_coords", return_value=(37.5665, 126.9780)),
        patch(f"{_TZ_UTILS}.derive_timezone_from_coords", return_value="Asia/Seoul"),
        patch(f"{_JOBS}.register_athlete_morning_job") as mock_register,
    ):
        _ingest_and_feedback(athlete, "123", garmin, db_session, slack_client, scheduler)

    assert athlete.timezone == "Asia/Seoul"
    mock_register.assert_called_once_with(scheduler, athlete, slack_client)
    db_session.commit.assert_called()


def test_ingest_and_feedback_logs_timezone_transition(caplog):
    import logging
    from running_coach_ai.scheduler.jobs import _ingest_and_feedback

    athlete = _athlete_mock(timezone="America/New_York")
    db_session = MagicMock()
    slack_client = MagicMock()
    scheduler = MagicMock()
    detail = _detail(summary_lat=37.5665, summary_lon=126.9780)
    garmin = MagicMock()
    garmin.get_activity_details.return_value = detail

    with (
        patch(f"{_PARSER}.parse_activity_summary", return_value=MagicMock(id=1)),
        patch(f"{_TELEMETRY}.extract_telemetry", return_value=MagicMock()),
        patch(f"{_TELEMETRY}.ingest_lap_splits"),
        patch(f"{_BIOMECH}.analyse_workout", return_value=MagicMock()),
        patch(f"{_BIOMECH}.update_running_profile"),
        patch(f"{_FEEDBACK}.generate_post_run_feedback"),
        patch(f"{_PARSER}.extract_activity_start_coords", return_value=(37.5665, 126.9780)),
        patch(f"{_TZ_UTILS}.derive_timezone_from_coords", return_value="Asia/Seoul"),
        patch(f"{_JOBS}.register_athlete_morning_job"),
        caplog.at_level(logging.INFO, logger=_JOBS),
    ):
        _ingest_and_feedback(athlete, "123", garmin, db_session, slack_client, scheduler)

    assert any("Asia/Seoul" in r.message for r in caplog.records)
    assert any("America/New_York" in r.message for r in caplog.records)


def test_ingest_and_feedback_same_timezone_no_update():
    """If derived tz equals current tz, no DB write and no job re-registration."""
    from running_coach_ai.scheduler.jobs import _ingest_and_feedback

    athlete = _athlete_mock(timezone="America/New_York")
    db_session = MagicMock()
    slack_client = MagicMock()
    scheduler = MagicMock()
    detail = _detail(summary_lat=40.7128, summary_lon=-74.0060)
    garmin = MagicMock()
    garmin.get_activity_details.return_value = detail

    with (
        patch(f"{_PARSER}.parse_activity_summary", return_value=MagicMock(id=1)),
        patch(f"{_TELEMETRY}.extract_telemetry", return_value=MagicMock()),
        patch(f"{_TELEMETRY}.ingest_lap_splits"),
        patch(f"{_BIOMECH}.analyse_workout", return_value=MagicMock()),
        patch(f"{_BIOMECH}.update_running_profile"),
        patch(f"{_FEEDBACK}.generate_post_run_feedback"),
        patch(f"{_PARSER}.extract_activity_start_coords", return_value=(40.7128, -74.0060)),
        patch(f"{_TZ_UTILS}.derive_timezone_from_coords", return_value="America/New_York"),
        patch(f"{_JOBS}.register_athlete_morning_job") as mock_register,
    ):
        _ingest_and_feedback(athlete, "123", garmin, db_session, slack_client, scheduler)

    mock_register.assert_not_called()


# ---------------------------------------------------------------------------
# (e) Treadmill / no GPS → timezone unchanged, register NOT called
# ---------------------------------------------------------------------------

def test_ingest_and_feedback_treadmill_no_timezone_change():
    from running_coach_ai.scheduler.jobs import _ingest_and_feedback

    athlete = _athlete_mock(timezone="America/New_York")
    db_session = MagicMock()
    slack_client = MagicMock()
    scheduler = MagicMock()
    detail = _detail()  # no GPS
    garmin = MagicMock()
    garmin.get_activity_details.return_value = detail

    with (
        patch(f"{_PARSER}.parse_activity_summary", return_value=MagicMock(id=1)),
        patch(f"{_TELEMETRY}.extract_telemetry", return_value=MagicMock()),
        patch(f"{_TELEMETRY}.ingest_lap_splits"),
        patch(f"{_BIOMECH}.analyse_workout", return_value=MagicMock()),
        patch(f"{_BIOMECH}.update_running_profile"),
        patch(f"{_FEEDBACK}.generate_post_run_feedback"),
        patch(f"{_PARSER}.extract_activity_start_coords", return_value=None),
        patch(f"{_TZ_UTILS}.derive_timezone_from_coords") as mock_derive,
        patch(f"{_JOBS}.register_athlete_morning_job") as mock_register,
    ):
        _ingest_and_feedback(athlete, "123", garmin, db_session, slack_client, scheduler)

    mock_derive.assert_not_called()
    mock_register.assert_not_called()
    assert athlete.timezone == "America/New_York"


def test_ingest_and_feedback_scheduler_failure_keeps_db_update():
    """Scheduler re-registration failure must NOT roll back the timezone DB update."""
    from running_coach_ai.scheduler.jobs import _ingest_and_feedback

    athlete = _athlete_mock(timezone="America/New_York")
    db_session = MagicMock()
    db_session.commit = MagicMock()
    slack_client = MagicMock()
    scheduler = MagicMock()
    detail = _detail(summary_lat=37.5665, summary_lon=126.9780)
    garmin = MagicMock()
    garmin.get_activity_details.return_value = detail

    with (
        patch(f"{_PARSER}.parse_activity_summary", return_value=MagicMock(id=1)),
        patch(f"{_TELEMETRY}.extract_telemetry", return_value=MagicMock()),
        patch(f"{_TELEMETRY}.ingest_lap_splits"),
        patch(f"{_BIOMECH}.analyse_workout", return_value=MagicMock()),
        patch(f"{_BIOMECH}.update_running_profile"),
        patch(f"{_FEEDBACK}.generate_post_run_feedback"),
        patch(f"{_PARSER}.extract_activity_start_coords", return_value=(37.5665, 126.9780)),
        patch(f"{_TZ_UTILS}.derive_timezone_from_coords", return_value="Asia/Seoul"),
        patch(f"{_JOBS}.register_athlete_morning_job", side_effect=RuntimeError("scheduler down")),
    ):
        # Should not raise
        _ingest_and_feedback(athlete, "123", garmin, db_session, slack_client, scheduler)

    # DB update was committed regardless of scheduler failure
    assert athlete.timezone == "Asia/Seoul"
    db_session.commit.assert_called()
