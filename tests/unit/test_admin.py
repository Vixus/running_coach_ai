"""Comprehensive tests for all !admin command functionality.

Covers gaps not addressed by existing test files:
  - Access control (non-admin silently ignored)
  - !admin add  (new athlete, already allowed, re-enable revoked)
  - !admin remove  (success, not found, already removed)
  - !admin list  (empty, various statuses)
  - Unknown command  (returns help text)
  - Command parsing  (mention format, target user extraction, case insensitivity)
  - _resync_garmin  error paths (no athlete, no credentials, auth failure, errored weeks)
  - _clean_garmin  error paths (no athlete, no credentials, auth failure, delete failures)
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


from running_coach_ai.slack.admin import (
    _add_athlete,
    _clean_garmin,
    _list_athletes,
    _remove_athlete,
    _resync_garmin,
    handle_admin_command,
)

TODAY = date.today()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_athlete(
    slack_user_id="U123",
    athlete_id=1,
    name="Alice",
    allowed=True,
    onboarding_complete=True,
    garmin_email="alice@example.com",
    garmin_password_encrypted=b"enc",
):
    a = MagicMock()
    a.id = athlete_id
    a.slack_user_id = slack_user_id
    a.name = name
    a.allowed = allowed
    a.onboarding_complete = onboarding_complete
    a.garmin_email = garmin_email
    a.garmin_password_encrypted = garmin_password_encrypted
    a.created_at = TODAY
    return a


def _db_returning(athlete_or_none):
    """Minimal DB mock whose .filter().first() returns the given value."""
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value.first.return_value = athlete_or_none
    q.order_by.return_value.all.return_value = (
        [] if athlete_or_none is None else [athlete_or_none]
    )
    db.query.return_value = q
    return db


def _db_for_garmin(athlete, upcoming_dates):
    """DB mock wired for _resync_garmin / _clean_garmin."""
    from running_coach_ai.database.models import Athlete

    db = MagicMock()
    athlete_q = MagicMock()
    athlete_q.filter.return_value.first.return_value = athlete

    date_rows = [MagicMock(scheduled_date=d) for d in upcoming_dates]
    workout_q = MagicMock()
    workout_q.filter.return_value.distinct.return_value.all.return_value = date_rows
    workout_q.filter.return_value.count.return_value = len(upcoming_dates)
    workout_q.filter.return_value.update.return_value = len(upcoming_dates)

    db.query.side_effect = lambda m: athlete_q if m is Athlete else workout_q
    db.commit.return_value = None
    return db


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

class TestAccessControl:

    def test_non_admin_sender_returns_none(self):
        """Any sender that is not ADMIN_SLACK_USER_ID must be silently ignored."""
        db = MagicMock()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            result = handle_admin_command("NOT_ADMIN", "!admin list", db)
        assert result is None

    def test_admin_sender_is_processed(self):
        """The configured admin user ID must be allowed to run commands."""
        db = MagicMock()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings, \
             patch("running_coach_ai.slack.admin._list_athletes", return_value="list") as mock_list:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            result = handle_admin_command("ADMIN1", "!admin list", db)
        mock_list.assert_called_once()
        assert result == "list"

    def test_non_admin_ignored_even_for_destructive_commands(self):
        """Non-admin must be blocked even for dangerous commands like clean-garmin."""
        db = MagicMock()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            result = handle_admin_command("HACKER", "!admin clean-garmin --confirm", db)
        assert result is None


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------

class TestUnknownCommand:

    def test_unknown_command_returns_help_text(self):
        """An unrecognised !admin command must return a helpful message, not crash."""
        db = MagicMock()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            result = handle_admin_command("ADMIN1", "!admin frobnicate", db)
        assert result is not None
        assert "add" in result.lower() or "unknown" in result.lower()

    def test_unknown_command_lists_valid_commands(self):
        """Help text must mention the valid commands."""
        db = MagicMock()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            result = handle_admin_command("ADMIN1", "!admin ???", db)
        assert "resync-garmin" in result
        assert "list" in result


# ---------------------------------------------------------------------------
# !admin add
# ---------------------------------------------------------------------------

class TestAddAthlete:

    def test_add_new_athlete_creates_row(self):
        """Adding a brand-new user must create an Athlete row and return confirmation."""
        db = _db_returning(None)  # no existing athlete
        result = _add_athlete("UNEW", db)
        db.add.assert_called_once()
        db.commit.assert_called_once()
        assert "UNEW" in result or "added" in result.lower()

    def test_add_already_allowed_returns_already_message(self):
        """Adding an athlete who is already on the allowed list must not duplicate them."""
        existing = _make_athlete(slack_user_id="U123", allowed=True)
        db = _db_returning(existing)
        result = _add_athlete("U123", db)
        db.add.assert_not_called()
        assert "already" in result.lower()

    def test_add_revoked_athlete_re_enables(self):
        """Adding an athlete whose allowed=False must set allowed=True, not create a new row."""
        revoked = _make_athlete(slack_user_id="U123", allowed=False, onboarding_complete=True)
        db = _db_returning(revoked)
        result = _add_athlete("U123", db)
        assert revoked.allowed is True
        db.commit.assert_called_once()
        db.add.assert_not_called()
        assert "re-enabled" in result.lower() or "restored" in result.lower() or "U123" in result

    def test_re_enabling_completed_onboarding_notes_data_intact(self):
        """Re-enabling an athlete who completed onboarding must note they won't re-onboard."""
        revoked = _make_athlete(allowed=False, onboarding_complete=True)
        db = _db_returning(revoked)
        result = _add_athlete("U123", db)
        assert "onboard" in result.lower() or "data" in result.lower() or "intact" in result.lower()

    def test_handle_admin_command_routes_add(self):
        """`!admin add <uid>` must call _add_athlete."""
        db = MagicMock()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings, \
             patch("running_coach_ai.slack.admin._add_athlete", return_value="added") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin add U456", db)
        mock_fn.assert_called_once()
        assert mock_fn.call_args[0][0] == "U456"

    def test_handle_admin_command_add_with_mention_format(self):
        """`!admin add <@U456>` must extract bare user ID U456."""
        db = MagicMock()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings, \
             patch("running_coach_ai.slack.admin._add_athlete", return_value="added") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin add <@U456>", db)
        mock_fn.assert_called_once()
        assert mock_fn.call_args[0][0] == "U456"


# ---------------------------------------------------------------------------
# !admin remove
# ---------------------------------------------------------------------------

class TestRemoveAthlete:

    def test_remove_allowed_athlete_sets_allowed_false(self):
        """Removing an athlete must set allowed=False and commit."""
        athlete = _make_athlete(slack_user_id="U123", allowed=True)
        db = _db_returning(athlete)
        result = _remove_athlete("U123", db)
        assert athlete.allowed is False
        db.commit.assert_called_once()
        assert "removed" in result.lower() or "U123" in result

    def test_remove_athlete_data_retained_message(self):
        """Response must state that data is retained and access can be restored."""
        athlete = _make_athlete(slack_user_id="U123", allowed=True)
        db = _db_returning(athlete)
        result = _remove_athlete("U123", db)
        assert "data" in result.lower() or "retained" in result.lower() or "restore" in result.lower() or "add" in result.lower()

    def test_remove_nonexistent_athlete_returns_not_found(self):
        """Removing a user not in the DB must return a 'not found' message."""
        db = _db_returning(None)
        result = _remove_athlete("UXXX", db)
        assert "no athlete" in result.lower() or "not found" in result.lower()
        db.commit.assert_not_called()

    def test_remove_already_removed_returns_already_message(self):
        """Removing an already-removed athlete must not double-commit."""
        athlete = _make_athlete(slack_user_id="U123", allowed=False)
        db = _db_returning(athlete)
        result = _remove_athlete("U123", db)
        db.commit.assert_not_called()
        assert "already" in result.lower() or "removed" in result.lower()

    def test_handle_admin_command_routes_remove(self):
        """`!admin remove <uid>` must call _remove_athlete."""
        db = MagicMock()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings, \
             patch("running_coach_ai.slack.admin._remove_athlete", return_value="removed") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin remove U789", db)
        mock_fn.assert_called_once()
        assert mock_fn.call_args[0][0] == "U789"

    def test_handle_admin_command_remove_with_mention_format(self):
        """`!admin remove <@U789>` must extract bare user ID."""
        db = MagicMock()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings, \
             patch("running_coach_ai.slack.admin._remove_athlete", return_value="removed") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin remove <@U789>", db)
        mock_fn.assert_called_once()
        assert mock_fn.call_args[0][0] == "U789"


# ---------------------------------------------------------------------------
# !admin list
# ---------------------------------------------------------------------------

class TestListAthletes:

    def _db_with_athletes(self, athletes):
        db = MagicMock()
        q = MagicMock()
        q.order_by.return_value.all.return_value = athletes
        db.query.return_value = q
        return db

    def test_list_empty_returns_no_athletes_message(self):
        """Empty DB must return an informative 'no athletes' message."""
        db = self._db_with_athletes([])
        result = _list_athletes(db)
        assert "no athletes" in result.lower() or "none" in result.lower() or "registered" in result.lower()

    def test_list_shows_athlete_name(self):
        """Listed athletes must include the athlete's name."""
        athlete = _make_athlete(name="Bob Runner")
        db = self._db_with_athletes([athlete])
        result = _list_athletes(db)
        assert "Bob Runner" in result

    def test_list_shows_slack_user_id(self):
        """Listed athletes must include their Slack user reference."""
        athlete = _make_athlete(slack_user_id="UABC")
        db = self._db_with_athletes([athlete])
        result = _list_athletes(db)
        assert "UABC" in result

    def test_list_active_athlete_shows_active_status(self):
        """Allowed + onboarding_complete athlete must be labelled active."""
        athlete = _make_athlete(allowed=True, onboarding_complete=True)
        db = self._db_with_athletes([athlete])
        result = _list_athletes(db)
        assert "active" in result.lower() or "✅" in result

    def test_list_onboarding_athlete_shows_onboarding_status(self):
        """Allowed but onboarding_complete=False must be labelled as onboarding."""
        athlete = _make_athlete(allowed=True, onboarding_complete=False)
        db = self._db_with_athletes([athlete])
        result = _list_athletes(db)
        assert "onboard" in result.lower() or "⏳" in result

    def test_list_removed_athlete_shows_removed_status(self):
        """allowed=False athlete must be labelled as removed."""
        athlete = _make_athlete(allowed=False, onboarding_complete=True)
        db = self._db_with_athletes([athlete])
        result = _list_athletes(db)
        assert "removed" in result.lower() or "🚫" in result

    def test_list_multiple_athletes_all_shown(self):
        """All athletes must appear in the response."""
        athletes = [
            _make_athlete(slack_user_id="U1", name="Alice"),
            _make_athlete(slack_user_id="U2", name="Bob"),
            _make_athlete(slack_user_id="U3", name="Carol"),
        ]
        db = self._db_with_athletes(athletes)
        result = _list_athletes(db)
        assert "Alice" in result and "Bob" in result and "Carol" in result

    def test_handle_admin_command_routes_list(self):
        """`!admin list` must call _list_athletes."""
        db = MagicMock()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings, \
             patch("running_coach_ai.slack.admin._list_athletes", return_value="athletes") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin list", db)
        mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# Command parsing — mention format and target user extraction
# ---------------------------------------------------------------------------

class TestCommandParsing:

    def _settings(self):
        return patch("running_coach_ai.slack.admin.settings")

    def test_resync_with_bare_user_id_passes_target(self):
        """`!admin resync-garmin U456` must pass U456 as target_user_id."""
        db = MagicMock()
        with self._settings() as mock_settings, \
             patch("running_coach_ai.slack.admin._resync_garmin", return_value="ok") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin resync-garmin U456", db)
        assert mock_fn.call_args[0][1] == "U456"

    def test_resync_with_mention_format_passes_target(self):
        """`!admin resync-garmin <@U456>` must pass U456 as target_user_id."""
        db = MagicMock()
        with self._settings() as mock_settings, \
             patch("running_coach_ai.slack.admin._resync_garmin", return_value="ok") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin resync-garmin <@U456>", db)
        assert mock_fn.call_args[0][1] == "U456"

    def test_resync_without_target_passes_none(self):
        """`!admin resync-garmin` (no user) must pass target_user_id=None."""
        db = MagicMock()
        with self._settings() as mock_settings, \
             patch("running_coach_ai.slack.admin._resync_garmin", return_value="ok") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin resync-garmin", db)
        assert mock_fn.call_args[0][1] is None

    def test_clean_with_mention_format_passes_target(self):
        """`!admin clean-garmin <@U789>` must pass U789 as target_user_id."""
        db = MagicMock()
        with self._settings() as mock_settings, \
             patch("running_coach_ai.slack.admin._clean_garmin", return_value="ok") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin clean-garmin <@U789>", db)
        assert mock_fn.call_args[0][1] == "U789"

    def test_verify_with_mention_format_passes_target(self):
        """`!admin verify-garmin <@U999>` must pass U999 as target_user_id."""
        db = MagicMock()
        with self._settings() as mock_settings, \
             patch("running_coach_ai.slack.admin._verify_garmin", return_value="ok") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin verify-garmin <@U999>", db)
        assert mock_fn.call_args[0][1] == "U999"

    def test_resync_without_target_uses_sender_as_lookup(self):
        """`!admin resync-garmin` (no target) must look up the sender's own athlete row."""
        athlete = _make_athlete(slack_user_id="ADMIN1")
        db = _db_for_garmin(athlete, [TODAY + timedelta(days=3)])
        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_calendar", return_value=[]), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", return_value=(1, 0, [])):
            mock_auth.return_value = MagicMock()
            result = _resync_garmin("ADMIN1", None, db, confirmed=True)
        # Lookup was done — no "no athlete" error
        assert "no athlete" not in result.lower()


# ---------------------------------------------------------------------------
# _resync_garmin — error paths
# ---------------------------------------------------------------------------

class TestResyncErrorPaths:

    def test_no_athlete_found_returns_error(self):
        """No matching athlete row must return an informative error."""
        db = _db_for_garmin(None, [])
        result = _resync_garmin("U123", None, db, confirmed=True)
        assert "no athlete" in result.lower() or "not found" in result.lower()

    def test_no_garmin_credentials_returns_error(self):
        """Athlete with no Garmin credentials must return an error, no API calls."""
        athlete = _make_athlete(garmin_email=None, garmin_password_encrypted=None)
        db = _db_for_garmin(athlete, [TODAY + timedelta(days=2)])
        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth:
            result = _resync_garmin("U123", None, db, confirmed=True)
        mock_auth.assert_not_called()
        assert "credentials" in result.lower() or "no garmin" in result.lower() or "onboard" in result.lower()

    def test_garmin_auth_failure_returns_error(self):
        """Auth failure during confirmed resync must return a user-readable error."""
        athlete = _make_athlete()
        db = _db_for_garmin(athlete, [TODAY + timedelta(days=2)])
        with patch("running_coach_ai.garmin.client.get_garmin_client", side_effect=Exception("auth failed")):
            result = _resync_garmin("U123", None, db, confirmed=True)
        assert "authenticate" in result.lower() or "auth" in result.lower() or "could not" in result.lower()

    def test_errored_week_reported_in_output(self):
        """If sync_week_to_garmin raises, the errored week must appear in the response."""
        athlete = _make_athlete()
        bad_week = TODAY + timedelta(days=3)
        db = _db_for_garmin(athlete, [bad_week])
        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_calendar", return_value=[]), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   side_effect=Exception("pace missing")):
            mock_auth.return_value = MagicMock()
            result = _resync_garmin("U123", None, db, confirmed=True)
        # The week that errored must appear in the result
        assert "error" in result.lower() or str(bad_week - timedelta(days=bad_week.weekday())) in result

    def test_partial_failure_reports_failed_dates(self):
        """sync_week_to_garmin returning failed_dates must list them in the response."""
        athlete = _make_athlete()
        fail_date = TODAY + timedelta(days=4)
        db = _db_for_garmin(athlete, [fail_date])
        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_calendar", return_value=[]), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   return_value=(0, 1, [fail_date])):
            mock_auth.return_value = MagicMock()
            result = _resync_garmin("U123", None, db, confirmed=True)
        assert str(fail_date) in result or "failed" in result.lower()

    def test_target_user_id_looked_up_not_sender(self):
        """When target_user_id is given, the target's row is fetched, not the sender's."""
        from running_coach_ai.database.models import Athlete

        target = _make_athlete(slack_user_id="UTARGET")
        db = MagicMock()
        athlete_q = MagicMock()
        athlete_q.filter.return_value.first.return_value = target
        workout_q = MagicMock()
        workout_q.filter.return_value.distinct.return_value.all.return_value = []
        db.query.side_effect = lambda m: athlete_q if m is Athlete else workout_q

        result = _resync_garmin("ADMIN1", "UTARGET", db, confirmed=False)

        # The returned text must reference the target athlete's name, not the admin
        assert "Alice" in result or "UTARGET" in result


# ---------------------------------------------------------------------------
# _clean_garmin — error paths
# ---------------------------------------------------------------------------

class TestCleanErrorPaths:

    def test_no_athlete_found_returns_error(self):
        """No matching athlete row must return an informative error."""
        db = _db_for_garmin(None, [])
        result = _clean_garmin("U123", None, db, confirmed=True)
        assert "no athlete" in result.lower() or "not found" in result.lower()

    def test_no_garmin_credentials_returns_error(self):
        """Athlete with no Garmin credentials must return an error, no API calls."""
        athlete = _make_athlete(garmin_email=None, garmin_password_encrypted=None)
        db = _db_for_garmin(athlete, [TODAY + timedelta(days=2)])
        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth:
            result = _clean_garmin("U123", None, db, confirmed=True)
        mock_auth.assert_not_called()
        assert "credentials" in result.lower() or "no garmin" in result.lower()

    def test_garmin_auth_failure_returns_error(self):
        """Auth failure during confirmed clean must return a user-readable error."""
        athlete = _make_athlete()
        db = _db_for_garmin(athlete, [TODAY + timedelta(days=2)])
        with patch("running_coach_ai.garmin.client.get_garmin_client",
                   side_effect=Exception("bad creds")):
            result = _clean_garmin("U123", None, db, confirmed=True)
        assert "authenticate" in result.lower() or "could not" in result.lower()

    def test_library_delete_failure_does_not_abort_resync(self):
        """A delete_workout failure must not prevent the re-sync step from running."""
        athlete = _make_athlete()
        db = _db_for_garmin(athlete, [TODAY + timedelta(days=3)])
        library = [{"workoutId": 10}, {"workoutId": 20}]
        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=library), \
             patch("running_coach_ai.garmin.client.delete_workout", side_effect=Exception("500")), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   return_value=(1, 0, [])) as mock_sync:
            mock_auth.return_value = MagicMock()
            result = _clean_garmin("U123", None, db, confirmed=True)
        mock_sync.assert_called_once()

    def test_library_delete_failure_count_in_response(self):
        """Failed library deletes must be reported in the response."""
        athlete = _make_athlete()
        db = _db_for_garmin(athlete, [TODAY + timedelta(days=3)])
        library = [{"workoutId": 10}]
        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=library), \
             patch("running_coach_ai.garmin.client.delete_workout", side_effect=Exception("500")), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   return_value=(1, 0, [])):
            mock_auth.return_value = MagicMock()
            result = _clean_garmin("U123", None, db, confirmed=True)
        # "1 failed" or similar must appear
        assert "fail" in result.lower() or "1" in result

    def test_resync_exception_during_clean_does_not_crash(self):
        """If sync_week_to_garmin raises during the clean resync step, it must be caught."""
        athlete = _make_athlete()
        db = _db_for_garmin(athlete, [TODAY + timedelta(days=3)])
        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=[]), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   side_effect=Exception("network error")):
            mock_auth.return_value = MagicMock()
            # Must not raise — the exception must be caught internally
            result = _clean_garmin("U123", None, db, confirmed=True)
        assert result is not None

    def test_result_message_includes_deleted_cleared_and_resynced_counts(self):
        """Clean result must report library deletes, DB ID clears, and re-sync count."""
        athlete = _make_athlete()
        db = _db_for_garmin(athlete, [TODAY + timedelta(days=3)])
        library = [{"workoutId": 10}, {"workoutId": 20}]
        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=library), \
             patch("running_coach_ai.garmin.client.delete_workout"), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   return_value=(1, 0, [])):
            mock_auth.return_value = MagicMock()
            result = _clean_garmin("U123", None, db, confirmed=True)
        # Deleted 2 from library
        assert "2" in result
        # Re-synced 1
        assert "1" in result
        # All three action lines must be present
        assert "deleted" in result.lower() or "Deleted" in result
        assert "cleared" in result.lower() or "Cleared" in result
        assert "synced" in result.lower() or "Synced" in result

    def test_clean_warning_includes_target_user_in_confirm_command(self):
        """Clean warning for a targeted user must show the correct confirm command."""
        athlete = _make_athlete(slack_user_id="UTARGET")
        db = _db_for_garmin(athlete, [TODAY + timedelta(days=2)])
        result = _clean_garmin("ADMIN1", "UTARGET", db, confirmed=False)
        assert "UTARGET" in result
        assert "--confirm" in result


# ---------------------------------------------------------------------------
# T075 — !admin prefix routing
# ---------------------------------------------------------------------------

class TestAdminPrefixRouting:
    """Verify !admin prefix is required; /admin prefix must NOT be intercepted."""

    def _admin_id(self):
        return "ADMIN_USER"

    def _db(self):
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value.first.return_value = None
        q.order_by.return_value.all.return_value = []
        db.query.return_value = q
        return db

    def test_admin_add_prefix_routes_to_handler(self):
        """!admin add U123 must invoke handle_admin_command and return a response."""
        db = self._db()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = self._admin_id()
            result = handle_admin_command(self._admin_id(), "!admin add U123", db)
        # Should return a string (success or error message), not None
        assert result is not None
        assert isinstance(result, str)

    def test_admin_remove_prefix_routes_to_handler(self):
        """!admin remove U123 must invoke handle_admin_command and return a response."""
        db = self._db()
        athlete = _make_athlete(slack_user_id="U123")
        db.query.return_value.filter.return_value.first.return_value = athlete

        with patch("running_coach_ai.slack.admin.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = self._admin_id()
            result = handle_admin_command(self._admin_id(), "!admin remove U123", db)
        assert result is not None
        assert isinstance(result, str)

    def test_admin_list_prefix_routes_to_handler(self):
        """!admin list must invoke handle_admin_command and return a response."""
        db = self._db()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = self._admin_id()
            result = handle_admin_command(self._admin_id(), "!admin list", db)
        assert result is not None
        assert isinstance(result, str)

    def test_slash_admin_prefix_is_not_intercepted(self):
        """/admin add U123 (slash prefix) must NOT be handled — returns None."""
        db = self._db()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = self._admin_id()
            result = handle_admin_command(self._admin_id(), "/admin add U123", db)
        # The regex in handle_admin_command only matches !admin, not /admin
        assert result is None, (
            "/admin prefix must not be intercepted — only !admin is valid"
        )

    def test_slash_admin_remove_is_not_intercepted(self):
        """/admin remove U123 (slash prefix) must NOT be handled."""
        db = self._db()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = self._admin_id()
            result = handle_admin_command(self._admin_id(), "/admin remove U123", db)
        assert result is None

    def test_non_admin_with_exclamation_prefix_is_ignored(self):
        """Non-admin sender using !admin prefix must be silently ignored (returns None)."""
        db = self._db()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = "REAL_ADMIN"
            result = handle_admin_command("ATTACKER", "!admin add BACKDOOR", db)
        assert result is None, "Non-admin sender must be silently ignored"

    def test_admin_prefix_case_variations_are_handled(self):
        """!Admin and !ADMIN (mixed case) must also be intercepted."""
        db = self._db()
        with patch("running_coach_ai.slack.admin.settings") as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = self._admin_id()
            # Mixed case — handle_admin_command uses re.IGNORECASE
            result_upper = handle_admin_command(self._admin_id(), "!ADMIN list", db)
            result_title = handle_admin_command(self._admin_id(), "!Admin list", db)
        assert result_upper is not None
        assert result_title is not None

