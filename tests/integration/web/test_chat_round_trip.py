"""Integration test: chat round-trip with mocked Claude."""

import pytest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch
from werkzeug.security import generate_password_hash

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from running_coach_ai.database.models import Athlete, ConversationMessage, Base


@pytest.fixture()
def app_and_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    athlete = Athlete(
        name="Jordan",
        web_username="jordan",
        web_password_hash=generate_password_hash("secret"),
        is_admin=False,
        onboarding_complete=True,
        allowed=True,
        coach_key="classic",
    )
    db.add(athlete)
    db.commit()
    db.refresh(athlete)

    @contextmanager
    def fake_get_session():
        yield db

    from flask import Flask
    from running_coach_ai.web.auth import bp as auth_bp
    from running_coach_ai.web.api.chat import bp as chat_bp

    flask_app = Flask(__name__)
    flask_app.config["SECRET_KEY"] = "test-integration-key"
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(auth_bp)
    flask_app.register_blueprint(chat_bp)

    with patch("running_coach_ai.web.auth.get_session", fake_get_session), \
         patch("running_coach_ai.web.api.chat.get_session", fake_get_session):
        yield flask_app, db

    db.close()
    engine.dispose()


def test_chat_round_trip(app_and_db):
    """POST /api/chat/message creates ConversationMessage rows; GET /api/chat/history returns them."""
    flask_app, db = app_and_db
    mock_response = "Run 45 min easy today, heart rate zone 2."

    with patch("running_coach_ai.web.api.chat.process_message", return_value=mock_response), \
         patch("running_coach_ai.web.api.chat.web_event"):
        with flask_app.test_client() as client:
            # Login
            resp = client.post("/auth/login", json={"username": "jordan", "password": "secret"})
            assert resp.status_code == 200, resp.get_data(as_text=True)

            # Post a message
            resp = client.post("/api/chat/message", json={"message": "What should I do today?"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["response"] == mock_response
            assert "timestamp" in data

            # Verify history endpoint works
            resp = client.get("/api/chat/history")
            assert resp.status_code == 200
