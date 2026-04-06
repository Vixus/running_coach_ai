"""T079: Unit tests for morning check-in health-data gate (polling behavior).

Tests cover:
  (a) Health data present on first tick → DM sent
  (b) All key health fields None before 10:00am → silent return, no DM
  (c) All key health fields None at/after 10:00am → INFO log, no DM
  (d) last_morning_checkin_date == today → immediate no-op, no Garmin call
"""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch


_ADAPTER = "running_coach_ai.coach.adapter"
TODAY = date.today()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_athlete(*, last_checkin_date=None, has_garmin=True, tz="America/New_York"):
    a = MagicMock()
    a.id = 1
    a.name = "Simon"
    a.timezone = tz
    a.garmin_email = "simon@example.com" if has_garmin else None
    a.garmin_password_encrypted = b"enc" if has_garmin else None
    a.home_lat = 40.7
    a.home_lon = -74.0
    a.last_morning_checkin_date = last_checkin_date
    return a


def _make_snapshot(*, sleep_score=75, hrv_score=42, body_battery_start=87):
    """Return a snapshot mock with the three key fields set."""
    s = MagicMock()
    s.date = TODAY
    s.sleep_score = sleep_score
    s.hrv_score = hrv_score
    s.hrv_status = "BALANCED"
    s.body_battery_start = body_battery_start
    s.resting_hr = 51
    s.stress_avg = 19
    return s


def _make_empty_snapshot():
    """Return a snapshot where all three key fields are None."""
    s = MagicMock()
    s.date = TODAY
    s.sleep_score = None
    s.hrv_score = None
    s.hrv_status = None
    s.body_battery_start = None
    s.resting_hr = None
    s.stress_avg = None
    return s


def _make_db():
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    db.query.return_value.filter.return_value.first.return_value = None
    return db


# ---------------------------------------------------------------------------
# (a) Health data present on first tick → DM sent
# ---------------------------------------------------------------------------

class TestHealthDataPresentFirstTick:
    @patch(f"{_ADAPTER}.extract_and_apply_plan", return_value="Morning message text")
    @patch(f"{_ADAPTER}.call_claude", return_value="Morning message text")
    @patch(f"{_ADAPTER}.scoped_query")
    @patch("running_coach_ai.slack.bot.send_dm")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    @patch("running_coach_ai.weather.client.summarise_forecast", return_value=[])
    @patch("running_coach_ai.weather.client.get_forecast", return_value={})
    def test_dm_sent_when_data_present(
        self,
        mock_forecast,
        mock_summarise,
        mock_garmin_client,
        mock_health_raw,
        mock_parse,
        mock_send_dm,
        mock_scoped,
        mock_claude,
        mock_extract,
    ):
        snapshot = _make_snapshot()
        mock_parse.return_value = snapshot

        # scoped_query for PlannedWorkout returns None (rest day)
        mock_scoped.return_value.filter.return_value.first.return_value = None

        athlete = _make_athlete()
        db = _make_db()
        slack_client = MagicMock()

        from running_coach_ai.coach.adapter import run_morning_checkin
        run_morning_checkin(athlete, db, slack_client)

        mock_send_dm.assert_called_once()
        assert athlete.last_morning_checkin_date == TODAY
        db.commit.assert_called()


# ---------------------------------------------------------------------------
# (b) All key health fields None before 10:00am → silent return, no DM
# ---------------------------------------------------------------------------

class TestNoHealthDataBefore10am:
    @patch("running_coach_ai.slack.bot.send_dm")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_silent_return_before_10am(
        self,
        mock_garmin_client,
        mock_health_raw,
        mock_parse,
        mock_send_dm,
    ):
        mock_parse.return_value = _make_empty_snapshot()

        athlete = _make_athlete()
        db = _make_db()
        slack_client = MagicMock()

        # Simulate 08:30 local time (before 10:00am)
        fake_local = datetime(TODAY.year, TODAY.month, TODAY.day, 8, 30, tzinfo=timezone.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            from running_coach_ai.coach.adapter import run_morning_checkin
            run_morning_checkin(athlete, db, slack_client)

        mock_send_dm.assert_not_called()
        assert athlete.last_morning_checkin_date != TODAY

    @patch("running_coach_ai.slack.bot.send_dm")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_silent_return_when_snapshot_is_none_before_10am(
        self,
        mock_garmin_client,
        mock_health_raw,
        mock_parse,
        mock_send_dm,
    ):
        """If Garmin fetch raises an exception, snapshot stays None → gate fires."""
        mock_parse.side_effect = Exception("Garmin API error")

        athlete = _make_athlete()
        db = _make_db()
        slack_client = MagicMock()

        fake_local = datetime(TODAY.year, TODAY.month, TODAY.day, 7, 0, tzinfo=timezone.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            from running_coach_ai.coach.adapter import run_morning_checkin
            run_morning_checkin(athlete, db, slack_client)

        mock_send_dm.assert_not_called()


# ---------------------------------------------------------------------------
# (c) All key health fields None at/after 10:00am → INFO log, no DM
# ---------------------------------------------------------------------------

class TestNoHealthDataAtOrAfter10am:
    @patch("running_coach_ai.slack.bot.send_dm")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_no_dm_at_10am_cutoff(
        self,
        mock_garmin_client,
        mock_health_raw,
        mock_parse,
        mock_send_dm,
    ):
        mock_parse.return_value = _make_empty_snapshot()

        athlete = _make_athlete()
        db = _make_db()
        slack_client = MagicMock()

        # Exactly 10:00am local
        fake_local = datetime(TODAY.year, TODAY.month, TODAY.day, 10, 0, tzinfo=timezone.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt, \
             patch(f"{_ADAPTER}.logger") as mock_logger:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            from running_coach_ai.coach.adapter import run_morning_checkin
            run_morning_checkin(athlete, db, slack_client)

            # INFO log must be emitted (not just debug)
            info_calls = [str(c) for c in mock_logger.info.call_args_list]
            assert any("skipping morning check-in" in msg.lower() or "skipping" in msg.lower()
                       for msg in info_calls), \
                f"Expected INFO skip log, got: {mock_logger.info.call_args_list}"

        mock_send_dm.assert_not_called()
        assert athlete.last_morning_checkin_date != TODAY

    @patch("running_coach_ai.slack.bot.send_dm")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_no_dm_after_10am(
        self,
        mock_garmin_client,
        mock_health_raw,
        mock_parse,
        mock_send_dm,
    ):
        mock_parse.return_value = _make_empty_snapshot()

        athlete = _make_athlete()
        db = _make_db()
        slack_client = MagicMock()

        # 11:30am local — well past the cutoff
        fake_local = datetime(TODAY.year, TODAY.month, TODAY.day, 11, 30, tzinfo=timezone.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            from running_coach_ai.coach.adapter import run_morning_checkin
            run_morning_checkin(athlete, db, slack_client)

        mock_send_dm.assert_not_called()


# ---------------------------------------------------------------------------
# (d) last_morning_checkin_date == today → immediate no-op, no Garmin call
# ---------------------------------------------------------------------------

class TestDedupGuard:
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_no_garmin_call_when_already_sent_today(self, mock_garmin_client):
        """Dedup guard must prevent even the Garmin health fetch on repeat ticks."""
        athlete = _make_athlete(last_checkin_date=TODAY)
        db = _make_db()
        slack_client = MagicMock()

        from running_coach_ai.coach.adapter import run_morning_checkin
        run_morning_checkin(athlete, db, slack_client)

        mock_garmin_client.assert_not_called()

    @patch("running_coach_ai.slack.bot.send_dm")
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_no_dm_when_already_sent_today(self, mock_garmin_client, mock_send_dm):
        athlete = _make_athlete(last_checkin_date=TODAY)
        db = _make_db()
        slack_client = MagicMock()

        from running_coach_ai.coach.adapter import run_morning_checkin
        run_morning_checkin(athlete, db, slack_client)

        mock_send_dm.assert_not_called()
