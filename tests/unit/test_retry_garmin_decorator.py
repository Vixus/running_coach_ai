"""T056: Tests for the @retry_garmin decorator in garmin/client.py."""

from unittest.mock import MagicMock, patch

import pytest


class TestRetryGarminDecorator:
    """Verify the @retry_garmin() decorator handles retryable and non-retryable errors."""

    def _make_garmin(self):
        return MagicMock()

    def test_successful_call_returns_result(self):
        """A successful first call must return the result without retrying."""
        from running_coach_ai.garmin.client import upload_workout

        mock_garmin = self._make_garmin()
        mock_garmin.upload_workout.return_value = {"workoutId": 42}

        result = upload_workout(mock_garmin, {"name": "Easy Run"})

        assert result == {"workoutId": 42}
        mock_garmin.upload_workout.assert_called_once()

    def test_http_429_retries_and_succeeds(self):
        """A 429 error must trigger retry; subsequent success must be returned."""
        import requests

        from running_coach_ai.garmin.client import upload_workout

        # Simulate 429 on first call, success on second
        rate_limit_err = requests.exceptions.HTTPError(response=MagicMock(status_code=429))
        mock_garmin = self._make_garmin()
        mock_garmin.upload_workout.side_effect = [
            rate_limit_err,
            {"workoutId": 99},
        ]

        with patch("running_coach_ai.garmin.client.time") as mock_time:
            mock_time.sleep = MagicMock()
            result = upload_workout(mock_garmin, {"name": "Tempo Run"})

        assert result == {"workoutId": 99}
        assert mock_garmin.upload_workout.call_count == 2

    def test_http_500_retries(self):
        """A 500 server error must trigger a retry."""
        import requests

        from running_coach_ai.garmin.client import upload_workout

        server_err = requests.exceptions.HTTPError(response=MagicMock(status_code=500))
        mock_garmin = self._make_garmin()
        mock_garmin.upload_workout.side_effect = [
            server_err,
            {"workoutId": 77},
        ]

        with patch("running_coach_ai.garmin.client.time") as mock_time:
            mock_time.sleep = MagicMock()
            result = upload_workout(mock_garmin, {"name": "Long Run"})

        assert result == {"workoutId": 77}
        assert mock_garmin.upload_workout.call_count == 2

    def test_exhausted_retries_re_raises(self):
        """After max_attempts retries are exhausted, the last exception must be re-raised."""
        import requests

        from running_coach_ai.garmin.client import upload_workout

        server_err = requests.exceptions.HTTPError(response=MagicMock(status_code=500))
        mock_garmin = self._make_garmin()
        # Keep failing on every call
        mock_garmin.upload_workout.side_effect = server_err

        with patch("running_coach_ai.garmin.client.time") as mock_time:
            mock_time.sleep = MagicMock()
            with pytest.raises(requests.exceptions.HTTPError):
                upload_workout(mock_garmin, {"name": "Intervals"})

        # Should have attempted multiple times (decorator default is 3)
        assert mock_garmin.upload_workout.call_count >= 2

    def test_schedule_workout_has_retry_decorator(self):
        """schedule_workout must succeed on a retry after a transient error."""
        import requests

        from running_coach_ai.garmin.client import schedule_workout

        transient_err = requests.exceptions.ConnectionError("connection reset")
        mock_garmin = self._make_garmin()
        mock_garmin.schedule_workout.side_effect = [transient_err, {"workoutScheduleId": 5}]

        with patch("running_coach_ai.garmin.client.time") as mock_time:
            mock_time.sleep = MagicMock()
            result = schedule_workout(mock_garmin, 42, "2026-06-01")

        assert result == {"workoutScheduleId": 5}
        assert mock_garmin.schedule_workout.call_count == 2

    def test_delete_workout_has_retry_decorator(self):
        """delete_workout must retry on connection errors."""
        import requests

        from running_coach_ai.garmin.client import delete_workout

        mock_garmin = self._make_garmin()
        transient_err = requests.exceptions.ConnectionError("timeout")
        mock_garmin.garth.request.side_effect = [transient_err, None]

        with patch("running_coach_ai.garmin.client.time") as mock_time:
            mock_time.sleep = MagicMock()
            delete_workout(mock_garmin, 101)

        assert mock_garmin.garth.request.call_count == 2
