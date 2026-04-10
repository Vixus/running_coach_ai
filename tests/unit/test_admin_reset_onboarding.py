"""Tests for US3: !admin reset-onboarding command.

Covers:
  T015-1: valid uid → ConversationMessage rows deleted, pending data cleared,
           onboarding_complete=False, onboarding_step=0, success message returned
  T015-2: unknown uid → error message returned
  T015-3: uid absent or empty → error message returned (uid is required)
"""

from unittest.mock import MagicMock, call, patch

from running_coach_ai.slack.admin import handle_admin_command


ADMIN_ID = "UADMIN"
_ADMIN_MOD = "running_coach_ai.slack.admin"


def _make_athlete(*, slack_user_id="U123", onboarding_complete=True):
    a = MagicMock()
    a.id = 1
    a.slack_user_id = slack_user_id
    a.onboarding_complete = onboarding_complete
    a.onboarding_step = 7
    a.pending_onboarding_data = {"name": "Alice"}
    a.pending_onboarding_data_created_at = MagicMock()
    return a


def _db_with_athlete(athlete):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = athlete
    return db


def _db_no_athlete():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAdminResetOnboarding:
    """Tests for !admin reset-onboarding <uid>."""

    def _call(self, text, db):
        with patch(f"{_ADMIN_MOD}.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = ADMIN_ID
            return handle_admin_command(ADMIN_ID, text, db)

    def test_valid_uid_resets_athlete_state(self):
        """T015-1: Valid uid → pending data cleared, onboarding reset, success DM."""
        athlete = _make_athlete(slack_user_id="U123", onboarding_complete=True)
        db = _db_with_athlete(athlete)

        # Make the delete query return something inspectable
        delete_query = MagicMock()
        delete_query.filter.return_value.delete.return_value = 3
        # Route query: first call (ConversationMessage delete) and second call (Athlete lookup)
        # We need to support both ConversationMessage delete and Athlete query
        def _query_router(model):
            from running_coach_ai.database.models import ConversationMessage, Athlete
            if model is ConversationMessage:
                return delete_query
            else:
                q = MagicMock()
                q.filter.return_value.first.return_value = athlete
                return q

        db.query.side_effect = _query_router

        result = self._call("!admin reset-onboarding U123", db)

        # Athlete state reset
        assert athlete.pending_onboarding_data is None
        assert athlete.pending_onboarding_data_created_at is None
        assert athlete.onboarding_complete is False
        assert athlete.onboarding_step == 0

        # ConversationMessage rows deleted
        delete_query.filter.return_value.delete.assert_called_once()

        # DB committed
        db.commit.assert_called()

        # Success message contains the user mention
        assert result is not None
        assert "U123" in result or "reset" in result.lower()

    def test_unknown_uid_returns_error(self):
        """T015-2: Unknown uid → error message returned."""
        db = _db_no_athlete()

        result = self._call("!admin reset-onboarding UUNKNOWN", db)

        assert result is not None
        assert any(word in result.lower() for word in ("not found", "no athlete", "unknown"))

    def test_uid_absent_returns_usage_error(self):
        """T015-3a: Bare '!admin reset-onboarding' with no uid → usage error, no reset."""
        db = MagicMock()

        result = self._call("!admin reset-onboarding", db)

        assert result is not None
        assert any(word in result.lower() for word in ("usage", "required", "uid", "user_id"))
        # DB must not have been touched
        db.commit.assert_not_called()

    def test_empty_uid_returns_usage_error(self):
        """T015-3b: '!admin reset-onboarding  ' (spaces only) → usage error."""
        db = MagicMock()

        result = self._call("!admin reset-onboarding   ", db)

        assert result is not None
        assert any(word in result.lower() for word in ("usage", "required", "uid", "user_id"))
        db.commit.assert_not_called()

    def test_mention_format_uid_accepted(self):
        """T015-extra: Mention-format uid '<@U123>' is parsed correctly."""
        athlete = _make_athlete(slack_user_id="U123")

        def _query_router(model):
            from running_coach_ai.database.models import ConversationMessage, Athlete
            if model is ConversationMessage:
                q = MagicMock()
                q.filter.return_value.delete.return_value = 0
                return q
            else:
                q = MagicMock()
                q.filter.return_value.first.return_value = athlete
                return q

        db = MagicMock()
        db.query.side_effect = _query_router

        result = self._call("!admin reset-onboarding <@U123>", db)

        # Should succeed (not return a usage error)
        assert result is not None
        assert not any(word in result.lower() for word in ("usage", "required"))
        assert athlete.onboarding_complete is False
