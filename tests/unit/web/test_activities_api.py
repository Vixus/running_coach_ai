"""Unit tests for GET /api/activities and POST /api/activities/{id}/feedback."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest


def _flask_app():
    from flask import Flask
    from running_coach_ai.web.api.activities import bp
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["TESTING"] = True
    app.register_blueprint(bp)
    return app


def _make_athlete():
    a = MagicMock(); a.id = 1; a.name = "Sarah"; return a


def _make_completed_workout(cw_id=1, distance_km=16.1, pace=4.64, coach_analysis=None):
    cw = MagicMock()
    cw.id = cw_id
    cw.athlete_id = 1
    cw.date = date.today()
    cw.distance_km = distance_km
    cw.duration_seconds = 4800
    cw.avg_pace_min_per_km = pace
    cw.avg_hr = 155
    cw.avg_cadence_spm = 184
    cw.avg_ground_contact_time_ms = 225.0
    cw.avg_vertical_oscillation_cm = 8.2
    cw.avg_power_w = 280.0
    cw.coach_analysis = coach_analysis
    cw.workout_type = "tempo"
    cw.planned_workout = None
    cw.telemetry = None
    cw.run_feedback = None
    cw.garmin_activity_id = "act123"
    return cw


# ---------------------------------------------------------------------------
# Basic pagination
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.activities.get_session")
def test_activities_returns_list(mock_gs):
    app = _flask_app()
    athlete = _make_athlete()
    cw = _make_completed_workout()

    db = MagicMock()
    db.get.return_value = athlete
    q = MagicMock()
    q.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [cw]
    q.filter.return_value.count.return_value = 1
    q.filter.return_value.all.return_value = [cw]
    db.query.return_value = q

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/activities?limit=20&offset=0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "activities" in data
        assert "summary" in data


# ---------------------------------------------------------------------------
# coach_analysis present when populated
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.activities.get_session")
def test_activities_includes_coach_analysis_when_present(mock_gs):
    app = _flask_app()
    athlete = _make_athlete()
    cw = _make_completed_workout(coach_analysis="Strong LT work today.")

    db = MagicMock()
    db.get.return_value = athlete
    q = MagicMock()
    q.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [cw]
    q.filter.return_value.count.return_value = 1
    q.filter.return_value.all.return_value = [cw]
    db.query.return_value = q

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/activities")
        data = resp.get_json()
        acts = data.get("activities", [])
        if acts:
            assert acts[0].get("coach_analysis") == "Strong LT work today."


# ---------------------------------------------------------------------------
# coach_analysis omitted when null
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.activities.get_session")
def test_activities_omits_coach_analysis_when_null(mock_gs):
    app = _flask_app()
    athlete = _make_athlete()
    cw = _make_completed_workout(coach_analysis=None)

    db = MagicMock()
    db.get.return_value = athlete
    q = MagicMock()
    q.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [cw]
    q.filter.return_value.count.return_value = 1
    q.filter.return_value.all.return_value = [cw]
    db.query.return_value = q

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/activities")
        data = resp.get_json()
        acts = data.get("activities", [])
        if acts:
            assert "coach_analysis" not in acts[0]


# ---------------------------------------------------------------------------
# Requires auth
# ---------------------------------------------------------------------------

def test_activities_requires_auth():
    app = _flask_app()
    with app.test_client() as client:
        resp = client.get("/api/activities")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST feedback — validation
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.activities.get_session")
def test_feedback_rpe_out_of_range(mock_gs):
    app = _flask_app()
    athlete = _make_athlete()
    cw = _make_completed_workout()

    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.first.return_value = cw
    db.query.return_value.filter.return_value.filter.return_value.first.return_value = None

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/activities/1/feedback", json={"feel_score": 2, "rpe": 11, "notes": "good"})
        assert resp.status_code == 422


@patch("running_coach_ai.web.api.activities.get_session")
def test_feedback_feel_score_out_of_range(mock_gs):
    app = _flask_app()
    athlete = _make_athlete()
    cw = _make_completed_workout()

    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.first.return_value = cw
    db.query.return_value.filter.return_value.filter.return_value.first.return_value = None

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/activities/1/feedback", json={"feel_score": 5, "rpe": 7, "notes": ""})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST feedback — 404 on unknown activity
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.activities.get_session")
def test_feedback_404_unknown_activity(mock_gs):
    app = _flask_app()
    athlete = _make_athlete()

    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.first.return_value = None

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/activities/9999/feedback", json={"feel_score": 3, "rpe": 6, "notes": ""})
        assert resp.status_code == 404
