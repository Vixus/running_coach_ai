"""Unit tests for web dashboard models: RunFeedback, WeeklyReviewSummary, WebEvent, Athlete web fields."""

import pytest
from unittest.mock import MagicMock
from datetime import date, datetime


# ---------------------------------------------------------------------------
# Athlete web credential fields
# ---------------------------------------------------------------------------

def test_athlete_has_web_credential_fields():
    from running_coach_ai.database.models import Athlete
    a = Athlete()
    a.web_username = "sarah"
    a.web_password_hash = "pbkdf2:sha256:..."
    a.is_admin = False
    assert a.web_username == "sarah"
    assert a.is_admin is False


def test_athlete_is_admin_default_false():
    from running_coach_ai.database.models import Athlete
    a = Athlete()
    # SQLAlchemy Column default is applied on INSERT, not object instantiation;
    # the attribute is None until persisted. Check the column definition instead.
    col = Athlete.__table__.columns["is_admin"]
    assert col.default.arg is False


# ---------------------------------------------------------------------------
# RunFeedback validation rules
# ---------------------------------------------------------------------------

def test_run_feedback_feel_score_valid_range():
    from running_coach_ai.database.models import RunFeedback
    fb = RunFeedback(feel_score=0, rpe=1, notes="")
    assert fb.feel_score == 0
    fb2 = RunFeedback(feel_score=4, rpe=10, notes="great run")
    assert fb2.feel_score == 4


def test_run_feedback_can_have_null_fields():
    from running_coach_ai.database.models import RunFeedback
    fb = RunFeedback()
    assert fb.feel_score is None
    assert fb.rpe is None
    assert fb.notes is None


def test_run_feedback_columns_exist():
    from running_coach_ai.database.models import RunFeedback
    cols = {c.name for c in RunFeedback.__table__.columns}
    assert "feel_score" in cols
    assert "rpe" in cols
    assert "notes" in cols
    assert "athlete_id" in cols
    assert "completed_workout_id" in cols


# ---------------------------------------------------------------------------
# WeeklyReviewSummary unique constraint
# ---------------------------------------------------------------------------

def test_weekly_review_summary_has_unique_constraint():
    from running_coach_ai.database.models import WeeklyReviewSummary
    constraints = [
        c.name for c in WeeklyReviewSummary.__table__.constraints
    ]
    assert "uq_weekly_review_athlete_week" in constraints


def test_weekly_review_summary_columns_exist():
    from running_coach_ai.database.models import WeeklyReviewSummary
    cols = {c.name for c in WeeklyReviewSummary.__table__.columns}
    assert "total_miles" in cols
    assert "avg_hrv" in cols
    assert "daily_volume_json" in cols
    assert "body_battery_json" in cols
    assert "next_week_json" in cols


def test_weekly_review_summary_narrative_not_nullable():
    from running_coach_ai.database.models import WeeklyReviewSummary
    col = WeeklyReviewSummary.__table__.columns["narrative"]
    assert col.nullable is False


# ---------------------------------------------------------------------------
# WebEvent model
# ---------------------------------------------------------------------------

def test_web_event_columns_exist():
    from running_coach_ai.database.models import WebEvent
    cols = {c.name for c in WebEvent.__table__.columns}
    assert "severity" in cols
    assert "category" in cols
    assert "message" in cols
    assert "details_json" in cols
    assert "athlete_id" in cols
    assert "timestamp" in cols


def test_web_event_athlete_id_nullable():
    from running_coach_ai.database.models import WebEvent
    col = WebEvent.__table__.columns["athlete_id"]
    assert col.nullable is True


def test_web_event_severity_not_nullable():
    from running_coach_ai.database.models import WebEvent
    col = WebEvent.__table__.columns["severity"]
    assert col.nullable is False
