"""Web tests for /story/<token> + /robots.txt (T078-T081)."""

from datetime import datetime
from unittest.mock import patch

import pytest
from flask import Flask

from running_coach_ai.database.models import (
    Athlete,
    AthleteStory,
    Base,
)


@pytest.fixture
def app_and_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    athlete = Athlete(
        id=1, email="sarah@x.com", web_username="sarah", web_password_hash="x",
        name="Sarah Lee", allowed=True, onboarding_complete=True,
    )
    db.add(athlete)

    # Reset rate-limit state between tests
    from running_coach_ai.web.routes import public_story as ps
    ps._ip_hits.clear()

    from contextlib import contextmanager

    @contextmanager
    def fake_get_session():
        yield db

    flask_app = Flask(__name__, template_folder="../../../running_coach_ai/web/templates")
    flask_app.config["SECRET_KEY"] = "x"
    flask_app.config["TESTING"] = True

    with patch("running_coach_ai.web.routes.public_story.get_session", fake_get_session):
        from running_coach_ai.web.routes.public_story import bp
        flask_app.register_blueprint(bp)
        yield flask_app, db, athlete
    db.close()


def _make_story(db, *, athlete_id=1, share_token="abcdefghijkl",
                published=True, deleted=False) -> AthleteStory:
    s = AthleteStory(
        athlete_id=athlete_id, milestone_type="race_complete",
        title="Sarah · Brooklyn Half",
        editorial_body="One.\n\nTwo. Two two.\n\nThree.\n\nFour.\n\nFive.",
        template_key="vogue", template_locked_by_athlete=False,
        share_token=share_token, regeneration_count=0,
        last_regenerated_at=None, created_at=datetime.utcnow(),
        published_at=datetime.utcnow() if published else None,
        deleted_at=datetime.utcnow() if deleted else None,
    )
    db.add(s); db.commit()
    return s


# ---------------------------------------------------------------------------
# T078 — token / preview / 404 logic
# ---------------------------------------------------------------------------


def test_published_story_renders_200(app_and_db):
    flask_app, db, athlete = app_and_db
    s = _make_story(db, share_token="aaaaaaaaaaaa")
    with flask_app.test_client() as c:
        resp = c.get("/story/aaaaaaaaaaaa")
    assert resp.status_code == 200
    assert b'<meta name="robots" content="noindex,nofollow">' in resp.data


def test_invalid_token_format_returns_404_no_db_hit(app_and_db):
    flask_app, _, _ = app_and_db
    with flask_app.test_client() as c:
        resp = c.get("/story/short")  # too short
    assert resp.status_code == 404


def test_unknown_token_returns_404(app_and_db):
    flask_app, db, _ = app_and_db
    with flask_app.test_client() as c:
        resp = c.get("/story/aaaaaaaaaaaa")
    assert resp.status_code == 404


def test_soft_deleted_returns_404(app_and_db):
    flask_app, db, athlete = app_and_db
    _make_story(db, share_token="bbbbbbbbbbbb", deleted=True)
    with flask_app.test_client() as c:
        resp = c.get("/story/bbbbbbbbbbbb")
    assert resp.status_code == 404


def test_unpublished_without_preview_returns_404(app_and_db):
    flask_app, db, athlete = app_and_db
    _make_story(db, share_token="cccccccccccc", published=False)
    with flask_app.test_client() as c:
        resp = c.get("/story/cccccccccccc")
    assert resp.status_code == 404


def test_unpublished_with_preview_as_owner_renders_200(app_and_db):
    flask_app, db, athlete = app_and_db
    _make_story(db, share_token="dddddddddddd", published=False)
    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["athlete_id"] = 1
            sess["is_admin"] = False
        resp = c.get("/story/dddddddddddd?preview=1")
    assert resp.status_code == 200
    # Preview banner present
    assert b"not yet public" in resp.data


def test_unpublished_with_preview_as_other_returns_404(app_and_db):
    flask_app, db, athlete = app_and_db
    _make_story(db, share_token="eeeeeeeeeeee", published=False)
    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["athlete_id"] = 99
            sess["is_admin"] = False
        resp = c.get("/story/eeeeeeeeeeee?preview=1")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# T079 — rate limit
# ---------------------------------------------------------------------------


def test_rate_limit_blocks_after_30_requests(app_and_db):
    flask_app, db, _ = app_and_db
    _make_story(db, share_token="ffffffffffff")
    with flask_app.test_client() as c:
        for _ in range(30):
            r = c.get("/story/ffffffffffff")
            assert r.status_code == 200
        r = c.get("/story/ffffffffffff")
        assert r.status_code == 429


# ---------------------------------------------------------------------------
# T080 — robots.txt
# ---------------------------------------------------------------------------


def test_robots_txt_disallows_story_namespace(app_and_db):
    flask_app, _, _ = app_and_db
    with flask_app.test_client() as c:
        resp = c.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    body = resp.data.decode("utf-8")
    assert "User-agent: *" in body
    assert "Disallow: /story/" in body
