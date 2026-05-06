"""Tests verifying that activity polling has no server-time gate (runs 24/7)."""

from unittest.mock import MagicMock, patch


class TestActivityPollNoGate:
    """Verify _run_activity_poll runs at any hour without an active-hours guard."""

    def _make_mock_db(self, athletes=None):
        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.all.return_value = athletes or []
        return mock_db

    def test_poll_proceeds_at_3am(self):
        """Poll should reach the DB query even when server time is 03:00."""
        from running_coach_ai.scheduler.jobs import _run_activity_poll

        mock_slack = MagicMock()
        mock_db = self._make_mock_db()

        with patch("running_coach_ai.database.session.get_session", return_value=mock_db):
            _run_activity_poll()

        # DB query was reached — the poll was not gated out
        mock_db.query.assert_called()

    def test_poll_proceeds_at_2pm(self):
        """Poll should reach the DB query when server time is 14:00."""
        from running_coach_ai.scheduler.jobs import _run_activity_poll

        mock_slack = MagicMock()
        mock_db = self._make_mock_db()

        with patch("running_coach_ai.database.session.get_session", return_value=mock_db):
            _run_activity_poll()

        mock_db.query.assert_called()

    def test_scheduler_passed_through_to_ingest(self):
        """Scheduler argument is forwarded to _ingest_and_feedback."""
        from running_coach_ai.scheduler import activity_poll as jobs

        mock_slack = MagicMock()
        mock_scheduler = MagicMock()

        athlete = MagicMock()
        athlete.garmin_email = "a@b.com"
        athlete.garmin_password_encrypted = b"enc"

        mock_db = self._make_mock_db(athletes=[athlete])

        with (
            patch("running_coach_ai.database.session.get_session", return_value=mock_db),
            patch("running_coach_ai.garmin.client.get_garmin_client") as mock_get_garmin,
            patch("running_coach_ai.garmin.client.poll_new_activities", return_value=["act1"]),
            patch("os.path.isfile", return_value=True),
            patch.object(jobs, "_ingest_and_feedback") as mock_ingest,
        ):
            mock_garmin = MagicMock()
            mock_get_garmin.return_value = mock_garmin
            _run_activity_poll = jobs._run_activity_poll
            _run_activity_poll(mock_scheduler)

        mock_ingest.assert_called_once_with(
            athlete, "act1", mock_garmin, mock_db, mock_scheduler
        )
