"""Unit tests for GET /api/review."""

import json
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest


def _flask_app():
    from flask import Flask
    from running_coach_ai.web.api.review import bp
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["TESTING"] = True
    app.register_blueprint(bp)
    return app


def _make_athlete():
    a = MagicMock()
    a.id = 1
    a.name = "Sarah"
    return a


def _make_review(week_start=None):
    r = MagicMock()
    r.week_start_date = week_start or date(2026, 4, 14)
    r.total_miles = 30.0
    r.elevation_gain_ft = 4210.0
    r.avg_hrv = 69.5
    r.total_tss = 284.0
    r.narrative = "Strong week, Sarah. Tuesday's tempo was a breakthrough."
    r.daily_volume_json = [10, 12, 8, 0, 0, 0, 0]
    r.body_battery_json = [72, 68, 74, 71, 76, 73, 79, 78]
    r.next_week_json = [{"day": "Mon", "type": "rest", "label": "Rest"}]
    r.created_at = datetime(2026, 4, 20, 20, 0, 0)
    return r


def _wire_review_db(athlete, review, total=1):
    """Mock the two-call chain used by /api/review:
       db.query(...).filter(...).count() → int
       db.query(...).filter(...).order_by(...).offset(0).first() → review
    """
    db = MagicMock()
    db.get.return_value = athlete
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.offset.return_value = chain
    chain.count.return_value = total
    chain.first.return_value = review
    db.query.return_value = chain
    return db


# ---------------------------------------------------------------------------
# GET /api/review — success
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.review.get_session")
def test_review_returns_correct_shape(mock_gs):
    app = _flask_app()
    athlete = _make_athlete()
    review = _make_review()

    db = _wire_review_db(athlete, review)

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/review")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["week_start"] == "2026-04-14"
        assert "metrics" in data
        assert data["metrics"]["total_miles"] == 30.0
        assert data["metrics"]["elevation_ft"] == 4210.0
        assert data["metrics"]["avg_hrv"] == 69.5
        assert data["metrics"]["total_tss"] == 284.0
        assert data["narrative"] == "Strong week, Sarah. Tuesday's tempo was a breakthrough."
        assert data["daily_volume"] == [10, 12, 8, 0, 0, 0, 0]
        assert data["body_battery_8w"] == [72, 68, 74, 71, 76, 73, 79, 78]
        assert isinstance(data["next_week"], list)
        assert data["next_week"][0]["day"] == "Mon"


@patch("running_coach_ai.web.api.review.get_session")
def test_review_metrics_keys_present(mock_gs):
    app = _flask_app()
    athlete = _make_athlete()
    review = _make_review()

    db = _wire_review_db(athlete, review)

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/review")
        data = resp.get_json()
        metrics = data["metrics"]
        for key in ("total_miles", "elevation_ft", "avg_hrv", "total_tss"):
            assert key in metrics, f"Missing metric key: {key}"


# ---------------------------------------------------------------------------
# GET /api/review — 404 when no review exists
# ---------------------------------------------------------------------------

@patch("running_coach_ai.web.api.review.get_session")
def test_review_404_when_no_row(mock_gs):
    app = _flask_app()
    athlete = _make_athlete()

    db = _wire_review_db(athlete, review=None, total=0)

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_gs.return_value = ctx

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["athlete_id"] = 1
        resp = client.get("/api/review")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data
        assert "Sunday" in data["error"]


def test_review_requires_auth():
    app = _flask_app()
    with app.test_client() as client:
        resp = client.get("/api/review")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Upsert logic — same week_start_date updates existing row
# ---------------------------------------------------------------------------

def test_weekly_review_upsert_updates_existing_row():
    """SQLite insert().on_conflict_do_update() replaces an existing row for the same week."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.dialects.sqlite import insert
    from running_coach_ai.database.models import Base, WeeklyReviewSummary

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    week = date(2026, 4, 14)

    # Insert first row
    stmt = insert(WeeklyReviewSummary).values(
        athlete_id=1,
        week_start_date=week,
        narrative="First narrative",
        total_miles=20.0,
        elevation_gain_ft=1000.0,
        avg_hrv=65.0,
        total_tss=200.0,
    ).on_conflict_do_update(
        index_elements=["athlete_id", "week_start_date"],
        set_={"narrative": "First narrative", "total_miles": 20.0},
    )
    db.execute(stmt)
    db.commit()

    # Upsert same week with updated values
    stmt2 = insert(WeeklyReviewSummary).values(
        athlete_id=1,
        week_start_date=week,
        narrative="Updated narrative",
        total_miles=30.0,
        elevation_gain_ft=1000.0,
        avg_hrv=65.0,
        total_tss=200.0,
    ).on_conflict_do_update(
        index_elements=["athlete_id", "week_start_date"],
        set_={"narrative": "Updated narrative", "total_miles": 30.0},
    )
    db.execute(stmt2)
    db.commit()

    rows = db.query(WeeklyReviewSummary).filter(WeeklyReviewSummary.athlete_id == 1).all()
    assert len(rows) == 1, "Upsert should not create a duplicate row"
    assert rows[0].narrative == "Updated narrative"
    assert rows[0].total_miles == 30.0

    db.close()
    engine.dispose()
