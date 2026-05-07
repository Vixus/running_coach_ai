"""Web tests for /api/stories endpoints (T061, T069, T070)."""

import io
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from PIL import Image

from running_coach_ai.database.models import (
    Athlete,
    AthleteStory,
    Base,
    StoryImage,
)


@pytest.fixture
def app_and_db(tmp_path, monkeypatch):
    """Flask app + in-memory SQLite + temp STORY_IMAGE_DIR."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    athlete = Athlete(
        id=1, email="sarah@x.com", web_username="sarah",
        web_password_hash="x", name="Sarah", allowed=True,
        onboarding_complete=True, story_opt_in=True,
        story_intro_seen_at=datetime.utcnow(),
    )
    db.add(athlete)

    # One in-progress story (draft) for the current_story queries
    story = AthleteStory(
        id=10, athlete_id=1, milestone_type="race_complete",
        title="Sarah · Brooklyn Half", editorial_body="lorem " * 100,
        template_key="vogue", template_locked_by_athlete=False,
        share_token="abcdef123456", regeneration_count=0,
        last_regenerated_at=None, created_at=datetime.utcnow(),
    )
    db.add(story)
    db.commit()

    monkeypatch.setattr("running_coach_ai.config.settings.STORY_IMAGE_DIR", str(tmp_path))
    # The stories.py module reads settings.STORY_IMAGE_DIR at request time, so monkeypatch
    # the live module attribute too if it ever caches.

    from contextlib import contextmanager

    @contextmanager
    def fake_get_session():
        yield db

    flask_app = Flask(__name__)
    flask_app.config["SECRET_KEY"] = "x"
    flask_app.config["TESTING"] = True
    with patch("running_coach_ai.web.api.stories.get_session", fake_get_session), \
         patch("running_coach_ai.web.auth.get_session", fake_get_session):
        from running_coach_ai.web.api.stories import bp as stories_bp
        from running_coach_ai.web.auth import bp as auth_bp
        flask_app.register_blueprint(stories_bp)
        flask_app.register_blueprint(auth_bp, url_prefix="/auth")
        yield flask_app, db, athlete, str(tmp_path)
    db.close()


def _login(client):
    with client.session_transaction() as s:
        s["athlete_id"] = 1
        s["is_admin"] = False


def _make_image_bytes(fmt="JPEG", size=(100, 100)):
    img = Image.new("RGB", size, color=(120, 80, 40))
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# T061 — POST /api/stories/<id>/template
# ---------------------------------------------------------------------------


def test_swap_template_to_valid_key(app_and_db):
    flask_app, db, athlete, _ = app_and_db
    with patch("running_coach_ai.web.api.stories.is_registered", return_value=True), \
         patch("running_coach_ai.web.api.stories.list_templates",
               return_value=[MagicMock(key="outside")]):
        with flask_app.test_client() as c:
            _login(c)
            resp = c.post("/api/stories/10/template", json={"template_key": "outside"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["template_key"] == "outside"
    db.refresh(db.get(AthleteStory, 10))
    s = db.get(AthleteStory, 10)
    assert s.template_locked_by_athlete is True


def test_swap_template_rejects_unknown_key(app_and_db):
    flask_app, db, athlete, _ = app_and_db
    with patch("running_coach_ai.web.api.stories.is_registered", return_value=False), \
         patch("running_coach_ai.web.api.stories.list_templates",
               return_value=[MagicMock(key="vogue"), MagicMock(key="outside")]):
        with flask_app.test_client() as c:
            _login(c)
            resp = c.post("/api/stories/10/template", json={"template_key": "nonsense"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert "available" in data
    assert "Unknown template key" in data["error"]


def test_swap_template_404_for_other_athletes_story(app_and_db):
    flask_app, db, athlete, _ = app_and_db
    with patch("running_coach_ai.web.api.stories.is_registered", return_value=True), \
         patch("running_coach_ai.web.api.stories.list_templates", return_value=[]):
        with flask_app.test_client() as c:
            _login(c)
            resp = c.post("/api/stories/9999/template", json={"template_key": "vogue"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# T069 — image upload validation
# ---------------------------------------------------------------------------


def test_upload_valid_jpeg_creates_row_and_file(app_and_db):
    flask_app, db, athlete, tmp_dir = app_and_db
    raw = _make_image_bytes("JPEG")

    with patch("running_coach_ai.web.api.stories.settings.STORY_IMAGE_DIR", tmp_dir):
        with flask_app.test_client() as c:
            _login(c)
            resp = c.post("/api/stories/current/images",
                          data={"file": (io.BytesIO(raw), "photo.jpg", "image/jpeg")},
                          content_type="multipart/form-data")
    assert resp.status_code == 201
    data = resp.get_json()
    assert "image" in data
    assert data["image"]["caption"] is None

    images = db.query(StoryImage).all()
    assert len(images) == 1


def test_upload_rejects_oversized(app_and_db):
    flask_app, db, athlete, tmp_dir = app_and_db
    raw = b"x" * (11 * 1024 * 1024)
    with flask_app.test_client() as c:
        _login(c)
        resp = c.post("/api/stories/current/images",
                      data={"file": (io.BytesIO(raw), "big.jpg", "image/jpeg")},
                      content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "10 MB" in resp.get_json()["error"]


def test_upload_rejects_wrong_mime(app_and_db):
    flask_app, db, athlete, tmp_dir = app_and_db
    with flask_app.test_client() as c:
        _login(c)
        resp = c.post("/api/stories/current/images",
                      data={"file": (io.BytesIO(b"hello"), "doc.txt", "text/plain")},
                      content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "Unsupported" in resp.get_json()["error"]


def test_upload_rejects_when_at_limit(app_and_db):
    flask_app, db, athlete, tmp_dir = app_and_db
    # Pre-populate 5 images
    for i in range(5):
        db.add(StoryImage(story_id=10, filename=f"f{i}.jpg", sort_order=i))
    db.commit()

    raw = _make_image_bytes("JPEG")
    with patch("running_coach_ai.web.api.stories.settings.STORY_IMAGE_DIR", tmp_dir):
        with flask_app.test_client() as c:
            _login(c)
            resp = c.post("/api/stories/current/images",
                          data={"file": (io.BytesIO(raw), "p.jpg", "image/jpeg")},
                          content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "Maximum 5 photos" in resp.get_json()["error"]


def test_upload_rejects_corrupt_image(app_and_db):
    flask_app, db, athlete, tmp_dir = app_and_db
    with flask_app.test_client() as c:
        _login(c)
        resp = c.post("/api/stories/current/images",
                      data={"file": (io.BytesIO(b"\xff\xd8\xff garbage"), "fake.jpg", "image/jpeg")},
                      content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "decode" in resp.get_json()["error"].lower()


# ---------------------------------------------------------------------------
# T070 — caption PATCH + DELETE
# ---------------------------------------------------------------------------


def test_patch_caption_persists(app_and_db):
    flask_app, db, athlete, _ = app_and_db
    img = StoryImage(story_id=10, filename="f.jpg", sort_order=0)
    db.add(img); db.commit()

    with flask_app.test_client() as c:
        _login(c)
        resp = c.patch(f"/api/stories/images/{img.id}",
                       json={"caption": "Mile 12"})
    assert resp.status_code == 200
    db.refresh(img)
    assert img.caption == "Mile 12"


def test_delete_removes_row_and_file(app_and_db, tmp_path):
    flask_app, db, athlete, tmp_dir = app_and_db
    # Place a real file on disk
    story_dir = tmp_path / "10"
    story_dir.mkdir()
    file_path = story_dir / "f.jpg"
    file_path.write_bytes(b"\xff\xd8\xff data")
    img = StoryImage(story_id=10, filename="f.jpg", sort_order=0)
    db.add(img); db.commit()

    with patch("running_coach_ai.web.api.stories.settings.STORY_IMAGE_DIR", str(tmp_path)):
        with flask_app.test_client() as c:
            _login(c)
            resp = c.delete(f"/api/stories/images/{img.id}")
    assert resp.status_code == 200
    assert not file_path.exists()
    assert db.query(StoryImage).count() == 0


def test_image_endpoints_404_for_other_athletes(app_and_db):
    flask_app, db, athlete, _ = app_and_db
    # Create another athlete with their own story + image
    other = Athlete(
        id=2, email="other@x.com", web_username="other", name="O",
        allowed=True, onboarding_complete=True, story_opt_in=False,
    )
    db.add(other)
    other_story = AthleteStory(
        id=20, athlete_id=2, milestone_type="race_complete", title="O · Race",
        editorial_body="x", template_key="vogue",
        share_token="zzzzzzzz1234", created_at=datetime.utcnow(),
    )
    db.add(other_story)
    other_img = StoryImage(story_id=20, filename="o.jpg")
    db.add(other_img); db.commit()

    with flask_app.test_client() as c:
        _login(c)  # logs in as athlete 1
        resp = c.patch(f"/api/stories/images/{other_img.id}", json={"caption": "x"})
    assert resp.status_code == 404
