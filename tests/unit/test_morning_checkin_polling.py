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
    """Return a snapshot mock with the three key fields set + training_readiness."""
    s = MagicMock()
    s.date = TODAY
    s.sleep_score = sleep_score
    s.hrv_score = hrv_score
    s.hrv_status = "BALANCED"
    s.body_battery_start = body_battery_start
    s.resting_hr = 51
    s.stress_avg = 19
    s.training_readiness = 75  # primary signal — Garmin morning data is complete
    return s


def _make_empty_snapshot():
    """Return a snapshot where all key fields (incl. training_readiness) are None.
    Fails the morning-data gate, so production should not send a DM.
    """
    s = MagicMock()
    s.date = TODAY
    s.sleep_score = None
    s.hrv_score = None
    s.hrv_status = None
    s.body_battery_start = None
    s.resting_hr = None
    s.stress_avg = None
    s.training_readiness = None
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
    @patch("running_coach_ai.coach.notify.upsert_morning_checkin")
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

        # Mock current time to a daytime hour so the 06:00 local floor doesn't fire
        fake_local = datetime(TODAY.year, TODAY.month, TODAY.day, 8, 0, tzinfo=timezone.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            from running_coach_ai.coach.adapter import run_morning_checkin
            run_morning_checkin(athlete, db)

        mock_send_dm.assert_called_once()
        assert athlete.last_morning_checkin_date == TODAY
        db.commit.assert_called()


# ---------------------------------------------------------------------------
# (b) All key health fields None before 10:00am → silent return, no DM
# ---------------------------------------------------------------------------

class TestNoHealthDataBefore10am:
    @patch("running_coach_ai.coach.notify.upsert_morning_checkin")
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
            run_morning_checkin(athlete, db)

        mock_send_dm.assert_not_called()
        assert athlete.last_morning_checkin_date != TODAY

    @patch("running_coach_ai.coach.notify.upsert_morning_checkin")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_defers_when_garmin_fetch_fails_before_noon(
        self,
        mock_garmin_client,
        mock_health_raw,
        mock_parse,
        mock_notify,
    ):
        """When the Garmin parse raises before noon, the gate defers so the
        next 30-min tick gets a retry — check-in is NOT delivered yet."""
        mock_parse.side_effect = Exception("Garmin API error")

        athlete = _make_athlete()
        db = _make_db()

        fake_local = datetime(TODAY.year, TODAY.month, TODAY.day, 7, 0, tzinfo=timezone.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            from running_coach_ai.coach.adapter import run_morning_checkin
            run_morning_checkin(athlete, db)

        mock_notify.assert_not_called()

    @patch(f"{_ADAPTER}.extract_and_apply_plan", return_value="Morning message text")
    @patch(f"{_ADAPTER}.call_claude", return_value="Morning message text")
    @patch(f"{_ADAPTER}.scoped_query")
    @patch("running_coach_ai.coach.notify.upsert_morning_checkin")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    @patch("running_coach_ai.weather.client.summarise_forecast", return_value=[])
    @patch("running_coach_ai.weather.client.get_forecast", return_value={})
    def test_proceeds_when_garmin_fetch_fails_after_noon(
        self,
        mock_forecast,
        mock_summarise,
        mock_garmin_client,
        mock_health_raw,
        mock_parse,
        mock_notify,
        mock_scoped,
        mock_claude,
        mock_extract,
    ):
        """When the Garmin parse raises after noon, we fall through with stored
        data and deliver the check-in rather than waiting forever."""
        mock_parse.side_effect = Exception("Garmin API error")
        mock_scoped.return_value.filter.return_value.first.return_value = None

        athlete = _make_athlete()
        db = _make_db()

        fake_local = datetime(TODAY.year, TODAY.month, TODAY.day, 12, 30, tzinfo=timezone.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            from running_coach_ai.coach.adapter import run_morning_checkin
            run_morning_checkin(athlete, db)

        mock_notify.assert_called_once()


# ---------------------------------------------------------------------------
# (c) All key health fields None → silent retry before noon, INFO skip at/after noon
# ---------------------------------------------------------------------------

class TestNoHealthDataCutoff:
    @patch("running_coach_ai.coach.notify.upsert_morning_checkin")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_silent_retry_before_noon(
        self,
        mock_garmin_client,
        mock_health_raw,
        mock_parse,
        mock_send_dm,
    ):
        """Before 12pm with no data: silent DEBUG retry — no INFO log, no DM."""
        mock_parse.return_value = _make_empty_snapshot()

        athlete = _make_athlete()
        db = _make_db()
        slack_client = MagicMock()

        # 10:00am — before the 12pm cutoff
        fake_local = datetime(TODAY.year, TODAY.month, TODAY.day, 10, 0, tzinfo=timezone.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt, \
             patch(f"{_ADAPTER}.logger") as mock_logger:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            from running_coach_ai.coach.adapter import run_morning_checkin
            run_morning_checkin(athlete, db)

            # Must NOT emit an INFO "skipping" log — that only fires at/after noon
            info_calls = [str(c) for c in mock_logger.info.call_args_list]
            assert not any("skipping" in msg.lower() for msg in info_calls), \
                f"Unexpected skip INFO log before noon: {mock_logger.info.call_args_list}"

        mock_send_dm.assert_not_called()
        assert athlete.last_morning_checkin_date != TODAY

    @patch("running_coach_ai.coach.notify.upsert_morning_checkin")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_info_skip_log_at_noon(
        self,
        mock_garmin_client,
        mock_health_raw,
        mock_parse,
        mock_send_dm,
    ):
        """At/after 12pm with no data: INFO 'skipping' log emitted, no DM sent."""
        mock_parse.return_value = _make_empty_snapshot()

        athlete = _make_athlete()
        db = _make_db()
        slack_client = MagicMock()

        # Exactly noon — at the cutoff
        fake_local = datetime(TODAY.year, TODAY.month, TODAY.day, 12, 0, tzinfo=timezone.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt, \
             patch(f"{_ADAPTER}.logger") as mock_logger:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            from running_coach_ai.coach.adapter import run_morning_checkin
            run_morning_checkin(athlete, db)

            info_calls = [str(c) for c in mock_logger.info.call_args_list]
            assert any("skipping" in msg.lower() for msg in info_calls), \
                f"Expected INFO skip log at noon, got: {mock_logger.info.call_args_list}"

        mock_send_dm.assert_not_called()
        assert athlete.last_morning_checkin_date != TODAY

    @patch("running_coach_ai.coach.notify.upsert_morning_checkin")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_no_dm_after_noon(
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

        # 13:00 — well past the noon cutoff
        fake_local = datetime(TODAY.year, TODAY.month, TODAY.day, 13, 0, tzinfo=timezone.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            from running_coach_ai.coach.adapter import run_morning_checkin
            run_morning_checkin(athlete, db)

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
        run_morning_checkin(athlete, db)

        mock_garmin_client.assert_not_called()

    @patch("running_coach_ai.coach.notify.upsert_morning_checkin")
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_no_dm_when_already_sent_today(self, mock_garmin_client, mock_send_dm):
        athlete = _make_athlete(last_checkin_date=TODAY)
        db = _make_db()
        slack_client = MagicMock()

        from running_coach_ai.coach.adapter import run_morning_checkin
        run_morning_checkin(athlete, db)

        mock_send_dm.assert_not_called()


def test_should_wait_when_fetch_fails_before_noon():
    """Garmin fetch failure before noon → still wait (let the 30-min retry fire)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    from running_coach_ai.coach.adapter import _should_wait_for_morning_data

    athlete = Mock(garmin_email="user@example.com")
    snapshot = None
    now_local = datetime(2026, 5, 21, 6, 30, tzinfo=ZoneInfo("America/New_York"))

    assert _should_wait_for_morning_data(
        snapshot, garmin_fetch_failed=True, athlete=athlete, now_local=now_local
    ) is True


def test_should_not_wait_when_fetch_fails_after_noon():
    """Garmin fetch failure after noon → proceed (no more retry budget)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    from running_coach_ai.coach.adapter import _should_wait_for_morning_data

    athlete = Mock(garmin_email="user@example.com")
    snapshot = None
    now_local = datetime(2026, 5, 21, 12, 30, tzinfo=ZoneInfo("America/New_York"))

    assert _should_wait_for_morning_data(
        snapshot, garmin_fetch_failed=True, athlete=athlete, now_local=now_local
    ) is False


def test_should_not_wait_when_no_garmin_email_regardless_of_clock():
    """Athletes with no Garmin credentials never wait — there's no data source to poll."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    from running_coach_ai.coach.adapter import _should_wait_for_morning_data

    athlete = Mock(garmin_email=None)
    snapshot = None
    now_local = datetime(2026, 5, 21, 6, 30, tzinfo=ZoneInfo("America/New_York"))

    assert _should_wait_for_morning_data(
        snapshot, garmin_fetch_failed=False, athlete=athlete, now_local=now_local
    ) is False


def test_noon_no_data_writes_no_report_notification(monkeypatch):
    """After noon, with NO snapshot at all, fire a 'no morning report today'
    notification with morning_snapshot_date=None."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import patch

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from werkzeug.security import generate_password_hash

    from running_coach_ai.coach.adapter import run_morning_checkin
    from running_coach_ai.database.models import Athlete, Base, Notification

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    athlete = Athlete(
        name="Sam Runner",
        web_username="sam",
        web_password_hash=generate_password_hash("pw"),
        is_admin=False,
        onboarding_complete=True,
        allowed=True,
        coach_key="classic",
        timezone="America/New_York",
        garmin_email=None,  # no credentials → gate returns False, falls through
    )
    db.add(athlete)
    db.commit()
    db.refresh(athlete)

    fake_now = datetime(2026, 5, 21, 12, 30, tzinfo=ZoneInfo("America/New_York"))

    import running_coach_ai.coach.adapter as adapter_mod
    real_datetime = adapter_mod.datetime

    class _FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz else fake_now.replace(tzinfo=None)

    try:
        with patch.object(adapter_mod, "datetime", _FakeDatetime):
            run_morning_checkin(athlete, db)
    finally:
        notif = db.query(Notification).filter(
            Notification.athlete_id == athlete.id,
            Notification.kind == "morning_checkin",
        ).first()
        db.close()
        engine.dispose()

    assert notif is not None
    assert notif.morning_snapshot_date is None
    assert "No morning report" in notif.body
