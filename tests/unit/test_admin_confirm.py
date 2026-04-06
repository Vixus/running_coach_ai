"""Tests for !admin resync-garmin and !admin clean-garmin confirmation flow."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from running_coach_ai.slack.admin import _clean_garmin, _resync_garmin, handle_admin_command

TODAY = date.today()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _athlete(slack_user_id="U123"):
    a = MagicMock()
    a.id = 1
    a.slack_user_id = slack_user_id
    a.name = "Alice"
    a.garmin_email = "alice@example.com"
    a.garmin_password_encrypted = b"enc"
    return a


def _db_with_upcoming(athlete, upcoming_count: int):
    """DB mock that returns `upcoming_count` upcoming workout rows."""
    db = MagicMock()
    athlete_q = MagicMock()
    athlete_q.filter.return_value.first.return_value = athlete

    date_rows = [
        MagicMock(scheduled_date=TODAY + timedelta(days=i + 1))
        for i in range(upcoming_count)
    ]
    workout_q = MagicMock()
    workout_q.filter.return_value.distinct.return_value.all.return_value = date_rows
    workout_q.filter.return_value.count.return_value = upcoming_count
    workout_q.filter.return_value.update.return_value = upcoming_count

    def _side(model_or_col):
        from running_coach_ai.database.models import Athlete
        return athlete_q if model_or_col is Athlete else workout_q

    db.query.side_effect = _side
    return db


# ---------------------------------------------------------------------------
# _resync_garmin: warning (no --confirm)
# ---------------------------------------------------------------------------

class TestResyncWarning:

    def test_no_confirm_returns_warning_not_result(self):
        """Without --confirm, _resync_garmin must return a warning, not execute."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 5)

        result = _resync_garmin("U123", None, db, confirmed=False)

        assert "confirm" in result.lower() or "warning" in result.lower() or "⚠️" in result

    def test_warning_mentions_upcoming_count(self):
        """Warning must state how many workouts will be affected."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 7)

        result = _resync_garmin("U123", None, db, confirmed=False)

        assert "7" in result

    def test_warning_shows_confirm_command(self):
        """Warning must tell the admin the exact command to confirm."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 3)

        result = _resync_garmin("U123", None, db, confirmed=False)

        assert "--confirm" in result

    def test_warning_no_garmin_api_calls(self):
        """Without --confirm, no Garmin API calls must be made."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 3)

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth:
            _resync_garmin("U123", None, db, confirmed=False)

        mock_auth.assert_not_called()

    def test_no_upcoming_workouts_returns_early(self):
        """No upcoming workouts: should return early regardless of --confirm."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 0)

        result = _resync_garmin("U123", None, db, confirmed=False)

        assert "no upcoming" in result.lower() or "no" in result.lower()

    def test_confirmed_true_executes_not_returns_warning(self):
        """With --confirm, must execute, not return the warning message."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 2)

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_calendar", return_value=[]), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", return_value=(2, 0, [])):
            mock_auth.return_value = MagicMock()
            result = _resync_garmin("U123", None, db, confirmed=True)

        # The execution path produces a result message, not a "confirm required" warning
        assert "--confirm" not in result or "Synced" in result


# ---------------------------------------------------------------------------
# _clean_garmin: warning (no --confirm)
# ---------------------------------------------------------------------------

class TestCleanWarning:

    def test_no_confirm_returns_warning(self):
        """Without --confirm, _clean_garmin must return a warning, not execute."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 4)

        result = _clean_garmin("U123", None, db, confirmed=False)

        assert "confirm" in result.lower() or "⚠️" in result

    def test_warning_mentions_library_deletion(self):
        """Warning must state that the entire Garmin workout library will be deleted."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 3)

        result = _clean_garmin("U123", None, db, confirmed=False)

        assert "library" in result.lower() or "every" in result.lower() or "all" in result.lower()

    def test_warning_mentions_manual_workouts_risk(self):
        """Warning must flag that manually created Garmin workouts will be deleted."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 3)

        result = _clean_garmin("U123", None, db, confirmed=False)

        assert "manually" in result.lower() or "manual" in result.lower()

    def test_warning_shows_confirm_command(self):
        """Warning must include the exact --confirm command."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 3)

        result = _clean_garmin("U123", None, db, confirmed=False)

        assert "--confirm" in result

    def test_warning_no_garmin_api_calls(self):
        """Without --confirm, no Garmin API calls must be made."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 3)

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth:
            _clean_garmin("U123", None, db, confirmed=False)

        mock_auth.assert_not_called()


# ---------------------------------------------------------------------------
# _clean_garmin: execution (--confirm)
# ---------------------------------------------------------------------------

class TestCleanExecution:

    def test_confirmed_only_clears_future_db_rows(self):
        """With --confirm, DB ID clear must target only future rows (scheduled_date >= today)."""
        from running_coach_ai.database.models import Athlete

        athlete = _athlete()

        athlete_q = MagicMock()
        athlete_q.filter.return_value.first.return_value = athlete

        workout_q = MagicMock()
        # upcoming query (for re-sync)
        upcoming_date = TODAY + timedelta(days=3)
        workout_q.filter.return_value.distinct.return_value.all.return_value = [
            MagicMock(scheduled_date=upcoming_date)
        ]
        workout_q.filter.return_value.count.return_value = 1
        workout_q.filter.return_value.update.return_value = 1

        db = MagicMock()
        db.query.side_effect = lambda m: athlete_q if m is Athlete else workout_q

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=[]), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", return_value=(1, 0, [])):
            mock_auth.return_value = MagicMock()
            _clean_garmin("U123", None, db, confirmed=True)

        # The update() call must have been made — clears future Garmin IDs
        workout_q.filter.return_value.update.assert_called_once_with(
            {"garmin_workout_id": None, "garmin_schedule_id": None}
        )

    def test_confirmed_deletes_library_entries(self):
        """With --confirm, each workout in the Garmin library must be deleted."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 1)

        library = [{"workoutId": 10}, {"workoutId": 20}, {"workoutId": 30}]

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=library), \
             patch("running_coach_ai.garmin.client.delete_workout") as mock_del, \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", return_value=(1, 0, [])):
            mock_auth.return_value = MagicMock()
            result = _clean_garmin("U123", None, db, confirmed=True)

        assert mock_del.call_count == 3

    def test_confirmed_resyncs_upcoming_workouts(self):
        """After clean, all upcoming weeks must be re-synced to Garmin."""
        athlete = _athlete()
        # 3 workouts in 3 different weeks
        db = _db_with_upcoming(athlete, 3)

        # Give them dates in 3 different weeks
        from running_coach_ai.database.models import Athlete
        workout_q = MagicMock()
        week_dates = [
            TODAY + timedelta(days=2),
            TODAY + timedelta(days=9),
            TODAY + timedelta(days=16),
        ]
        workout_q.filter.return_value.distinct.return_value.all.return_value = [
            MagicMock(scheduled_date=d) for d in week_dates
        ]
        workout_q.filter.return_value.count.return_value = 3
        workout_q.filter.return_value.update.return_value = 3

        athlete_q = MagicMock()
        athlete_q.filter.return_value.first.return_value = athlete

        db2 = MagicMock()
        db2.query.side_effect = lambda m: athlete_q if m is Athlete else workout_q

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=[]), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", return_value=(1, 0, [])) as mock_sync:
            mock_auth.return_value = MagicMock()
            _clean_garmin("U123", None, db2, confirmed=True)

        assert mock_sync.call_count == 3


# ---------------------------------------------------------------------------
# handle_admin_command routing: --confirm flag parsing
# ---------------------------------------------------------------------------

class TestHandleAdminConfirmRouting:

    def _settings_patch(self):
        return patch("running_coach_ai.slack.admin.settings")

    def test_resync_without_confirm_returns_warning(self):
        """!admin resync-garmin (no --confirm) must return a warning."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 3)

        with self._settings_patch() as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            with patch("running_coach_ai.slack.admin._resync_garmin") as mock_fn:
                mock_fn.return_value = "⚠️ confirm required"
                result = handle_admin_command("ADMIN1", "!admin resync-garmin", db)

        mock_fn.assert_called_once()
        call_kwargs = mock_fn.call_args.kwargs if hasattr(mock_fn.call_args, "kwargs") else mock_fn.call_args[1]
        assert call_kwargs.get("confirmed") is False

    def test_resync_with_confirm_passes_confirmed_true(self):
        """!admin resync-garmin --confirm must pass confirmed=True."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 3)

        with self._settings_patch() as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            with patch("running_coach_ai.slack.admin._resync_garmin") as mock_fn:
                mock_fn.return_value = "synced"
                handle_admin_command("ADMIN1", "!admin resync-garmin --confirm", db)

        mock_fn.assert_called_once()
        call_kwargs = mock_fn.call_args[1] if mock_fn.call_args[1] else {}
        assert call_kwargs.get("confirmed") is True

    def test_clean_with_confirm_passes_confirmed_true(self):
        """!admin clean-garmin --confirm must pass confirmed=True."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 2)

        with self._settings_patch() as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            with patch("running_coach_ai.slack.admin._clean_garmin") as mock_fn:
                mock_fn.return_value = "cleaned"
                handle_admin_command("ADMIN1", "!admin clean-garmin --confirm", db)

        mock_fn.assert_called_once()
        call_kwargs = mock_fn.call_args[1] if mock_fn.call_args[1] else {}
        assert call_kwargs.get("confirmed") is True

    def test_clean_without_confirm_passes_confirmed_false(self):
        """!admin clean-garmin (no --confirm) must pass confirmed=False."""
        athlete = _athlete()
        db = _db_with_upcoming(athlete, 2)

        with self._settings_patch() as mock_settings:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            with patch("running_coach_ai.slack.admin._clean_garmin") as mock_fn:
                mock_fn.return_value = "⚠️ warning"
                handle_admin_command("ADMIN1", "!admin clean-garmin", db)

        mock_fn.assert_called_once()
        call_kwargs = mock_fn.call_args[1] if mock_fn.call_args[1] else {}
        assert call_kwargs.get("confirmed") is False
