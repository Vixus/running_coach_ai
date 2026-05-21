"""T080: Unit tests for morning check-in same-day deduplication guard.

Verifies that even when two 30-minute interval ticks fire in quick succession
with health data present on both, the athlete receives exactly one DM and the
second tick is a DB-guarded no-op (no Garmin call, no Claude call, no DM).
"""

import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from running_coach_ai.database.models import Athlete, Base


@pytest.fixture()
def in_memory_db():
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
    )
    db.add(athlete)
    db.commit()
    db.refresh(athlete)
    try:
        yield db, athlete
    finally:
        db.close()
        engine.dispose()


_ADAPTER = "running_coach_ai.coach.adapter"
TODAY = date.today()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_athlete(*, tz="America/New_York"):
    a = MagicMock()
    a.id = 1
    a.name = "Simon"
    a.timezone = tz
    a.garmin_email = "simon@example.com"
    a.garmin_password_encrypted = b"enc"
    a.home_lat = 40.7
    a.home_lon = -74.0
    a.last_morning_checkin_date = None  # not yet sent today
    return a


def _make_snapshot():
    s = MagicMock()
    s.date = TODAY
    s.sleep_score = 75
    s.hrv_score = 42
    s.hrv_status = "BALANCED"
    s.body_battery_start = 87
    s.resting_hr = 51
    s.stress_avg = 19
    s.training_readiness = 75  # primary morning-data-complete signal
    return s


def _patched_run(athlete, db, slack_client):
    """Run morning check-in with time pinned to a daytime hour so the
    06:00 local floor doesn't return early."""
    from datetime import datetime as _dt, timezone as _tz
    from running_coach_ai.coach.adapter import run_morning_checkin
    fake_local = _dt(TODAY.year, TODAY.month, TODAY.day, 8, 0, tzinfo=_tz.utc)
    with patch(f"{_ADAPTER}.datetime") as mock_dt:
        mock_dt.now.return_value = fake_local
        mock_dt.side_effect = lambda *args, **kwargs: _dt(*args, **kwargs)
        run_morning_checkin(athlete, db)


def _make_db():
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    db.query.return_value.filter.return_value.first.return_value = None
    return db


# ---------------------------------------------------------------------------
# Deduplication race-condition tests
# ---------------------------------------------------------------------------

class TestMorningCheckinDedup:
    """Athlete receives exactly one DM even if two ticks fire with data present."""

    @patch(f"{_ADAPTER}.extract_and_apply_plan", return_value="Morning message")
    @patch(f"{_ADAPTER}.call_claude", return_value="Morning message")
    @patch(f"{_ADAPTER}.scoped_query")
    @patch("running_coach_ai.coach.notify.upsert_morning_checkin")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    @patch("running_coach_ai.weather.client.summarise_forecast", return_value=[])
    @patch("running_coach_ai.weather.client.get_forecast", return_value={})
    def test_exactly_one_dm_on_two_rapid_ticks(
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
        """Tick 1: sends DM + sets last_morning_checkin_date.
        Tick 2: dedup guard fires immediately, no DM sent.
        """
        snapshot = _make_snapshot()
        mock_parse.return_value = snapshot
        mock_scoped.return_value.filter.return_value.first.return_value = None

        athlete = _make_athlete()
        db = _make_db()
        slack_client = MagicMock()

        # --- Tick 1: health data present, sends DM ---
        _patched_run(athlete, db, slack_client)

        assert mock_send_dm.call_count == 1, "Expected exactly 1 DM on first tick"
        assert athlete.last_morning_checkin_date == TODAY

        # --- Tick 2: dedup guard must fire (no Garmin call, no DM) ---
        garmin_calls_before = mock_garmin_client.call_count
        _patched_run(athlete, db, slack_client)

        assert mock_send_dm.call_count == 1, (
            "Expected DM count to remain 1 after second tick; dedup guard failed"
        )
        assert mock_garmin_client.call_count == garmin_calls_before, (
            "Garmin client should not be called on the second tick (dedup guard fired first)"
        )

    @patch(f"{_ADAPTER}.extract_and_apply_plan", return_value="Morning message")
    @patch(f"{_ADAPTER}.call_claude", return_value="Morning message")
    @patch(f"{_ADAPTER}.scoped_query")
    @patch("running_coach_ai.coach.notify.upsert_morning_checkin")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    @patch("running_coach_ai.weather.client.summarise_forecast", return_value=[])
    @patch("running_coach_ai.weather.client.get_forecast", return_value={})
    def test_commit_called_exactly_once(
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
        """db.commit() is called once (after the DM send), not on the second tick."""
        snapshot = _make_snapshot()
        mock_parse.return_value = snapshot
        mock_scoped.return_value.filter.return_value.first.return_value = None

        athlete = _make_athlete()
        db = _make_db()
        slack_client = MagicMock()

        from running_coach_ai.coach.adapter import run_morning_checkin

        _patched_run(athlete, db, slack_client)  # Tick 1
        commit_count_after_tick1 = db.commit.call_count

        _patched_run(athlete, db, slack_client)  # Tick 2

        assert commit_count_after_tick1 >= 1, "commit() must be called after first tick"
        assert db.commit.call_count == commit_count_after_tick1, (
            "commit() must not be called on the second (no-op) tick"
        )

    @patch(f"{_ADAPTER}.extract_and_apply_plan", return_value="Morning message")
    @patch(f"{_ADAPTER}.call_claude", return_value="Morning message")
    @patch(f"{_ADAPTER}.scoped_query")
    @patch("running_coach_ai.coach.notify.upsert_morning_checkin")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    @patch("running_coach_ai.weather.client.summarise_forecast", return_value=[])
    @patch("running_coach_ai.weather.client.get_forecast", return_value={})
    def test_claude_not_called_on_second_tick(
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
        """Claude must not be called on the second tick."""
        snapshot = _make_snapshot()
        mock_parse.return_value = snapshot
        mock_scoped.return_value.filter.return_value.first.return_value = None

        athlete = _make_athlete()
        db = _make_db()
        slack_client = MagicMock()

        from running_coach_ai.coach.adapter import run_morning_checkin

        _patched_run(athlete, db, slack_client)  # Tick 1
        claude_calls_after_tick1 = mock_claude.call_count

        _patched_run(athlete, db, slack_client)  # Tick 2

        assert mock_claude.call_count == claude_calls_after_tick1, (
            "Claude must not be called on the second (no-op) tick"
        )


# ---------------------------------------------------------------------------
# upsert_morning_checkin tests (Task 3)
# ---------------------------------------------------------------------------

def test_upsert_morning_checkin_inserts_when_none_today(in_memory_db):
    """First call today writes a new Notification row."""
    from datetime import date
    from running_coach_ai.coach.notify import upsert_morning_checkin
    from running_coach_ai.database.models import Notification

    db, athlete = in_memory_db
    notif = upsert_morning_checkin(
        db, athlete,
        body="First morning report",
        morning_snapshot_date=date(2026, 5, 21),
        today_local=date(2026, 5, 21),
    )
    db.commit()

    assert notif.id is not None
    assert notif.body == "First morning report"
    assert notif.morning_snapshot_date == date(2026, 5, 21)

    rows = db.query(Notification).filter(
        Notification.athlete_id == athlete.id, Notification.kind == "morning_checkin"
    ).all()
    assert len(rows) == 1


def test_upsert_morning_checkin_updates_when_today_row_exists(in_memory_db):
    """Second call today UPDATES the same row — no second insert."""
    from datetime import date
    from running_coach_ai.coach.notify import upsert_morning_checkin
    from running_coach_ai.database.models import Notification

    db, athlete = in_memory_db
    first = upsert_morning_checkin(
        db, athlete,
        body="Stale-data body",
        morning_snapshot_date=date(2026, 5, 20),
        today_local=date(2026, 5, 21),
    )
    db.commit()
    first_id = first.id

    second = upsert_morning_checkin(
        db, athlete,
        body="Fresh-data body",
        morning_snapshot_date=date(2026, 5, 21),
        today_local=date(2026, 5, 21),
    )
    db.commit()

    assert second.id == first_id
    assert second.body == "Fresh-data body"
    assert second.morning_snapshot_date == date(2026, 5, 21)

    rows = db.query(Notification).filter(
        Notification.athlete_id == athlete.id, Notification.kind == "morning_checkin"
    ).all()
    assert len(rows) == 1


def test_upsert_morning_checkin_does_not_touch_yesterday_row(in_memory_db):
    """A morning_checkin from yesterday must NOT be overwritten by today's upsert."""
    from datetime import date, datetime, timedelta
    from running_coach_ai.coach.notify import upsert_morning_checkin
    from running_coach_ai.database.models import Notification

    db, athlete = in_memory_db
    yesterday_notif = Notification(
        athlete_id=athlete.id,
        kind="morning_checkin",
        title="Morning check-in",
        body="Yesterday's body",
        morning_snapshot_date=date(2026, 5, 20),
        created_at=datetime.utcnow() - timedelta(days=1),
    )
    db.add(yesterday_notif)
    db.commit()
    yesterday_id = yesterday_notif.id

    today_notif = upsert_morning_checkin(
        db, athlete,
        body="Today's body",
        morning_snapshot_date=date(2026, 5, 21),
        today_local=date(2026, 5, 21),
    )
    db.commit()

    assert today_notif.id != yesterday_id
    rows = db.query(Notification).filter(
        Notification.athlete_id == athlete.id, Notification.kind == "morning_checkin"
    ).order_by(Notification.created_at.asc()).all()
    assert len(rows) == 2
    assert rows[0].id == yesterday_id
    assert rows[0].body == "Yesterday's body"
