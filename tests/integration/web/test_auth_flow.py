"""Integration test: full auth flow with real Flask app and in-memory SQLite DB."""

import pytest
from unittest.mock import patch, MagicMock
from werkzeug.security import generate_password_hash

from running_coach_ai.database.models import Athlete, Base


@pytest.fixture()
def app_and_db():
    """Create a Flask test app with an in-memory SQLite DB seeded with one athlete."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    athlete = Athlete(
        name="Sarah",
        web_username="sarah",
        web_password_hash=generate_password_hash("hunter2"),
        is_admin=False,
        onboarding_complete=True,
        allowed=True,
    )
    db.add(athlete)
    db.commit()
    db.refresh(athlete)

    # Patch get_session to use the test session
    from contextlib import contextmanager

    @contextmanager
    def fake_get_session():
        yield db

    with patch("running_coach_ai.web.auth.get_session", fake_get_session):
        from flask import Flask
        from running_coach_ai.web.auth import bp

        flask_app = Flask(__name__)
        flask_app.config["SECRET_KEY"] = "integration-test-key"
        flask_app.config["TESTING"] = True
        flask_app.register_blueprint(bp)

        yield flask_app, db

    db.close()
    engine.dispose()


def test_full_auth_flow(app_and_db):
    """Login → session cookie set → logout → protected endpoint returns 401."""
    flask_app, db = app_and_db

    with flask_app.test_client() as client:
        # Step 1: login
        resp = client.post("/auth/login", json={"username": "sarah", "password": "hunter2"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["name"] == "Sarah"
        assert data["is_admin"] is False

        # Step 2: logout
        resp = client.post("/auth/logout")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True


def test_login_wrong_password_returns_401(app_and_db):
    flask_app, db = app_and_db
    with flask_app.test_client() as client:
        resp = client.post("/auth/login", json={"username": "sarah", "password": "wrong"})
        assert resp.status_code == 401


def test_login_unknown_user_returns_401(app_and_db):
    flask_app, db = app_and_db
    with flask_app.test_client() as client:
        resp = client.post("/auth/login", json={"username": "ghost", "password": "any"})
        assert resp.status_code == 401
