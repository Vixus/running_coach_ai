"""Unit tests for extract_coach_switch() in conversation.py (T014)."""

from unittest.mock import MagicMock


from running_coach_ai.database.models import Athlete
from running_coach_ai.coach.conversation import extract_coach_switch


def _make_athlete(coach_key="classic"):
    athlete = MagicMock(spec=Athlete)
    athlete.id = 1
    athlete.coach_key = coach_key
    return athlete


def test_valid_switch_updates_athlete_and_strips_tag():
    athlete = _make_athlete("classic")
    db = MagicMock()
    response = "Great choice! <coach_switch>maya</coach_switch>You're now working with Coach Maya."

    result = extract_coach_switch(athlete, response, db)

    assert athlete.coach_key == "maya"
    db.commit.assert_called_once()
    assert "<coach_switch>" not in result
    assert "You're now working with Coach Maya." in result


def test_invalid_key_leaves_athlete_unchanged_and_strips_tag():
    athlete = _make_athlete("classic")
    db = MagicMock()
    response = "Something went wrong <coach_switch>bogus_key</coach_switch> but here we are."

    result = extract_coach_switch(athlete, response, db)

    assert athlete.coach_key == "classic"
    db.commit.assert_not_called()
    assert "<coach_switch>" not in result


def test_no_tag_returns_response_unchanged():
    athlete = _make_athlete("classic")
    db = MagicMock()
    response = "Just a normal coaching message with no switch tag."

    result = extract_coach_switch(athlete, response, db)

    assert result == response
    assert athlete.coach_key == "classic"
    db.commit.assert_not_called()


def test_switch_to_same_coach_no_commit():
    """US2 Acceptance Scenario 4: selecting the current coach must not trigger a DB write."""
    athlete = _make_athlete("classic")
    db = MagicMock()
    response = "Staying with <coach_switch>classic</coach_switch>Coach Alex it is."

    result = extract_coach_switch(athlete, response, db)

    assert athlete.coach_key == "classic"
    db.commit.assert_not_called()
    assert "<coach_switch>" not in result


def test_switch_only_mutates_coach_key_not_other_attributes():
    """FR-008: the scope of mutation is coach_key only — no other Athlete attributes are changed."""
    athlete = _make_athlete("classic")
    athlete.name = "Sam"
    athlete.timezone = "America/New_York"
    db = MagicMock()
    response = "<coach_switch>maya</coach_switch>Now working with Coach Maya."

    extract_coach_switch(athlete, response, db)

    assert athlete.coach_key == "maya"
    assert athlete.name == "Sam"
    assert athlete.id == 1


def test_jordan_switch_is_valid():
    athlete = _make_athlete("classic")
    db = MagicMock()
    response = "<coach_switch>jordan</coach_switch>Let's get after it."

    result = extract_coach_switch(athlete, response, db)

    assert athlete.coach_key == "jordan"
    db.commit.assert_called_once()
    assert "<coach_switch>" not in result


def test_tag_with_whitespace_is_stripped_and_matched():
    athlete = _make_athlete("classic")
    db = MagicMock()
    response = "Switching now. <coach_switch>  maya  </coach_switch>Done."

    result = extract_coach_switch(athlete, response, db)

    assert athlete.coach_key == "maya"
    db.commit.assert_called_once()
    assert "<coach_switch>" not in result
