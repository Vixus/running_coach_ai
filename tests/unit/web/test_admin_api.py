"""Unit tests for GET /api/admin/events."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def _flask_app():
    from flask import Flask
    from running_coach_ai.web.api.admin import bp
    from running_coach_ai.web.auth import bp as auth_bp
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["TESTING"] = True
    app.register_blueprint(auth_bp)
    app.register_blueprint(bp)
    return app


def _make_admin_athlete():
    a = MagicMock()
    a.id = 1
    a.name = "Admin"
    a.is_admin = True
    return a


def _make_regular_athlete():
    a = MagicMock()
    a.id = 2
    a.name = "Regular"
    a.is_admin = False
    return a


def _make_event(category="garmin", severity="error", message="Test event"):
    e = MagicMock()
    e.id = 1
    e.timestamp = datetime(2026, 4, 18, 9, 0, 0)
    e.severity = severity
    e.category = category
    e.message = message
    e.athlete_id = 1
    e.details_json = {"error_type": "GarminError"}
    return e


# ---------------------------------------------------------------------------
# Authentication / authorization
# ---------------------------------------------------------------------------

def test_admin_events_requires_auth():
    app = _flask_app()
    with app.test_client() as client:
        resp = client.get("/api/admin/events")
        assert resp.status_code == 401


@patch("running_coach_ai.web.auth.get_session")
def test_admin_events_non_admin_gets_403(mock_gs):
    app = _flask_app()
    athlete = _make_regular_athlete()

    db = MagicMock()
    db.get.return_value = athlete

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 2
        resp = client.get("/api/admin/events")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Basic success
# ---------------------------------------------------------------------------

def _make_admin_ctx(admin_athlete):
    """Return a (db, ctx) mock pair with admin athlete."""
    db = MagicMock()
    db.get.return_value = admin_athlete

    query_mock = MagicMock()
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.count.return_value = 1
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []
    db.query.return_value = query_mock

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    return db, query_mock, ctx


@patch("running_coach_ai.web.api.admin.cleanup_old_events")
@patch("running_coach_ai.web.api.admin.get_session")
@patch("running_coach_ai.web.auth.get_session")
def test_admin_events_returns_list(mock_auth_gs, mock_admin_gs, mock_cleanup):
    app = _flask_app()
    athlete = _make_admin_athlete()
    ev = _make_event()

    # auth.py get_session mock
    auth_db = MagicMock()
    auth_db.get.return_value = athlete
    auth_ctx = MagicMock()
    auth_ctx.__enter__ = MagicMock(return_value=auth_db)
    auth_ctx.__exit__ = MagicMock(return_value=False)
    mock_auth_gs.return_value = auth_ctx

    # admin blueprint get_session mock
    admin_db = MagicMock()
    admin_db.get.return_value = athlete
    qm = MagicMock()
    qm.filter.return_value = qm
    qm.order_by.return_value = qm
    qm.count.return_value = 1
    qm.offset.return_value = qm
    qm.limit.return_value = qm
    qm.all.return_value = [ev]
    admin_db.query.return_value = qm
    admin_ctx = MagicMock()
    admin_ctx.__enter__ = MagicMock(return_value=admin_db)
    admin_ctx.__exit__ = MagicMock(return_value=False)
    mock_admin_gs.return_value = admin_ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/admin/events")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "events" in data
        assert "total" in data
        assert data["total"] == 1
        assert len(data["events"]) == 1


@patch("running_coach_ai.web.api.admin.cleanup_old_events")
@patch("running_coach_ai.web.api.admin.get_session")
@patch("running_coach_ai.web.auth.get_session")
def test_admin_events_response_shape(mock_auth_gs, mock_admin_gs, mock_cleanup):
    app = _flask_app()
    athlete = _make_admin_athlete()
    ev = _make_event(category="garmin", severity="error", message="Sync failed")

    auth_db = MagicMock()
    auth_db.get.return_value = athlete
    auth_ctx = MagicMock()
    auth_ctx.__enter__ = MagicMock(return_value=auth_db)
    auth_ctx.__exit__ = MagicMock(return_value=False)
    mock_auth_gs.return_value = auth_ctx

    admin_db = MagicMock()
    admin_db.get.return_value = athlete
    qm = MagicMock()
    qm.filter.return_value = qm
    qm.order_by.return_value = qm
    qm.count.return_value = 1
    qm.offset.return_value = qm
    qm.limit.return_value = qm
    qm.all.return_value = [ev]
    admin_db.query.return_value = qm
    admin_ctx = MagicMock()
    admin_ctx.__enter__ = MagicMock(return_value=admin_db)
    admin_ctx.__exit__ = MagicMock(return_value=False)
    mock_admin_gs.return_value = admin_ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/admin/events")
        data = resp.get_json()
        item = data["events"][0]
        for key in ("id", "timestamp", "severity", "category", "message"):
            assert key in item, f"Missing key in event response: {key}"
        assert item["category"] == "garmin"
        assert item["severity"] == "error"


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.admin.cleanup_old_events")
@patch("running_coach_ai.web.api.admin.get_session")
@patch("running_coach_ai.web.auth.get_session")
def test_admin_events_category_filter_applied(mock_auth_gs, mock_admin_gs, mock_cleanup):
    app = _flask_app()
    athlete = _make_admin_athlete()

    auth_db = MagicMock()
    auth_db.get.return_value = athlete
    auth_ctx = MagicMock()
    auth_ctx.__enter__ = MagicMock(return_value=auth_db)
    auth_ctx.__exit__ = MagicMock(return_value=False)
    mock_auth_gs.return_value = auth_ctx

    admin_db = MagicMock()
    admin_db.get.return_value = athlete
    qm = MagicMock()
    qm.filter.return_value = qm
    qm.order_by.return_value = qm
    qm.count.return_value = 0
    qm.offset.return_value = qm
    qm.limit.return_value = qm
    qm.all.return_value = []
    admin_db.query.return_value = qm
    admin_ctx = MagicMock()
    admin_ctx.__enter__ = MagicMock(return_value=admin_db)
    admin_ctx.__exit__ = MagicMock(return_value=False)
    mock_admin_gs.return_value = admin_ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/admin/events?category=garmin")
        assert resp.status_code == 200
        assert qm.filter.call_count >= 1


@patch("running_coach_ai.web.api.admin.cleanup_old_events")
@patch("running_coach_ai.web.api.admin.get_session")
@patch("running_coach_ai.web.auth.get_session")
def test_admin_events_pagination_limit_respected(mock_auth_gs, mock_admin_gs, mock_cleanup):
    app = _flask_app()
    athlete = _make_admin_athlete()

    auth_db = MagicMock()
    auth_db.get.return_value = athlete
    auth_ctx = MagicMock()
    auth_ctx.__enter__ = MagicMock(return_value=auth_db)
    auth_ctx.__exit__ = MagicMock(return_value=False)
    mock_auth_gs.return_value = auth_ctx

    admin_db = MagicMock()
    admin_db.get.return_value = athlete
    qm = MagicMock()
    qm.filter.return_value = qm
    qm.order_by.return_value = qm
    qm.count.return_value = 100
    qm.offset.return_value = qm
    qm.limit.return_value = qm
    qm.all.return_value = []
    admin_db.query.return_value = qm
    admin_ctx = MagicMock()
    admin_ctx.__enter__ = MagicMock(return_value=admin_db)
    admin_ctx.__exit__ = MagicMock(return_value=False)
    mock_admin_gs.return_value = admin_ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/admin/events?limit=10&offset=5")
        assert resp.status_code == 200
        qm.limit.assert_called_with(10)
        qm.offset.assert_called_with(5)
