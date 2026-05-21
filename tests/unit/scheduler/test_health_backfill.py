"""Tests for the health-backfill job's morning-checkin re-fire trigger."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from running_coach_ai.database.models import (
    Athlete,
    Base,
    HealthSnapshot,
    Notification,
)


@pytest.fixture()
def db_with_athlete():
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
        garmin_email="sam@example.com",
        garmin_password_encrypted=b"enc",
    )
    db.add(athlete)
    db.commit()
    db.refresh(athlete)
    try:
        yield db, athlete
    finally:
        db.close()
        engine.dispose()


def test_health_backfill_refires_when_stale_morning_notif_exists(db_with_athlete):
    """A morning_checkin notification with morning_snapshot_date < today gets
    re-fired after backfill writes today's snapshot."""
    db, athlete = db_with_athlete
    today = datetime.now(ZoneInfo("America/New_York")).date()
    yesterday = today - timedelta(days=1)

    # Seed a stale-based morning_checkin notification
    stale_notif = Notification(
        athlete_id=athlete.id,
        kind="morning_checkin",
        title="Morning check-in",
        body="Stale body referencing HRV 40",
        morning_snapshot_date=yesterday,
        created_at=datetime.utcnow(),
    )
    db.add(stale_notif)
    db.commit()

    # No complete HealthSnapshot for today — backfill will fetch and write one.
    # parse_health_snapshot is mocked so nothing is actually inserted into DB,
    # but the stale-notification check runs regardless.
    fresh_snap = HealthSnapshot(
        athlete_id=athlete.id, date=today,
        hrv_score=55, hrv_status="balanced",
        sleep_score=85, sleep_duration_seconds=int(7.4 * 3600),
        resting_hr=48, body_battery_start=78, body_battery_end=72,
    )

    from contextlib import contextmanager

    @contextmanager
    def fake_get_session():
        yield db

    with patch(
        "running_coach_ai.scheduler.health_backfill.get_session", fake_get_session
    ), patch(
        "running_coach_ai.scheduler.health_backfill.get_garmin_client"
    ), patch(
        "running_coach_ai.scheduler.health_backfill.get_health_snapshot",
        return_value={},
    ), patch(
        "running_coach_ai.scheduler.health_backfill.parse_health_snapshot",
        return_value=fresh_snap,
    ), patch(
        "running_coach_ai.scheduler.health_backfill.run_morning_checkin"
    ) as mock_run:
        from running_coach_ai.scheduler.health_backfill import _run_health_backfill
        _run_health_backfill()

    assert mock_run.called, "Expected run_morning_checkin to be invoked"
    call = mock_run.call_args
    assert call.kwargs.get("force") is True or (len(call.args) >= 3 and call.args[2] is True)


def test_health_backfill_does_not_refire_when_today_snapshot_already_fresh(db_with_athlete):
    """No re-fire when morning_checkin notification already used today's snapshot."""
    db, athlete = db_with_athlete
    today = datetime.now(ZoneInfo("America/New_York")).date()

    fresh_notif = Notification(
        athlete_id=athlete.id,
        kind="morning_checkin",
        title="Morning check-in",
        body="Already fresh body",
        morning_snapshot_date=today,
        created_at=datetime.utcnow(),
    )
    db.add(fresh_notif)
    fresh_snap = HealthSnapshot(
        athlete_id=athlete.id, date=today,
        hrv_score=55, hrv_status="balanced",
        sleep_score=85, sleep_duration_seconds=int(7.4 * 3600),
        resting_hr=48, body_battery_start=78,
    )
    db.add(fresh_snap)
    db.commit()

    from contextlib import contextmanager

    @contextmanager
    def fake_get_session():
        yield db

    with patch(
        "running_coach_ai.scheduler.health_backfill.get_session", fake_get_session
    ), patch(
        "running_coach_ai.scheduler.health_backfill.run_morning_checkin"
    ) as mock_run:
        from running_coach_ai.scheduler.health_backfill import _run_health_backfill
        _run_health_backfill()

    assert not mock_run.called, "run_morning_checkin should NOT have been invoked"
