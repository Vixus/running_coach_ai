"""Web tests for /api/admin/* story endpoints (T086-T090)."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from running_coach_ai.database.models import (
    Athlete,
    AthleteStory,
    Base,
    StoryImage,
    StoryInterviewSession,
    StoryQuestion,
)


@pytest.fixture
def app_and_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    admin = Athlete(
        id=99, email="admin@x.com", web_username="admin", web_password_hash="x",
        name="Admin", allowed=True, onboarding_complete=True, is_admin=True,
        story_opt_in=False,
    )
    athlete = Athlete(
        id=1, email="sarah@x.com", web_username="sarah", web_password_hash="x",
        name="Sarah", allowed=True, onboarding_complete=True,
        story_opt_in=True, story_intro_seen_at=None,  # intentionally None — admin bypasses
    )
    db.add_all([admin, athlete])
    db.commit()

    from contextlib import contextmanager

    @contextmanager
    def fake_get_session():
        yield db

    flask_app = Flask(__name__)
    flask_app.config["SECRET_KEY"] = "x"
    flask_app.config["TESTING"] = True

    with patch("running_coach_ai.web.api.admin.get_session", fake_get_session), \
         patch("running_coach_ai.web.auth.get_session", fake_get_session):
        from running_coach_ai.web.api.admin import bp as admin_bp
        from running_coach_ai.web.auth import bp as auth_bp
        flask_app.register_blueprint(admin_bp)
        flask_app.register_blueprint(auth_bp, url_prefix="/auth")
        yield flask_app, db, admin, athlete
    db.close()


def _login_admin(client, admin_id=99):
    with client.session_transaction() as s:
        s["athlete_id"] = admin_id
        s["is_admin"] = True


def _login_user(client, user_id=1):
    with client.session_transaction() as s:
        s["athlete_id"] = user_id
        s["is_admin"] = False


# ---------------------------------------------------------------------------
# T086 — start-interview
# ---------------------------------------------------------------------------


def test_admin_start_interview_opens_session_bypassing_intro_gate(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    with patch("running_coach_ai.coach.story.call_claude",
               return_value=json.dumps({"question": "Q?", "options": ["a","b"], "session_complete": False})):
        with flask_app.test_client() as c:
            _login_admin(c)
            resp = c.post("/api/admin/athletes/1/start-interview?trigger=pr_set")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "session_id" in data
    assert data["first_question"]["question_id"] is not None
    assert "ask a few questions" in data["first_question"]["question"].lower()


def test_admin_start_interview_rejects_unknown_trigger(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    with flask_app.test_client() as c:
        _login_admin(c)
        resp = c.post("/api/admin/athletes/1/start-interview?trigger=bogus")
    assert resp.status_code == 400
    data = resp.get_json()
    assert "valid" in data and "pr_set" in data["valid"]


def test_admin_start_interview_403_for_non_admin(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    with flask_app.test_client() as c:
        _login_user(c)
        resp = c.post("/api/admin/athletes/1/start-interview?trigger=pr_set")
    assert resp.status_code == 403


def test_admin_start_interview_blocks_when_active_session_exists(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    db.add(StoryInterviewSession(
        athlete_id=1, trigger_kind="pr_set",
        trigger_context_json={}, started_at=datetime.utcnow(),
    ))
    db.commit()
    with flask_app.test_client() as c:
        _login_admin(c)
        resp = c.post("/api/admin/athletes/1/start-interview?trigger=race_complete")
    assert resp.status_code == 400
    assert "active session" in resp.get_json()["error"].lower()


# ---------------------------------------------------------------------------
# T087 — render endpoint
# ---------------------------------------------------------------------------


def test_admin_render_swaps_template_without_lock(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    s = AthleteStory(
        id=10, athlete_id=1, milestone_type="race_complete", title="t",
        editorial_body="x", template_key="vogue", template_locked_by_athlete=False,
        share_token="abc123XYZ_-a", created_at=datetime.utcnow(),
    )
    db.add(s); db.commit()

    with patch("running_coach_ai.coach.story_templates.is_registered", return_value=True), \
         patch("running_coach_ai.coach.story_templates.list_templates",
               return_value=[MagicMock(key="outside", to_summary_dict=lambda: {"key":"outside"})]):
        with flask_app.test_client() as c:
            _login_admin(c)
            resp = c.post("/api/admin/stories/10/render?template=outside")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["template_key"] == "outside"
    db.refresh(s)
    assert s.template_locked_by_athlete is False


def test_admin_render_rejects_unknown_template(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    s = AthleteStory(
        id=10, athlete_id=1, milestone_type="race_complete", title="t",
        editorial_body="x", template_key="vogue",
        share_token="abc123XYZ_-b", created_at=datetime.utcnow(),
    )
    db.add(s); db.commit()
    with patch("running_coach_ai.coach.story_templates.is_registered", return_value=False), \
         patch("running_coach_ai.coach.story_templates.list_templates",
               return_value=[MagicMock(key="vogue", to_summary_dict=lambda: {"key":"vogue"})]):
        with flask_app.test_client() as c:
            _login_admin(c)
            resp = c.post("/api/admin/stories/10/render?template=ghost")
    assert resp.status_code == 400
    assert "available" in resp.get_json()


def test_admin_render_403_for_non_admin(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    s = AthleteStory(
        id=10, athlete_id=1, milestone_type="race_complete", title="t",
        editorial_body="x", template_key="vogue",
        share_token="abc123XYZ_-c", created_at=datetime.utcnow(),
    )
    db.add(s); db.commit()
    with flask_app.test_client() as c:
        _login_user(c)
        resp = c.post("/api/admin/stories/10/render?template=outside")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# T088 — generate-story (admin)
# ---------------------------------------------------------------------------


def test_admin_generate_story_synchronous(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    body = " ".join(["lorem"] * 500)
    fake_response = json.dumps({"editorial_body": body, "template_key": "vogue"})

    with patch("running_coach_ai.coach.story.call_claude", return_value=fake_response), \
         patch("running_coach_ai.coach.story.list_templates",
               return_value=[MagicMock(key="vogue", display_name="V",
                                       voice_description="v",
                                       trigger_affinities=["race_complete"])]), \
         patch("running_coach_ai.coach.story.is_registered", return_value=True):
        with flask_app.test_client() as c:
            _login_admin(c)
            resp = c.post("/api/admin/athletes/1/generate-story?milestone=race_complete")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "story_id" in data
    assert "/preview=1" not in data["preview_url"]  # query string format check
    assert "?preview=1" in data["preview_url"]


def test_admin_generate_story_504_on_claude_failure(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    with patch("running_coach_ai.coach.story.call_claude", return_value="not json"), \
         patch("running_coach_ai.coach.story.list_templates",
               return_value=[MagicMock(key="vogue", display_name="V",
                                       voice_description="v",
                                       trigger_affinities=["race_complete"])]):
        with flask_app.test_client() as c:
            _login_admin(c)
            resp = c.post("/api/admin/athletes/1/generate-story?milestone=race_complete")
    assert resp.status_code == 504


def test_admin_generate_story_rejects_unknown_milestone(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    with flask_app.test_client() as c:
        _login_admin(c)
        resp = c.post("/api/admin/athletes/1/generate-story?milestone=block_complete")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# T089 — hard delete
# ---------------------------------------------------------------------------


def test_admin_hard_delete_cascades(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    s = AthleteStory(
        id=10, athlete_id=1, milestone_type="race_complete", title="t",
        editorial_body="x", template_key="vogue",
        share_token="abc123XYZ_-d", created_at=datetime.utcnow(),
    )
    db.add(s); db.flush()
    db.add(StoryImage(story_id=10, filename="f.jpg"))
    db.add(StoryImage(story_id=10, filename="g.jpg"))
    sess = StoryInterviewSession(
        athlete_id=1, story_id=10, trigger_kind="pr_set",
        trigger_context_json={}, started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    db.add(sess); db.flush()
    db.add(StoryQuestion(
        session_id=sess.id, athlete_id=1, story_id=10,
        question_index=2, question="q", options_json=["a","b"],
        answer="x", is_custom_answer=False,
        asked_at=datetime.utcnow(), answered_at=datetime.utcnow(),
    ))
    db.commit()

    with flask_app.test_client() as c:
        _login_admin(c)
        resp = c.delete("/api/admin/stories/10?hard=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["deleted"]["story_rows"] == 1
    assert data["deleted"]["image_rows"] == 2
    assert db.query(AthleteStory).count() == 0
    assert db.query(StoryImage).count() == 0


def test_admin_hard_delete_400_without_hard_flag(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    s = AthleteStory(
        id=10, athlete_id=1, milestone_type="race_complete", title="t",
        editorial_body="x", template_key="vogue",
        share_token="abc123XYZ_-e", created_at=datetime.utcnow(),
    )
    db.add(s); db.commit()
    with flask_app.test_client() as c:
        _login_admin(c)
        resp = c.delete("/api/admin/stories/10")
    assert resp.status_code == 400
    assert db.query(AthleteStory).count() == 1


# ---------------------------------------------------------------------------
# T090 — list templates
# ---------------------------------------------------------------------------


def test_admin_list_templates_returns_summary(app_and_db):
    flask_app, db, admin, athlete = app_and_db
    fake_tpl = MagicMock()
    fake_tpl.to_summary_dict.return_value = {
        "key": "vogue", "display_name": "Editorial Profile",
        "voice_description": "v", "trigger_affinities": ["race_complete"],
        "thumbnail": "/static/story_templates/vogue/thumbnail.jpg",
    }
    with patch("running_coach_ai.coach.story_templates.list_templates",
               return_value=[fake_tpl]):
        with flask_app.test_client() as c:
            _login_admin(c)
            resp = c.get("/api/admin/stories/templates")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "templates" in data
    assert data["templates"][0]["key"] == "vogue"
