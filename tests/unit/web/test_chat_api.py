"""Unit tests for GET /api/chat/history and POST /api/chat/message."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


def _flask_app():
    from flask import Flask
    from running_coach_ai.web.api.chat import bp
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["TESTING"] = True
    app.register_blueprint(bp)
    return app


def _make_athlete():
    a = MagicMock(); a.id = 1; a.name = "Sarah"; a.coach_key = "classic"; return a


def _make_msg(role="user", content="Hello", source="slack"):
    m = MagicMock()
    m.role = role
    m.content = content
    m.source = source
    m.created_at = datetime(2026, 4, 18, 9, 0, 0)
    return m


# ---------------------------------------------------------------------------
# GET /api/chat/history
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.chat.get_session")
def test_chat_history_returns_messages(mock_gs):
    app = _flask_app()
    athlete = _make_athlete()
    msgs = [_make_msg("user", "Hi", "slack"), _make_msg("assistant", "Hello!", "slack")]

    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = msgs

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/chat/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "messages" in data
        assert len(data["messages"]) == 2


@patch("running_coach_ai.web.api.chat.get_session")
def test_chat_history_includes_source_field(mock_gs):
    app = _flask_app()
    athlete = _make_athlete()
    msgs = [_make_msg("user", "Test", "web")]

    db = MagicMock()
    db.get.return_value = athlete
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = msgs

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/chat/history")
        data = resp.get_json()
        assert data["messages"][0]["source"] == "web"


def test_chat_history_requires_auth():
    app = _flask_app()
    with app.test_client() as client:
        resp = client.get("/api/chat/history")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/chat/message
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.chat.web_event")
@patch("running_coach_ai.web.api.chat.process_message")
@patch("running_coach_ai.web.api.chat.get_session")
def test_chat_message_returns_response(mock_gs, mock_process, mock_event):
    app = _flask_app()
    athlete = _make_athlete()
    mock_process.return_value = "Great question! Run easy today."

    db = MagicMock()
    db.get.return_value = athlete

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/chat/message", json={"message": "How should I train today?"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["response"] == "Great question! Run easy today."
        assert "timestamp" in data


@patch("running_coach_ai.web.api.chat.web_event")
@patch("running_coach_ai.web.api.chat.process_message")
@patch("running_coach_ai.web.api.chat.get_session")
def test_chat_message_appended_with_web_source(mock_gs, mock_process, mock_event):
    app = _flask_app()
    athlete = _make_athlete()
    mock_process.return_value = "Run 45 min easy."

    db = MagicMock()
    db.get.return_value = athlete

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        client.post("/api/chat/message", json={"message": "Plan?"})
        call_kwargs = mock_process.call_args
        assert call_kwargs.kwargs.get("source") == "web" or (len(call_kwargs.args) >= 4 and call_kwargs.args[3] == "web")


@patch("running_coach_ai.web.api.chat.web_event")
@patch("running_coach_ai.web.api.chat.process_message")
@patch("running_coach_ai.web.api.chat.get_session")
def test_chat_message_claude_error_returns_503(mock_gs, mock_process, mock_event):
    import anthropic
    app = _flask_app()
    athlete = _make_athlete()
    mock_process.side_effect = anthropic.APIError("Service unavailable", request=MagicMock(), body=None)

    db = MagicMock()
    db.get.return_value = athlete

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/chat/message", json={"message": "Hello"})
        assert resp.status_code == 503
