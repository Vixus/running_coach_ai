"""Unit tests for WebEventHandler and web_event() helper."""

import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# WebEventHandler.emit()
# ---------------------------------------------------------------------------

def test_handler_emit_writes_to_db():
    from running_coach_ai.web.events import WebEventHandler
    from running_coach_ai.database.models import WebEvent

    handler = WebEventHandler()

    db = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)

    record = logging.LogRecord(
        name="running_coach_ai.web",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="Test error",
        args=(),
        exc_info=None,
    )
    record.web_category = "garmin"
    record.web_athlete_id = 1

    with patch("running_coach_ai.database.session.get_session", return_value=ctx):
        handler.emit(record)

    db.add.assert_called_once()
    added_event = db.add.call_args[0][0]
    assert isinstance(added_event, WebEvent)
    assert added_event.severity == "error"
    assert added_event.category == "garmin"
    assert added_event.athlete_id == 1


def test_handler_maps_warning_to_warn():
    from running_coach_ai.web.events import WebEventHandler
    from running_coach_ai.database.models import WebEvent

    handler = WebEventHandler()
    db = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)

    record = logging.LogRecord(
        name="test", level=logging.WARNING,
        pathname="", lineno=0, msg="warn msg", args=(), exc_info=None,
    )

    with patch("running_coach_ai.database.session.get_session", return_value=ctx):
        handler.emit(record)

    added = db.add.call_args[0][0]
    assert added.severity == "warn"


# ---------------------------------------------------------------------------
# web_event() helper
# ---------------------------------------------------------------------------

def test_web_event_writes_row():
    from running_coach_ai.web.events import web_event
    from running_coach_ai.database.models import WebEvent

    db = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)

    with patch("running_coach_ai.database.session.get_session", return_value=ctx):
        web_event(severity="info", category="auth", message="Login success", athlete_id=5)

    db.add.assert_called_once()
    ev = db.add.call_args[0][0]
    assert ev.severity == "info"
    assert ev.category == "auth"
    assert ev.message == "Login success"
    assert ev.athlete_id == 5


def test_web_event_invalid_severity_defaults_to_info():
    from running_coach_ai.web.events import web_event
    from running_coach_ai.database.models import WebEvent

    db = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)

    with patch("running_coach_ai.database.session.get_session", return_value=ctx):
        web_event(severity="critical", category="auth", message="Test")

    ev = db.add.call_args[0][0]
    assert ev.severity == "info"


def test_web_event_details_json_serialized():
    from running_coach_ai.web.events import web_event

    db = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)

    details = {"latency_ms": 350, "status": "ok"}
    with patch("running_coach_ai.database.session.get_session", return_value=ctx):
        web_event(severity="info", category="claude", message="Chat response", details=details)

    ev = db.add.call_args[0][0]
    assert ev.details_json == details


# ---------------------------------------------------------------------------
# cleanup_old_events()
# ---------------------------------------------------------------------------

def test_cleanup_old_events_deletes_stale_rows():
    from running_coach_ai.web.events import cleanup_old_events

    db = MagicMock()
    db.query.return_value.filter.return_value.delete.return_value = 5

    count = cleanup_old_events(db)

    assert count == 5
    db.query.return_value.filter.return_value.delete.assert_called_once()
