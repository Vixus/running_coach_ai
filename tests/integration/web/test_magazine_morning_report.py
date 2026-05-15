"""Integration test: /api/magazine surfaces today's morning check-in.

This is the regression test for the recurring bug where the home screen
keeps showing the persona's static greeting ("Morning. Let's see what the
data says today.") even after the morning_checkin notification has fired.

The test exercises the real Flask route against an in-memory SQLite DB so
it would catch:
  * coach.message being hardcoded to persona.greeting
  * the freshness gate using a server-local date that doesn't match the
    athlete's local-tz check-in date
  * the response dropping the body or the source label
"""

import pytest
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch
from werkzeug.security import generate_password_hash

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from running_coach_ai.database.models import (
    Athlete,
    Base,
    HealthSnapshot,
    Notification,
)


@pytest.fixture()
def app_and_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    athlete = Athlete(
        name="Sam",
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

    @contextmanager
    def fake_get_session():
        yield db

    from flask import Flask
    from running_coach_ai.web.api.magazine import bp as mag_bp

    flask_app = Flask(__name__)
    flask_app.config["SECRET_KEY"] = "test-mag"
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(mag_bp)

    with patch(
        "running_coach_ai.web.api.magazine.get_session", fake_get_session
    ):
        yield flask_app, db, athlete

    db.close()
    engine.dispose()


def _client_with_session(flask_app, athlete_id):
    client = flask_app.test_client()
    with client.session_transaction() as sess:
        sess["athlete_id"] = athlete_id
    return client


def _make_morning_notif(athlete_id, body, created_at):
    return Notification(
        athlete_id=athlete_id,
        kind="morning_checkin",
        title="Morning check-in",
        body=body,
        action_path="/#morning",
        created_at=created_at,
    )


# ── Unit tests for the helper ───────────────────────────────────────────────


def test_excerpt_takes_first_two_sentences():
    from running_coach_ai.web.api.magazine import _excerpt_first_sentences

    body = (
        "Morning, Sam. Today is an easy 5-miler at zone 2. "
        "Weather's cool — perfect running conditions. "
        "Focus on cadence above 180."
    )
    excerpt = _excerpt_first_sentences(body, n=2)
    assert excerpt == (
        "Morning, Sam. Today is an easy 5-miler at zone 2."
    )


def test_excerpt_handles_no_sentence_terminators():
    from running_coach_ai.web.api.magazine import _excerpt_first_sentences

    assert _excerpt_first_sentences("just a fragment") == "just a fragment"


def test_excerpt_handles_empty_input():
    from running_coach_ai.web.api.magazine import _excerpt_first_sentences

    assert _excerpt_first_sentences("") == ""
    assert _excerpt_first_sentences(None) == ""
    assert _excerpt_first_sentences("   \n\n  ") == ""


# ── Integration tests against the real endpoint ─────────────────────────────


def test_magazine_surfaces_recent_morning_checkin(app_and_db):
    """The whole point: a fresh morning_checkin notification should show up
    as coach.message on the home card. This is the regression we keep hitting."""
    flask_app, db, athlete = app_and_db
    db.add(
        _make_morning_notif(
            athlete.id,
            body=(
                "Morning, Sam — HRV's holding steady at 62. "
                "You've got 6mi easy on tap today. "
                "Keep cadence above 180 and call it a win."
            ),
            created_at=datetime.utcnow() - timedelta(minutes=15),
        )
    )
    db.commit()

    client = _client_with_session(flask_app, athlete.id)
    resp = client.get("/api/magazine")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()

    assert data["coach"]["message_source"] == "morning_checkin"
    assert data["coach"]["message"].startswith("Morning, Sam")
    assert "easy on tap today" in data["coach"]["message"]
    # The third sentence ("Keep cadence...") is intentionally trimmed —
    # the home card is a single-line serif quote.
    assert "cadence above 180" not in data["coach"]["message"]
    assert data["coach"]["message_at"] is not None


def test_magazine_falls_back_to_persona_greeting_when_no_notification(
    app_and_db,
):
    flask_app, db, athlete = app_and_db
    client = _client_with_session(flask_app, athlete.id)

    resp = client.get("/api/magazine")
    assert resp.status_code == 200
    data = resp.get_json()

    assert data["coach"]["message_source"] == "persona_greeting"
    assert data["coach"]["message_at"] is None
    # Classic persona's greeting
    assert "Morning" in data["coach"]["message"]


def test_magazine_ignores_stale_morning_checkin(app_and_db):
    """A notification older than 24h must NOT bleed through to the home card —
    that was the original 'showing yesterday's data' bug."""
    flask_app, db, athlete = app_and_db
    db.add(
        _make_morning_notif(
            athlete.id,
            body="Yesterday's morning report. Do not surface this.",
            created_at=datetime.utcnow() - timedelta(hours=30),
        )
    )
    db.commit()

    client = _client_with_session(flask_app, athlete.id)
    resp = client.get("/api/magazine")
    assert resp.status_code == 200
    data = resp.get_json()

    assert data["coach"]["message_source"] == "persona_greeting"
    assert "Do not surface this" not in (data["coach"]["message"] or "")


def test_magazine_prefers_most_recent_morning_checkin(app_and_db):
    """If multiple morning_checkin notifications exist in the freshness window,
    the most recent wins — covers backfill / retry scenarios."""
    flask_app, db, athlete = app_and_db
    db.add(
        _make_morning_notif(
            athlete.id,
            body="Older check-in body.",
            created_at=datetime.utcnow() - timedelta(hours=10),
        )
    )
    db.add(
        _make_morning_notif(
            athlete.id,
            body="Newer check-in body. With extra context.",
            created_at=datetime.utcnow() - timedelta(minutes=5),
        )
    )
    db.commit()

    client = _client_with_session(flask_app, athlete.id)
    resp = client.get("/api/magazine")
    data = resp.get_json()

    assert data["coach"]["message_source"] == "morning_checkin"
    assert data["coach"]["message"].startswith("Newer check-in body.")


def test_magazine_ignores_other_notification_kinds(app_and_db):
    """post_run_feedback, weekly_review, system etc. must not get picked up
    as the morning report — only kind='morning_checkin' qualifies."""
    flask_app, db, athlete = app_and_db
    db.add(
        Notification(
            athlete_id=athlete.id,
            kind="post_run_feedback",
            title="Post-run feedback",
            body="Great run! This is not a morning report.",
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
    )
    db.add(
        Notification(
            athlete_id=athlete.id,
            kind="weekly_review",
            title="Weekly review",
            body="Week summary — also not a morning report.",
            created_at=datetime.utcnow() - timedelta(minutes=5),
        )
    )
    db.commit()

    client = _client_with_session(flask_app, athlete.id)
    resp = client.get("/api/magazine")
    data = resp.get_json()

    assert data["coach"]["message_source"] == "persona_greeting"
    assert "Great run" not in (data["coach"]["message"] or "")
    assert "Week summary" not in (data["coach"]["message"] or "")


# ── Health-snapshot fallback (Garmin 429 → no today snapshot) ───────────────


def _today():
    """Athlete-local today, matching the magazine endpoint's tz handling.

    The endpoint now computes `today` from `athlete.timezone` (falling back to
    America/New_York) so that the page doesn't roll over to "tomorrow" the
    moment server UTC crosses midnight. The fixture pins athlete tz to
    America/New_York, so we mirror that here.
    """
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _Z
    return _dt.now(_Z("America/New_York")).date()


def test_magazine_returns_todays_health_snapshot_when_available(app_and_db):
    flask_app, db, athlete = app_and_db
    today = _today()
    db.add(
        HealthSnapshot(
            athlete_id=athlete.id,
            date=today,
            hrv_score=55,
            hrv_status="balanced",
            body_battery_start=80,
            body_battery_end=70,
            sleep_duration_seconds=7 * 3600,
            sleep_score=82,
            resting_hr=48,
        )
    )
    db.commit()

    client = _client_with_session(flask_app, athlete.id)
    data = client.get("/api/magazine").get_json()

    assert data["health"] is not None
    assert data["health"]["hrv"] == 55
    assert data["health"]["resting_hr"] == 48
    assert data["health"]["date_iso"] == today.isoformat()
    assert data["health"]["is_stale"] is False


def test_magazine_falls_back_to_recent_health_snapshot_when_today_missing(
    app_and_db,
):
    """The regression: Garmin 429s, no HealthSnapshot for today, home card
    shows "—" everywhere even though yesterday's data is fine. We should
    fall back to the most recent snapshot within 3 days and flag it stale."""
    flask_app, db, athlete = app_and_db
    today = _today()
    yesterday = today - timedelta(days=1)
    db.add(
        HealthSnapshot(
            athlete_id=athlete.id,
            date=yesterday,
            hrv_score=40,
            hrv_status="balanced",
            body_battery_end=84,
            sleep_duration_seconds=int(7.5 * 3600),
            resting_hr=52,
        )
    )
    db.commit()

    client = _client_with_session(flask_app, athlete.id)
    data = client.get("/api/magazine").get_json()

    assert data["health"] is not None, "health block must not be null"
    assert data["health"]["hrv"] == 40
    assert data["health"]["body_battery"] == 84
    assert data["health"]["resting_hr"] == 52
    assert data["health"]["sleep_hours"] == 7.5
    assert data["health"]["date_iso"] == yesterday.isoformat()
    assert data["health"]["is_stale"] is True


def test_magazine_skips_snapshots_older_than_3_days(app_and_db):
    """Anything older than 3 days is not surfaced — better to show "—" than
    HRV from a week ago."""
    flask_app, db, athlete = app_and_db
    today = _today()
    db.add(
        HealthSnapshot(
            athlete_id=athlete.id,
            date=today - timedelta(days=5),
            hrv_score=99,
            resting_hr=99,
        )
    )
    db.commit()

    client = _client_with_session(flask_app, athlete.id)
    data = client.get("/api/magazine").get_json()

    assert data["health"] is None


def test_magazine_prefers_today_over_recent_when_both_exist(app_and_db):
    flask_app, db, athlete = app_and_db
    today = _today()
    db.add(
        HealthSnapshot(
            athlete_id=athlete.id,
            date=today - timedelta(days=1),
            hrv_score=40,
            resting_hr=52,
        )
    )
    db.add(
        HealthSnapshot(
            athlete_id=athlete.id,
            date=today,
            hrv_score=60,
            resting_hr=50,
        )
    )
    db.commit()

    client = _client_with_session(flask_app, athlete.id)
    data = client.get("/api/magazine").get_json()

    assert data["health"]["hrv"] == 60
    assert data["health"]["is_stale"] is False
    assert data["health"]["date_iso"] == today.isoformat()


def test_magazine_isolates_morning_checkin_per_athlete(app_and_db):
    """A different athlete's morning check-in must NOT show up on this
    athlete's home card."""
    flask_app, db, athlete = app_and_db
    other = Athlete(
        name="Other",
        web_username="other",
        web_password_hash=generate_password_hash("pw"),
        onboarding_complete=True,
        allowed=True,
        coach_key="classic",
    )
    db.add(other)
    db.commit()
    db.refresh(other)

    db.add(
        _make_morning_notif(
            other.id,
            body="This belongs to the other athlete.",
            created_at=datetime.utcnow() - timedelta(minutes=5),
        )
    )
    db.commit()

    client = _client_with_session(flask_app, athlete.id)
    resp = client.get("/api/magazine")
    data = resp.get_json()

    assert data["coach"]["message_source"] == "persona_greeting"
    assert "other athlete" not in (data["coach"]["message"] or "")
