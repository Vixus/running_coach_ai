"""Tests for get_garmin_calendar 404 handling.

Garmin returns 404 (not an empty list) when a date range has no scheduled workouts.
This is normal for future/empty weeks and must be treated as an empty calendar, not an error.
"""

import logging
from unittest.mock import MagicMock


from running_coach_ai.garmin.client import get_garmin_calendar


def _garmin_raising(error_msg: str) -> MagicMock:
    garmin = MagicMock()
    garmin.connectapi.side_effect = Exception(error_msg)
    return garmin


class TestGetGarminCalendar404:

    def test_404_returns_empty_list(self):
        """404 from Garmin calendar API must return [] not raise."""
        garmin = _garmin_raising("404 Client Error: Not Found")
        result = get_garmin_calendar(garmin, "2026-09-07", "2026-09-13")
        assert result == []

    def test_404_does_not_log_warning(self, caplog):
        """404 must be logged at DEBUG, not WARNING — it is expected for empty weeks."""
        garmin = _garmin_raising("404 Client Error: Not Found")
        with caplog.at_level(logging.WARNING, logger="running_coach_ai.garmin.client"):
            get_garmin_calendar(garmin, "2026-09-07", "2026-09-13")
        assert not any("Could not read Garmin calendar" in r.message for r in caplog.records)

    def test_404_logs_at_debug(self, caplog):
        """404 must produce a DEBUG log so empty weeks are still traceable."""
        garmin = _garmin_raising("404 Client Error: Not Found")
        with caplog.at_level(logging.DEBUG, logger="running_coach_ai.garmin.client"):
            get_garmin_calendar(garmin, "2026-09-07", "2026-09-13")
        assert any("2026-09-07" in r.message for r in caplog.records)

    def test_non_404_error_logs_warning(self, caplog):
        """Non-404 errors (500, auth failure, etc.) must still log at WARNING."""
        garmin = _garmin_raising("500 Internal Server Error")
        with caplog.at_level(logging.WARNING, logger="running_coach_ai.garmin.client"):
            get_garmin_calendar(garmin, "2026-04-07", "2026-04-13")
        assert any("Could not read Garmin calendar" in r.message for r in caplog.records)

    def test_non_404_error_returns_empty_list(self):
        """Any API error (including non-404) must still return [] safely."""
        garmin = _garmin_raising("500 Internal Server Error")
        result = get_garmin_calendar(garmin, "2026-04-07", "2026-04-13")
        assert result == []

    def test_success_returns_list(self):
        """When the API returns a list, it is passed through unchanged."""
        entries = [{"workoutId": 1, "workoutScheduleId": 2, "date": "2026-04-08"}]
        garmin = MagicMock()
        garmin.connectapi.return_value = entries
        result = get_garmin_calendar(garmin, "2026-04-07", "2026-04-13")
        assert result == entries

    def test_non_list_response_returns_empty_list(self):
        """If Garmin returns something other than a list, return [] safely."""
        garmin = MagicMock()
        garmin.connectapi.return_value = {"unexpected": "dict"}
        result = get_garmin_calendar(garmin, "2026-04-07", "2026-04-13")
        assert result == []
