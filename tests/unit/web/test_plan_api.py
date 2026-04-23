"""Unit tests for GET /api/plan and POST /api/plan/sync."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest


def _flask_app():
    from flask import Flask
    from running_coach_ai.web.api.plan import bp
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["TESTING"] = True
    app.register_blueprint(bp)
    return app


def _make_athlete():
    a = MagicMock()
    a.id = 1
    a.name = "Sarah"
    return a


def _make_planned_workout(date_offset=0, workout_type="easy", distance_km=12.9, pace=5.5):
    w = MagicMock()
    w.id = 1
    w.scheduled_date = date.today() + timedelta(days=date_offset)
    w.workout_type = workout_type
    w.workout_name = "Easy Run"
    w.target_distance_km = distance_km
    w.target_pace_min_per_km = pace
    w.description = "Easy aerobic run"
    w.status = "planned"
    w.garmin_workout_id = "123"
    w.target_zones_json = None
    w.completed_workout = None
    return w


# ---------------------------------------------------------------------------
# Week grouping (Monday-first)
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.plan.get_session")
def test_plan_returns_monday_first_weeks(mock_get_session):
    app = _flask_app()
    athlete = _make_athlete()

    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.all.return_value = []

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/plan?month=2026-04")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "weeks" in data
        assert "week_labels" in data


# ---------------------------------------------------------------------------
# Requires auth
# ---------------------------------------------------------------------------

def test_plan_requires_auth():
    app = _flask_app()
    with app.test_client() as client:
        resp = client.get("/api/plan")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/plan/sync — success path
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.plan.web_event")
@patch("running_coach_ai.web.api.plan.sync_week_to_garmin")
@patch("running_coach_ai.web.api.plan.get_session")
def test_plan_sync_success(mock_get_session, mock_sync, mock_web_event):
    app = _flask_app()
    athlete = _make_athlete()

    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.all.return_value = []

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx
    mock_sync.return_value = 5

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/plan/sync")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True


# ---------------------------------------------------------------------------
# POST /api/plan/sync — Garmin error path
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.plan.web_event")
@patch("running_coach_ai.web.api.plan.sync_week_to_garmin")
@patch("running_coach_ai.web.api.plan.get_session")
def test_plan_sync_garmin_error(mock_get_session, mock_sync, mock_web_event):
    app = _flask_app()
    athlete = _make_athlete()

    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.all.return_value = []

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx
    mock_sync.side_effect = Exception("Garmin session expired")

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/plan/sync")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["ok"] is False
        assert "error" in data


# ---------------------------------------------------------------------------
# Imperial conversion
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.plan.get_session")
def test_plan_converts_to_imperial(mock_get_session):
    app = _flask_app()
    athlete = _make_athlete()
    workout = _make_planned_workout(distance_km=16.09, pace=5.59)

    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.all.return_value = [workout]

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/plan?month=2026-04")
        assert resp.status_code == 200
        data = resp.get_json()
        # Find any workout cell
        found_cell = None
        for week in data.get("weeks", []):
            for cell in week:
                if cell:
                    found_cell = cell
                    break
        if found_cell and found_cell.get("distance_mi") is not None:
            assert found_cell["distance_mi"] < 20  # 16.09 km = 10 mi
