"""Unit tests for GET/POST /api/coach (magazine coach switcher)."""

from unittest.mock import MagicMock, patch

from running_coach_ai.web.api import magazine as magazine_mod


def _flask_app():
    from flask import Flask
    from running_coach_ai.web.api.magazine import bp
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["TESTING"] = True
    app.register_blueprint(bp)
    return app


def _make_athlete(coach_key="classic"):
    a = MagicMock()
    a.id = 1
    a.coach_key = coach_key
    return a


def _make_session_ctx(athlete):
    db = MagicMock()
    db.get.return_value = athlete
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx, db


# ─── GET /api/coach ──────────────────────────────────────────────────────────

def test_get_coach_unauthed_returns_401():
    app = _flask_app()
    with app.test_client() as client:
        resp = client.get("/api/coach")
        assert resp.status_code == 401


@patch("running_coach_ai.web.api.magazine.get_session")
def test_get_coach_returns_options_and_current(mock_gs):
    app = _flask_app()
    athlete = _make_athlete("maya")
    ctx, db = _make_session_ctx(athlete)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/coach")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["current"] == "maya"
        keys = {o["key"] for o in data["options"]}
        assert keys == {"classic", "maya", "jordan"}
        # Each option has name + description
        for o in data["options"]:
            assert o["name"]
            assert o["description"]


@patch("running_coach_ai.web.api.magazine.get_session")
def test_get_coach_falls_back_to_classic_when_coach_key_null(mock_gs):
    app = _flask_app()
    athlete = _make_athlete(None)
    ctx, _ = _make_session_ctx(athlete)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/coach")
        assert resp.status_code == 200
        assert resp.get_json()["current"] == "classic"


# ─── POST /api/coach ─────────────────────────────────────────────────────────

@patch("running_coach_ai.web.api.magazine.get_session")
def test_post_coach_valid_key_writes_and_commits(mock_gs):
    app = _flask_app()
    athlete = _make_athlete("classic")
    ctx, db = _make_session_ctx(athlete)
    mock_gs.return_value = ctx
    # Seed quote cache so we can verify it gets invalidated
    magazine_mod._quote_cache[1] = {"date": None, "last_completed_id": None, "quotes": [{"q": "stale"}]}

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/coach", json={"coach_key": "maya"})
        assert resp.status_code == 200
        assert resp.get_json()["current"] == "maya"

    assert athlete.coach_key == "maya"
    db.commit.assert_called_once()
    # Cache must be evicted so the next /api/magazine load regenerates with the new persona
    assert 1 not in magazine_mod._quote_cache


@patch("running_coach_ai.web.api.magazine.get_session")
def test_post_coach_legacy_alias_resolves(mock_gs):
    app = _flask_app()
    athlete = _make_athlete("classic")
    ctx, db = _make_session_ctx(athlete)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/coach", json={"coach_key": "sofia"})  # legacy → maya
        assert resp.status_code == 200
        assert resp.get_json()["current"] == "maya"

    assert athlete.coach_key == "maya"
    db.commit.assert_called_once()


@patch("running_coach_ai.web.api.magazine.get_session")
def test_post_coach_invalid_key_returns_400_and_no_write(mock_gs):
    app = _flask_app()
    athlete = _make_athlete("classic")
    ctx, db = _make_session_ctx(athlete)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/coach", json={"coach_key": "bogus"})
        assert resp.status_code == 400

    assert athlete.coach_key == "classic"
    db.commit.assert_not_called()


@patch("running_coach_ai.web.api.magazine.get_session")
def test_post_coach_same_key_no_commit(mock_gs):
    """Selecting the current coach must not trigger a DB write or cache evict."""
    app = _flask_app()
    athlete = _make_athlete("classic")
    ctx, db = _make_session_ctx(athlete)
    mock_gs.return_value = ctx
    magazine_mod._quote_cache[1] = {"date": None, "last_completed_id": None, "quotes": [{"q": "keep"}]}

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/coach", json={"coach_key": "classic"})
        assert resp.status_code == 200

    assert athlete.coach_key == "classic"
    db.commit.assert_not_called()
    # Cache survives — no persona change, no need to regenerate
    assert 1 in magazine_mod._quote_cache
    # cleanup
    magazine_mod._quote_cache.pop(1, None)


def test_post_coach_unauthed_returns_401():
    app = _flask_app()
    with app.test_client() as client:
        resp = client.post("/api/coach", json={"coach_key": "maya"})
        assert resp.status_code == 401


@patch("running_coach_ai.web.api.magazine.get_session")
def test_post_coach_missing_body_returns_400(mock_gs):
    app = _flask_app()
    athlete = _make_athlete("classic")
    ctx, _ = _make_session_ctx(athlete)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.post("/api/coach", json={})
        assert resp.status_code == 400
