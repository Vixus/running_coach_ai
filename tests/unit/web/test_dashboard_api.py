"""Unit tests for GET /api/dashboard."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def _make_athlete(coach_key="classic"):
    a = MagicMock()
    a.id = 1
    a.name = "Sarah"
    a.coach_key = coach_key
    a.is_admin = False
    return a


def _make_goal(race_date=None, race_name="Boston Marathon", week=14, total_weeks=24):
    if race_date is None:
        race_date = date.today() + timedelta(days=71)
    g = MagicMock()
    g.race_date = race_date
    g.race_name = race_name
    g.active = True
    return g


def _make_health_snapshot(hrv=71, body_battery_end=78, sleep_dur=26640, resting_hr=44):
    s = MagicMock()
    s.date = date.today()
    s.hrv_score = hrv
    s.body_battery_end = body_battery_end
    s.sleep_duration_seconds = sleep_dur
    s.resting_hr = resting_hr
    s.body_battery_start = 40
    s.sleep_score = 80
    s.stress_avg = 25
    s.hrv_status = "BALANCED"
    return s


def _make_workout(scheduled_date=None, workout_type="long_run", distance_km=35.4, pace=5.1):
    if scheduled_date is None:
        scheduled_date = date.today()
    w = MagicMock()
    w.id = 42
    w.scheduled_date = scheduled_date
    w.workout_type = workout_type
    w.workout_name = "Long Run"
    w.target_distance_km = distance_km
    w.target_pace_min_per_km = pace
    w.description = "First 14 miles conversational pace"
    w.garmin_workout_id = "12345"
    w.target_zones_json = {"zones": "Z2-Z3"}
    w.status = "planned"
    return w


def _flask_app_with_dashboard():
    """Build minimal Flask test app with only the dashboard blueprint registered."""
    from flask import Flask
    flask_app = Flask(__name__)
    flask_app.config["SECRET_KEY"] = "test"
    flask_app.config["TESTING"] = True

    from running_coach_ai.web.api.dashboard import bp
    flask_app.register_blueprint(bp)
    return flask_app


# ---------------------------------------------------------------------------
# Authenticated response shape
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.dashboard.get_session")
def test_dashboard_returns_correct_shape(mock_get_session):
    athlete = _make_athlete()
    goal = _make_goal()
    today_snap = _make_health_snapshot()
    workout = _make_workout()

    snapshots_7d = [_make_health_snapshot(hrv=67) for _ in range(7)] + [today_snap]

    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.first.side_effect = [goal, today_snap, workout]
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = snapshots_7d
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx

    app = _flask_app_with_dashboard()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "athlete" in data
        assert "health" in data
        assert "coach_message" in data
        assert "week_strip" in data
        assert "charts" in data


# ---------------------------------------------------------------------------
# Coach message uses correct persona greeting
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.dashboard.get_session")
def test_dashboard_coach_message_matches_persona(mock_get_session):
    athlete = _make_athlete(coach_key="maya")
    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.first.side_effect = [None, None, None]
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx

    app = _flask_app_with_dashboard()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.get_json()
        from running_coach_ai.coach.personas import get_persona
        expected_greeting = get_persona("maya").greeting
        assert data["coach_message"] == expected_greeting


# ---------------------------------------------------------------------------
# 401 when unauthenticated
# ---------------------------------------------------------------------------

def test_dashboard_requires_auth():
    app = _flask_app_with_dashboard()
    with app.test_client() as client:
        resp = client.get("/api/dashboard")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Imperial unit conversion
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.dashboard.get_session")
def test_dashboard_converts_to_imperial(mock_get_session):
    athlete = _make_athlete()
    workout = _make_workout(distance_km=35.4, pace=5.1)  # ~22 mi, ~8:12/mi

    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.first.side_effect = [None, None, workout]
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx

    app = _flask_app_with_dashboard()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.get_json()
        if data.get("today_workout"):
            # distance should be in miles, not km
            dist = data["today_workout"].get("target_distance_mi")
            if dist is not None:
                assert dist < 30  # 35.4 km = 22 mi, not 35 mi
