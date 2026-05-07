"""Unit tests for editorial generation + auto-template selection (T046-T049, T059-T060)."""

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from running_coach_ai.coach.story import (
    RegenerationCooldownError,
    StoryGenerationError,
    generate_story,
    regenerate_story,
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
    """In-memory DB seeded with one athlete + one completed session with answers."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    athlete = Athlete(
        id=1, name="Sarah Lee", age=32, allowed=True,
        onboarding_complete=True, story_opt_in=True,
        story_intro_seen_at=datetime.utcnow(),
    )
    db.add(athlete)

    # One completed session with 5 answered questions
    sess = StoryInterviewSession(
        athlete_id=1, trigger_kind="race_complete",
        trigger_context_json={"goal_id": 1},
        started_at=datetime.utcnow() - timedelta(days=1),
        completed_at=datetime.utcnow(),
        skipped=False,
    )
    db.add(sess)
    db.flush()

    for i in range(1, 6):
        q = StoryQuestion(
            session_id=sess.id, athlete_id=1,
            question_index=i, question=f"Q{i}",
            options_json=["a", "b"], answer=f"answer {i}",
            is_custom_answer=False,
            asked_at=datetime.utcnow(), answered_at=datetime.utcnow(),
        )
        db.add(q)
    db.commit()
    yield db, athlete


# ---------------------------------------------------------------------------
# T046 — generate_story basic happy path
# ---------------------------------------------------------------------------


def _claude_response(body_words=500, template_key="vogue"):
    body = " ".join(["lorem"] * body_words)
    return json.dumps({"editorial_body": body, "template_key": template_key})


def test_generate_story_creates_row_and_links_sessions(db_session):
    db, athlete = db_session
    with patch("running_coach_ai.coach.story.call_claude",
               return_value=_claude_response(template_key="vogue")), \
         patch("running_coach_ai.coach.story.list_templates", return_value=[
             MagicMock(key="vogue", display_name="Vogue", voice_description="v",
                       trigger_affinities=["race_complete"])
         ]), \
         patch("running_coach_ai.coach.story.is_registered", return_value=True):
        story = generate_story(athlete, "race_complete", db)

    assert story.id is not None
    assert len(story.share_token) == 12
    assert story.template_key == "vogue"
    assert story.template_locked_by_athlete is False
    assert story.regeneration_count == 0

    # All sessions should be linked
    sessions = db.query(StoryInterviewSession).filter_by(athlete_id=1).all()
    assert all(s.story_id == story.id for s in sessions)


def test_generate_story_writes_failure_notification_after_two_bad_responses(db_session):
    db, athlete = db_session
    with patch("running_coach_ai.coach.story.call_claude", return_value="not json"), \
         patch("running_coach_ai.coach.story.list_templates", return_value=[
             MagicMock(key="vogue", display_name="V", voice_description="v",
                       trigger_affinities=["race_complete"])
         ]):
        with pytest.raises(StoryGenerationError):
            generate_story(athlete, "race_complete", db)

    from running_coach_ai.database.models import Notification
    notif = db.query(Notification).filter_by(athlete_id=1, kind="story_failed").first()
    assert notif is not None


# ---------------------------------------------------------------------------
# T047 — regeneration cooldown
# ---------------------------------------------------------------------------


def test_regeneration_cooldown_blocks_athlete_within_one_hour(db_session):
    db, athlete = db_session
    story = AthleteStory(
        athlete_id=1, milestone_type="race_complete", title="t",
        editorial_body="x", template_key="vogue",
        share_token="abcdefghijkl", regeneration_count=1,
        last_regenerated_at=datetime.utcnow() - timedelta(minutes=10),
        created_at=datetime.utcnow() - timedelta(days=1),
    )
    db.add(story)
    db.commit()

    with pytest.raises(RegenerationCooldownError) as exc:
        regenerate_story(story, db, by_admin=False)
    assert exc.value.retry_after_seconds > 0


def test_admin_regeneration_bypasses_cooldown(db_session):
    db, athlete = db_session
    story = AthleteStory(
        athlete_id=1, milestone_type="race_complete", title="t",
        editorial_body="old", template_key="vogue",
        share_token="abcdefghijkl", regeneration_count=1,
        last_regenerated_at=datetime.utcnow() - timedelta(minutes=10),
        created_at=datetime.utcnow() - timedelta(days=1),
    )
    db.add(story)
    db.commit()

    with patch("running_coach_ai.coach.story.call_claude",
               return_value=_claude_response(template_key="vogue")), \
         patch("running_coach_ai.coach.story.list_templates", return_value=[
             MagicMock(key="vogue", display_name="V", voice_description="v",
                       trigger_affinities=["race_complete"])
         ]), \
         patch("running_coach_ai.coach.story.is_registered", return_value=True):
        result = regenerate_story(story, db, by_admin=True)

    assert result.regeneration_count == 2
    assert result.editorial_body != "old"


# ---------------------------------------------------------------------------
# T048 + T059 — auto-selection happy path + fallback
# ---------------------------------------------------------------------------


def test_generate_story_uses_claude_voted_template(db_session):
    db, athlete = db_session
    with patch("running_coach_ai.coach.story.call_claude",
               return_value=_claude_response(template_key="outside")), \
         patch("running_coach_ai.coach.story.list_templates", return_value=[
             MagicMock(key="vogue", display_name="V", voice_description="v",
                       trigger_affinities=["race_complete"]),
             MagicMock(key="outside", display_name="O", voice_description="o",
                       trigger_affinities=["race_complete"]),
         ]), \
         patch("running_coach_ai.coach.story.is_registered",
               side_effect=lambda k: k in ("vogue", "outside")):
        story = generate_story(athlete, "race_complete", db)
    assert story.template_key == "outside"


def test_generate_story_falls_back_when_unknown_template_voted(db_session):
    db, athlete = db_session
    with patch("running_coach_ai.coach.story.call_claude",
               return_value=_claude_response(template_key="nonexistent")), \
         patch("running_coach_ai.coach.story.list_templates", return_value=[
             MagicMock(key="vogue", display_name="V", voice_description="v",
                       trigger_affinities=["race_complete"]),
         ]), \
         patch("running_coach_ai.coach.story.is_registered",
               side_effect=lambda k: k == "vogue"), \
         patch("running_coach_ai.coach.story.fallback_for_trigger",
               return_value="vogue"):
        story = generate_story(athlete, "race_complete", db)
    assert story.template_key == "vogue"
