"""Regression tests for process_message() coach_key handling.

Specifically guards the wrapper's finally-block contract:
- An ephemeral coach_key override (passed as parameter) MUST be restored
  on exit so it doesn't leak into the DB via session commit.
- A persistent coach switch made *inside* the message turn (e.g. via
  extract_coach_switch parsing a <coach_switch> tag from Claude's reply)
  MUST survive the finally block — otherwise the next session.commit()
  silently undoes it.

The original bug: the finally block unconditionally restored
athlete.coach_key, so any persistent switch from extract_coach_switch
was reverted in memory and then written back to the DB by the outer
session's commit-on-exit. See conversation.py:1565-1577.
"""

from unittest.mock import MagicMock, patch

from running_coach_ai.database.models import Athlete
from running_coach_ai.coach.conversation import process_message


def _athlete(coach_key="classic"):
    a = MagicMock(spec=Athlete)
    a.id = 1
    a.coach_key = coach_key
    return a


# ── Case A ───────────────────────────────────────────────────────────────────
@patch("running_coach_ai.coach.conversation._process_message_inner")
def test_no_override_no_mutation_leaves_coach_key_unchanged(mock_inner):
    """Plain message, no override, no <coach_switch> tag → coach_key unchanged."""
    a = _athlete("classic")
    db = MagicMock()
    mock_inner.return_value = "Just a normal reply."

    result = process_message(a, "How am I doing?", db)

    assert result == "Just a normal reply."
    assert a.coach_key == "classic"


# ── Case B (regression for the bug we just fixed) ────────────────────────────
@patch("running_coach_ai.coach.conversation._process_message_inner")
def test_persistent_coach_switch_in_response_survives_finally(mock_inner):
    """When <coach_switch> in Claude's response writes a new coach_key during
    the turn, the wrapper's finally block must NOT revert it. Regression for
    the silent-undo bug.
    """
    a = _athlete("classic")
    db = MagicMock()

    def inner(athlete, text, db_session, source="web"):
        # Simulates extract_coach_switch's mutation during message processing.
        athlete.coach_key = "maya"
        return "Switched! Hi from Coach Maya."

    mock_inner.side_effect = inner
    result = process_message(a, "switch to maya", db)

    assert "Switched" in result
    assert a.coach_key == "maya", (
        "process_message must preserve a coach_key mutation made by "
        "extract_coach_switch — finally block was reverting it (the bug)."
    )


# ── Case C ───────────────────────────────────────────────────────────────────
@patch("running_coach_ai.coach.conversation._process_message_inner")
def test_ephemeral_override_is_applied_during_call_and_restored_after(mock_inner):
    """Passing coach_key override applies during the call, restores after."""
    a = _athlete("classic")
    db = MagicMock()
    captured = {}

    def inner(athlete, text, db_session, source="web"):
        captured["coach_key_during_call"] = athlete.coach_key
        return "Reply with overridden persona."

    mock_inner.side_effect = inner
    process_message(a, "msg", db, coach_key="jordan")

    assert captured["coach_key_during_call"] == "jordan", (
        "override must be applied to athlete.coach_key during the inner call"
    )
    assert a.coach_key == "classic", (
        "override must be reverted to original after the call returns"
    )


# ── Case D ───────────────────────────────────────────────────────────────────
@patch("running_coach_ai.coach.conversation._process_message_inner")
def test_override_plus_in_call_mutation_override_wins(mock_inner):
    """When both an ephemeral override is passed AND the inner call mutates
    coach_key (e.g. via <coach_switch>), the wrapper restores the pre-override
    value — override wins, the in-call mutation is dropped.

    This is the current contract; it locks the behavior so a future change
    surfaces here for explicit review.
    """
    a = _athlete("classic")
    db = MagicMock()

    def inner(athlete, text, db_session, source="web"):
        athlete.coach_key = "maya"  # simulated <coach_switch>
        return "ok"

    mock_inner.side_effect = inner
    process_message(a, "msg", db, coach_key="jordan")

    assert a.coach_key == "classic", (
        "with override active, finally must restore original even if "
        "inner mutated coach_key"
    )


# ── Case E ───────────────────────────────────────────────────────────────────
@patch("running_coach_ai.coach.conversation._process_message_inner")
def test_exception_in_inner_still_restores_override(mock_inner):
    """If the inner call raises, the finally block must still restore the
    override so the caller's athlete object isn't left mutated."""
    import pytest

    a = _athlete("classic")
    db = MagicMock()
    mock_inner.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        process_message(a, "msg", db, coach_key="jordan")

    assert a.coach_key == "classic"
