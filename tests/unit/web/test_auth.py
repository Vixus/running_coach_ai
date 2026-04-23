"""Unit tests for web dashboard auth routes and decorators."""

import pytest
from unittest.mock import MagicMock, patch
from werkzeug.security import generate_password_hash


def _make_app():
    """Create a test Flask app with minimal config."""
    import sys, os
    # Ensure we can import
    from running_coach_ai.config import settings
    settings_orig_key = settings.WEB_SECRET_KEY

    from flask import Flask
    # We test auth.py logic directly rather than through create_app to avoid
    # importing missing blueprint modules during test setup
    from running_coach_ai.web.auth import bp, login_required, admin_required
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret-key"
    app.config["TESTING"] = True
    app.register_blueprint(bp)
    return app


def _make_athlete(athlete_id=1, name="Sarah", is_admin=False, username="sarah", password="hunter2"):
    a = MagicMock()
    a.id = athlete_id
    a.name = name
    a.is_admin = is_admin
    a.web_username = username
    a.web_password_hash = generate_password_hash(password)
    return a


# ---------------------------------------------------------------------------
# Login success
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.auth.get_session")
def test_login_success(mock_get_session):
    app = _make_app()
    athlete = _make_athlete()

    ctx_manager = MagicMock()
    ctx_manager.__enter__ = MagicMock(return_value=MagicMock(
        query=MagicMock(return_value=MagicMock(
            filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=athlete)))
        ))
    ))
    ctx_manager.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx_manager

    with app.test_client() as client:
        resp = client.post("/auth/login", json={"username": "sarah", "password": "hunter2"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["athlete_id"] == 1
        assert data["name"] == "Sarah"
        assert data["is_admin"] is False


# ---------------------------------------------------------------------------
# Login failure — wrong password
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.auth.get_session")
def test_login_wrong_password(mock_get_session):
    app = _make_app()
    athlete = _make_athlete()

    ctx_manager = MagicMock()
    ctx_manager.__enter__ = MagicMock(return_value=MagicMock(
        query=MagicMock(return_value=MagicMock(
            filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=athlete)))
        ))
    ))
    ctx_manager.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx_manager

    with app.test_client() as client:
        resp = client.post("/auth/login", json={"username": "sarah", "password": "wrong"})
        assert resp.status_code == 401
        assert "error" in resp.get_json()


# ---------------------------------------------------------------------------
# Login failure — unknown user
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.auth.get_session")
def test_login_unknown_user(mock_get_session):
    app = _make_app()

    ctx_manager = MagicMock()
    ctx_manager.__enter__ = MagicMock(return_value=MagicMock(
        query=MagicMock(return_value=MagicMock(
            filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None)))
        ))
    ))
    ctx_manager.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx_manager

    with app.test_client() as client:
        resp = client.post("/auth/login", json={"username": "nobody", "password": "x"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.auth.get_session")
def test_logout_clears_session(mock_get_session):
    app = _make_app()
    athlete = _make_athlete()

    ctx_manager = MagicMock()
    ctx_manager.__enter__ = MagicMock(return_value=MagicMock(
        query=MagicMock(return_value=MagicMock(
            filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=athlete)))
        ))
    ))
    ctx_manager.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx_manager

    with app.test_client() as client:
        client.post("/auth/login", json={"username": "sarah", "password": "hunter2"})
        resp = client.post("/auth/logout")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True


# ---------------------------------------------------------------------------
# login_required blocks unauthenticated
# ---------------------------------------------------------------------------

def test_login_required_blocks_unauthenticated():
    from running_coach_ai.web.auth import login_required
    from flask import Flask, jsonify

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["TESTING"] = True

    @app.route("/protected")
    @login_required
    def protected():
        return jsonify({"ok": True})

    with app.test_client() as client:
        resp = client.get("/protected")
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "Unauthorized"


# ---------------------------------------------------------------------------
# admin_required blocks non-admin
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.auth.get_session")
def test_admin_required_blocks_non_admin(mock_get_session):
    from running_coach_ai.web.auth import admin_required
    from flask import Flask, jsonify, session

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["TESTING"] = True

    athlete = _make_athlete(is_admin=False)
    ctx_manager = MagicMock()
    db_mock = MagicMock()
    db_mock.get.return_value = athlete
    ctx_manager.__enter__ = MagicMock(return_value=db_mock)
    ctx_manager.__exit__ = MagicMock(return_value=False)
    mock_get_session.return_value = ctx_manager

    @app.route("/admin-only")
    @admin_required
    def admin_only():
        return jsonify({"ok": True})

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/admin-only")
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "Forbidden"
