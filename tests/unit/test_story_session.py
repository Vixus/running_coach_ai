"""Unit tests for fire_trigger_if_eligible, next_question, capture_chat_answer (T024-T026)."""

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from running_coach_ai.coach import story
from running_coach_ai.coach.story import (
    capture_chat_answer,
    close_session,
    fire_trigger_if_eligible,
    get_pending_question,
    next_question,
    record_option_answer,
)
from running_coach_ai.database.models import (
    Athlete,
    AthleteStory,
    Base,
    StoryInterviewSession,
    StoryQuestion,
)


@pytest.fixture
def db_session():
    """In-memory SQLite with all tables, plus one athlete row."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    athlete = Athlete(
        id=1, name="Sarah", age=32, allowed=True,
        onboarding_complete=True, story_opt_in=True,
        story_intro_seen_at=datetime.utcnow(),
    )
    db.add(athlete)
    db.commit()
    yield db, athlete
    db.close()


# ---------------------------------------------------------------------------
# T024 — fire_trigger_if_eligible eligibility gates
# ---------------------------------------------------------------------------


def test_blocks_when_opt_in_false(db_session):
    db, athlete = db_session
    athlete.story_opt_in = False
    db.commit()

    result = fire_trigger_if_eligible(athlete, "pr_set", {}, db)
    assert result is None
    assert db.query(StoryInterviewSession).count() == 0


def test_blocks_when_intro_not_seen(db_session):
    db, athlete = db_session
    athlete.story_intro_seen_at = None
    db.commit()

    result = fire_trigger_if_eligible(athlete, "pr_set", {}, db)
    assert result is None


def test_bypass_intro_for_admin(db_session):
    db, athlete = db_session
    athlete.story_intro_seen_at = None
    db.commit()

    result = fire_trigger_if_eligible(athlete, "race_complete", {}, db, bypass_intro=True)
    assert result is not None
    assert result.trigger_kind == "race_complete"


def test_blocks_when_active_session_exists(db_session):
    db, athlete = db_session
    db.add(StoryInterviewSession(
        athlete_id=1, trigger_kind="pr_set",
        trigger_context_json={}, started_at=datetime.utcnow(),
    ))
    db.commit()

    result = fire_trigger_if_eligible(athlete, "race_complete", {}, db)
    assert result is None


def test_blocks_on_priority_collision(db_session):
    db, athlete = db_session
    # Higher-priority race_complete fired 10 minutes ago
    db.add(StoryInterviewSession(
        athlete_id=1, trigger_kind="race_complete",
        trigger_context_json={},
        started_at=datetime.utcnow() - timedelta(minutes=10),
        completed_at=datetime.utcnow() - timedelta(minutes=5),
        skipped=False,
    ))
    db.commit()

    # Lower-priority pr_set should be suppressed for 24h
    result = fire_trigger_if_eligible(athlete, "pr_set", {}, db)
    assert result is None


def test_success_path_creates_session_and_consent_question(db_session):
    db, athlete = db_session
    result = fire_trigger_if_eligible(athlete, "pr_set", {"completed_workout_id": 42}, db)
    assert result is not None
    assert result.trigger_kind == "pr_set"
    assert result.completed_at is None

    # Consent prompt is question_index=1
    questions = db.query(StoryQuestion).filter(
        StoryQuestion.session_id == result.id
    ).all()
    assert len(questions) == 1
    assert questions[0].question_index == 1
    assert "ask a few questions" in questions[0].question.lower()
    options = questions[0].options_json
    assert "Skip" in options


def test_unknown_trigger_kind_returns_none(db_session):
    db, athlete = db_session
    assert fire_trigger_if_eligible(athlete, "nonsense", {}, db) is None


# ---------------------------------------------------------------------------
# T025 — next_question
# ---------------------------------------------------------------------------


def test_next_question_creates_row_on_valid_claude_response(db_session):
    db, athlete = db_session
    session = fire_trigger_if_eligible(athlete, "pr_set", {}, db)

    # Answer the consent prompt so transcript has content
    consent = db.query(StoryQuestion).filter_by(session_id=session.id).first()
    consent.answer = "Yes, go ahead"
    consent.is_custom_answer = False
    consent.answered_at = datetime.utcnow()
    db.commit()

    fake_response = json.dumps({
        "question": "What did the last mile feel like?",
        "options": ["Painful", "Smooth", "Surprising"],
        "session_complete": False,
    })
    with patch("running_coach_ai.coach.story.call_claude", return_value=fake_response):
        new_q = next_question(session, db)

    assert new_q is not None
    assert new_q.question_index == 2
    assert new_q.question == "What did the last mile feel like?"
    assert len(new_q.options_json) == 3


def test_next_question_closes_when_session_complete_after_5(db_session):
    db, athlete = db_session
    session = fire_trigger_if_eligible(athlete, "pr_set", {}, db)
    # Pre-populate 5 answered questions
    for i in range(2, 7):
        db.add(StoryQuestion(
            session_id=session.id, athlete_id=1,
            question_index=i, question=f"Q{i}", options_json=["a", "b"],
            answer="x", is_custom_answer=False,
            asked_at=datetime.utcnow(), answered_at=datetime.utcnow(),
        ))
    consent = db.query(StoryQuestion).filter_by(session_id=session.id, question_index=1).first()
    consent.answered_at = datetime.utcnow()
    consent.answer = "Yes, go ahead"
    db.commit()

    fake_response = json.dumps({
        "question": "(unused)",
        "options": ["a", "b"],
        "session_complete": True,
    })
    with patch("running_coach_ai.coach.story.call_claude", return_value=fake_response):
        new_q = next_question(session, db)

    assert new_q is None
    db.refresh(session)
    assert session.completed_at is not None
    assert session.skipped is False


def test_next_question_aborts_after_two_malformed_responses(db_session):
    db, athlete = db_session
    session = fire_trigger_if_eligible(athlete, "pr_set", {}, db)

    with patch("running_coach_ai.coach.story.call_claude", return_value="not json"):
        new_q = next_question(session, db)

    assert new_q is None
    db.refresh(session)
    assert session.completed_at is not None


# ---------------------------------------------------------------------------
# T026 — capture_chat_answer
# ---------------------------------------------------------------------------


def test_capture_records_free_text_answer(db_session):
    db, athlete = db_session
    session = fire_trigger_if_eligible(athlete, "pr_set", {}, db)

    fake_response = json.dumps({
        "question": "Next question?",
        "options": ["a", "b"],
        "session_complete": False,
    })
    with patch("running_coach_ai.coach.story.call_claude", return_value=fake_response):
        answered = capture_chat_answer(athlete, "I felt great honestly", db)

    assert answered is not None
    assert answered.question_index == 1
    assert answered.answer == "I felt great honestly"
    assert answered.is_custom_answer is True
    assert answered.answered_at is not None


def test_capture_returns_none_when_no_pending_question(db_session):
    db, athlete = db_session
    answered = capture_chat_answer(athlete, "anything", db)
    assert answered is None


def test_capture_consent_decline_closes_session(db_session):
    db, athlete = db_session
    session = fire_trigger_if_eligible(athlete, "pr_set", {}, db)

    answered = capture_chat_answer(athlete, "skip", db)
    assert answered is not None
    db.refresh(session)
    assert session.completed_at is not None
    assert session.skipped is True


def test_record_option_answer_marks_non_custom(db_session):
    db, athlete = db_session
    session = fire_trigger_if_eligible(athlete, "pr_set", {}, db)
    consent = db.query(StoryQuestion).filter_by(session_id=session.id).first()

    record_option_answer(consent, "Yes, go ahead", db)
    db.refresh(consent)
    assert consent.answer == "Yes, go ahead"
    assert consent.is_custom_answer is False
