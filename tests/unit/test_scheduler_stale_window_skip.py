"""T058: Tests for the stale-window guard in _ingest_and_feedback."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch



def _make_activity_response(start_time_local: str) -> dict:
    """Build the structure returned by garmin.get_activity() with startTimeLocal in summaryDTO."""
    return {
        "activityId": 999,
        "summaryDTO": {
            "startTimeLocal": start_time_local,
        },
    }


class TestStaleWindowSkip:
    """_ingest_and_feedback must skip feedback for activities older than 16 hours."""

    def _make_athlete(self, athlete_id=1):
        a = MagicMock()
        a.id = athlete_id
        a.garmin_email = "test@example.com"
        a.garmin_password_encrypted = b"enc"
        return a

    def test_activity_older_than_16h_is_skipped(self):
        """Activity started >16 hours ago must not trigger parse or feedback."""
        stale_time = (datetime.now() - timedelta(hours=17)).strftime("%Y-%m-%d %H:%M:%S")
        activity_data = _make_activity_response(stale_time)

        mock_garmin = MagicMock()
        mock_garmin.get_activity.return_value = activity_data
        mock_garmin.get_activity_details.return_value = {"activityId": 999}

        athlete = self._make_athlete()
        mock_db = MagicMock()

        with patch("running_coach_ai.garmin.parser.parse_activity_summary") as mock_parse, \
             patch("running_coach_ai.coach.feedback.generate_post_run_feedback") as mock_feedback:
            from running_coach_ai.scheduler.jobs import _ingest_and_feedback
            _ingest_and_feedback(athlete, "ACT_123", mock_garmin, mock_db, MagicMock())

        # Stale activity: neither parse nor feedback should be called
        mock_parse.assert_not_called()
        mock_feedback.assert_not_called()

    def test_activity_within_16h_is_processed(self):
        """Activity started 1 hour ago must proceed to parse."""
        fresh_time = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        activity_data = _make_activity_response(fresh_time)

        mock_garmin = MagicMock()
        mock_garmin.get_activity.return_value = activity_data
        mock_garmin.get_activity_details.return_value = {"activityId": 999}

        athlete = self._make_athlete()
        mock_db = MagicMock()

        with patch("running_coach_ai.garmin.parser.parse_activity_summary",
                   return_value=MagicMock()) as mock_parse, \
             patch("running_coach_ai.garmin.telemetry.extract_telemetry",
                   return_value=MagicMock()), \
             patch("running_coach_ai.garmin.telemetry.ingest_lap_splits"), \
             patch("running_coach_ai.coach.biomechanics.analyse_workout", return_value={}), \
             patch("running_coach_ai.coach.biomechanics.update_running_profile"), \
             patch("running_coach_ai.coach.feedback.generate_post_run_feedback"):
            from running_coach_ai.scheduler.jobs import _ingest_and_feedback
            _ingest_and_feedback(athlete, "ACT_123", mock_garmin, mock_db, MagicMock())

        mock_parse.assert_called_once()

    def test_missing_start_time_proceeds_normally(self):
        """If startTimeLocal is absent, the guard must not block processing."""
        activity_data = {"activityId": 999, "summaryDTO": {}}

        mock_garmin = MagicMock()
        mock_garmin.get_activity.return_value = activity_data
        mock_garmin.get_activity_details.return_value = {"activityId": 999}

        athlete = self._make_athlete()
        mock_db = MagicMock()

        with patch("running_coach_ai.garmin.parser.parse_activity_summary",
                   return_value=MagicMock()) as mock_parse, \
             patch("running_coach_ai.garmin.telemetry.extract_telemetry",
                   return_value=MagicMock()), \
             patch("running_coach_ai.garmin.telemetry.ingest_lap_splits"), \
             patch("running_coach_ai.coach.biomechanics.analyse_workout", return_value={}), \
             patch("running_coach_ai.coach.biomechanics.update_running_profile"), \
             patch("running_coach_ai.coach.feedback.generate_post_run_feedback"):
            from running_coach_ai.scheduler.jobs import _ingest_and_feedback
            _ingest_and_feedback(athlete, "ACT_123", mock_garmin, mock_db, MagicMock())

        mock_parse.assert_called_once()

    def test_stale_activity_boundary_16h01m_is_skipped(self):
        """An activity 16h01m old must be skipped (exceeds the 16h cutoff)."""
        stale_time = (datetime.now() - timedelta(hours=16, minutes=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        activity_data = _make_activity_response(stale_time)

        mock_garmin = MagicMock()
        mock_garmin.get_activity.return_value = activity_data
        mock_garmin.get_activity_details.return_value = {"activityId": 999}

        athlete = self._make_athlete()
        mock_db = MagicMock()

        with patch("running_coach_ai.garmin.parser.parse_activity_summary") as mock_parse, \
             patch("running_coach_ai.coach.feedback.generate_post_run_feedback") as mock_feedback:
            from running_coach_ai.scheduler.jobs import _ingest_and_feedback
            _ingest_and_feedback(athlete, "ACT_123", mock_garmin, mock_db, MagicMock())

        mock_parse.assert_not_called()
        mock_feedback.assert_not_called()

