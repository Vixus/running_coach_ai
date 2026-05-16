"""Integration tests for GET /api/today across all 6 states.

Per spec 007:
  - US1 (PRE_RUN, P1) — T027–T033
  - US2 (COMPLETED, P1) — T039–T042
  - US3 (REST_DAY, P2) — T045–T046
  - US4 (RACE_DAY, P2) — T050–T051
  - US5 (NO_PLAN, P3) — T054
  - US6 (OFF_PLAN, P3) — T057–T058
  - Polish (observability) — T059–T060

Tests run against in-memory SQLite (FR-035). No Claude/Garmin calls.
"""

from __future__ import annotations

import pytest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from running_coach_ai.database.models import (
    Athlete,
    Base,
    CompletedWorkout,
    Goal,
    HealthSnapshot,
    Notification,
    PlannedWorkout,
    TrainingPlan,
    WebEvent,
)


# ─── Test harness ─────────────────────────────────────────────────────────


def _athlete_today(athlete) -> date:
    """Resolve today in the athlete's timezone, mirroring the endpoint."""
    tz = ZoneInfo(athlete.timezone or "America/New_York")
    return datetime.now(tz).date()


@pytest.fixture()
def app_and_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
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
        lthr_bpm=170,
    )
    db.add(athlete)
    db.commit()
    db.refresh(athlete)

    @contextmanager
    def fake_get_session():
        yield db

    from flask import Flask
    from running_coach_ai.web.api.today import bp as today_bp

    flask_app = Flask(__name__)
    flask_app.config["SECRET_KEY"] = "test-today"
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(today_bp)

    with patch(
        "running_coach_ai.web.api.today.get_session", fake_get_session
    ):
        yield flask_app, db, athlete

    db.close()
    engine.dispose()


def _client(flask_app, athlete_id):
    client = flask_app.test_client()
    with client.session_transaction() as s:
        s["athlete_id"] = athlete_id
    return client


def _seed_goal(db, athlete_id, race_date=None, active=True, race_name="Berlin Marathon"):
    goal = Goal(
        athlete_id=athlete_id,
        race_type="marathon",
        race_name=race_name,
        race_date=race_date or (date.today() + timedelta(days=120)),
        target_time_seconds=12000,
        experience_level="intermediate",
        training_days_per_week=5,
        active=active,
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


def _seed_plan(db, athlete_id, goal_id, weeks_ago=2):
    plan = TrainingPlan(
        athlete_id=athlete_id,
        goal_id=goal_id,
        valid_from=date.today() - timedelta(weeks=weeks_ago),
        valid_to=date.today() + timedelta(weeks=14),
        plan_json={},
        current_phase="build",
        active=True,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _seed_planned_workout(
    db,
    athlete_id,
    plan_id,
    scheduled_date,
    workout_type="tempo",
    distance_km=9.66,        # ~6 mi
    pace_min_per_km=4.81,    # ~7:45/mi
    duration_s=None,
    garmin_workout_id=None,
    description="3 mi warm-up · 3 mi tempo · cool-down",
):
    pw = PlannedWorkout(
        plan_id=plan_id,
        athlete_id=athlete_id,
        scheduled_date=scheduled_date,
        workout_type=workout_type,
        workout_name=None,
        description=description,
        target_distance_km=distance_km,
        target_duration_seconds=duration_s,
        target_pace_min_per_km=pace_min_per_km,
        status="planned",
        garmin_workout_id=garmin_workout_id,
    )
    db.add(pw)
    db.commit()
    db.refresh(pw)
    return pw


def _seed_health_snapshot(
    db, athlete_id, on_date, hrv=62, hrv_status="balanced", sleep_h=7.4, bb=78, rhr=48,
):
    snap = HealthSnapshot(
        athlete_id=athlete_id,
        date=on_date,
        hrv_score=hrv,
        hrv_status=hrv_status,
        sleep_score=85,
        sleep_duration_seconds=int(sleep_h * 3600) if sleep_h else None,
        resting_hr=rhr,
        body_battery_start=bb,
        body_battery_end=bb - 10 if bb else None,
        stress_avg=22,
    )
    db.add(snap)
    db.commit()
    return snap


def _seed_morning_notif(db, athlete_id, body, created_at=None):
    notif = Notification(
        athlete_id=athlete_id,
        kind="morning_checkin",
        title="Morning check-in",
        body=body,
        action_path="/#morning",
        created_at=created_at or datetime.utcnow(),
    )
    db.add(notif)
    db.commit()
    return notif


def _seed_completed(
    db,
    athlete_id,
    on_date,
    planned_workout_id=None,
    distance_km=9.8,
    pace_min_per_km=4.79,
    avg_hr=158,
    training_load=87.0,
    coach_analysis=None,
    activity_id_suffix="t",
):
    cw = CompletedWorkout(
        athlete_id=athlete_id,
        planned_workout_id=planned_workout_id,
        garmin_activity_id=f"gact-{athlete_id}-{on_date.isoformat()}-{activity_id_suffix}",
        activity_type="running",
        date=on_date,
        distance_km=distance_km,
        duration_seconds=2820,
        avg_pace_min_per_km=pace_min_per_km,
        avg_hr=avg_hr,
        max_hr=172,
        training_load=training_load,
        coach_analysis=coach_analysis,
    )
    db.add(cw)
    db.commit()
    db.refresh(cw)
    return cw


# ═══════════════════════════════════════════════════════════════════════════
# US1 — PRE_RUN  (T027–T031)
# ═══════════════════════════════════════════════════════════════════════════


def test_pre_run_state_with_morning_checkin(app_and_db):
    """T027 — Active Goal + PlannedWorkout(tempo) + HealthSnapshot + morning_checkin
    Notification → state=PRE_RUN, rationale.source=morning_checkin, 4 cover_lines."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(db, athlete.id, plan.id, today, workout_type="tempo")
    _seed_health_snapshot(db, athlete.id, today)
    _seed_morning_notif(
        db, athlete.id,
        body=(
            "**Today.** This tempo locks in race pace before next week's volume jump — "
            "keep the segments even and don't get greedy in mile 4.\n\n"
            "Sam, HRV is up 2 and you slept solid, so the system is green-lit. "
            "Threshold work like this is what holds your goal race pace under fatigue.\n\n"
            "Weather: cool, 12°C. Wind from the south."
        ),
    )

    resp = _client(app, athlete.id).get("/api/today")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()

    assert data["state"] == "PRE_RUN"
    assert data["rationale"]["source"] == "morning_checkin"
    # Today Card surfaces only the **Today.** tagline — short, punchy.
    assert "tempo locks in race pace" in data["rationale"]["text"]
    # Tagline only — the longer readiness paragraph stays in the morning report
    assert "HRV is up 2" not in data["rationale"]["text"]
    assert "Weather" not in data["rationale"]["text"]
    # And no leftover markdown asterisks
    assert "**" not in data["rationale"]["text"]
    assert data["rationale"]["coach"] == "Coach Alex"
    assert data["rationale"]["accent_color"].startswith("#")
    assert data["headline"]["eyebrow"] == "Tempo"
    assert data["headline"]["title"]  # e.g. "6 mi @ 7:45"
    assert data["modifiers"]["on_watch"] is False
    assert data["modifiers"]["is_bonus"] is False
    assert isinstance(data["cover_lines"], list)
    assert len(data["cover_lines"]) == 4
    drill_targets = {c["drill_to"] for c in data["cover_lines"]}
    assert drill_targets == {"morning"}
    # Headline + rationale chat prompts wired
    assert data["actions"]["headline_chat_prompt"] == "Tell me about today's workout."
    assert data["actions"]["rationale_chat_prompt"] == "I have a question about today's plan."
    assert data["actions"]["cta"] is None


def test_pre_run_rationale_rule_based_when_no_notification(app_and_db):
    """T028 — No morning_checkin Notification + after-7am-local → rationale.source=rule_based.

    Forces "after 7am" by mocking datetime in the endpoint module.
    """
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(db, athlete.id, plan.id, today, workout_type="tempo")
    _seed_health_snapshot(db, athlete.id, today)

    # Patch the endpoint's view of "now" to 10am athlete-local
    import running_coach_ai.web.api.today as today_mod

    real_datetime = today_mod.datetime

    class _FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            base = real_datetime(today.year, today.month, today.day, 10, 0, 0)
            return base.replace(tzinfo=tz) if tz else base

    with patch.object(today_mod, "datetime", _FakeDatetime):
        resp = _client(app, athlete.id).get("/api/today")
    assert resp.status_code == 200
    data = resp.get_json()

    assert data["state"] == "PRE_RUN"
    assert data["rationale"]["source"] == "rule_based"
    # Athlete first name AND workout type MUST appear (FR-008 — the WHY of the run)
    assert "Sam" in data["rationale"]["text"]
    assert "tempo" in data["rationale"]["text"].lower()


def test_pre_run_rationale_placeholder_before_7am(app_and_db):
    """T028 (part 2) — no morning_checkin + before 7am local → placeholder."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(db, athlete.id, plan.id, today, workout_type="easy")

    import running_coach_ai.web.api.today as today_mod
    real_datetime = today_mod.datetime

    class _FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            base = real_datetime(today.year, today.month, today.day, 5, 30, 0)
            return base.replace(tzinfo=tz) if tz else base

    with patch.object(today_mod, "datetime", _FakeDatetime):
        resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()

    assert data["state"] == "PRE_RUN"
    assert data["rationale"]["source"] == "placeholder"
    assert "checking in soon" in data["rationale"]["text"].lower()


def test_pre_run_on_watch_badge_when_synced(app_and_db):
    """T029 — PlannedWorkout.garmin_workout_id non-null → modifiers.on_watch=true."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(
        db, athlete.id, plan.id, today, garmin_workout_id="g-12345"
    )
    _seed_health_snapshot(db, athlete.id, today)

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "PRE_RUN"
    assert data["modifiers"]["on_watch"] is True


def test_pre_run_cover_lines_stale_fallback(app_and_db):
    """T029 (cover-lines) — no today HealthSnapshot but yesterday's exists → stale=true."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(db, athlete.id, plan.id, today, workout_type="easy")
    yesterday = today - timedelta(days=1)
    _seed_health_snapshot(db, athlete.id, yesterday, hrv=55)

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["cover_lines"][0]["value"] == 55
    assert data["cover_lines"][0]["is_stale"] is True


# ═══════════════════════════════════════════════════════════════════════════
# US2 — COMPLETED  (T039–T042)
# ═══════════════════════════════════════════════════════════════════════════


def test_completed_state_with_coach_analysis(app_and_db):
    """T039 — CompletedWorkout(planned_workout_id=N, coach_analysis=...) → COMPLETED,
    rationale.source=coach_analysis, modifiers.is_bonus=false, cover_lines drill_to=last_run."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    pw = _seed_planned_workout(db, athlete.id, plan.id, today, workout_type="tempo")
    _seed_completed(
        db, athlete.id, today,
        planned_workout_id=pw.id,
        coach_analysis=(
            "Sam, you held 7:42 average through the tempo blocks with HR sitting right "
            "where I wanted it — that's a clean threshold session.\n\nThe data shows "
            "negative split in mile 4 — aerobic ceiling still climbing."
        ),
    )

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "COMPLETED"
    assert data["rationale"]["source"] == "coach_analysis"
    assert "threshold session" in data["rationale"]["text"]
    assert "negative split" not in data["rationale"]["text"]   # paragraph 2 excluded
    assert data["modifiers"]["is_bonus"] is False
    assert data["headline"]["ribbon"] == "Completed"
    # Cover lines: Distance / Pace / Avg HR / Training Load → drill_to=last_run
    drills = {c["drill_to"] for c in data["cover_lines"]}
    assert drills == {"last_run"}
    assert any("mi" in str(c["value"]) for c in data["cover_lines"])
    assert data["actions"]["headline_chat_prompt"] == "How did today's run go?"


def test_completed_state_bonus_run(app_and_db):
    """T040 — CompletedWorkout with planned_workout_id=null → is_bonus=true,
    ribbon = 'Bonus Run — not on plan'."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    _seed_goal(db, athlete.id)   # active Goal so it doesn't roll to NO_PLAN
    _seed_completed(db, athlete.id, today, planned_workout_id=None, coach_analysis="")

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "COMPLETED"
    assert data["modifiers"]["is_bonus"] is True
    assert "Bonus Run" in data["headline"]["ribbon"]


def test_completed_rationale_rule_based_when_coach_analysis_empty(app_and_db):
    """T041 — coach_analysis is empty → rule_based fallback used."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    pw = _seed_planned_workout(db, athlete.id, plan.id, today, workout_type="easy")
    _seed_completed(db, athlete.id, today, planned_workout_id=pw.id, coach_analysis=None)

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "COMPLETED"
    assert data["rationale"]["source"] == "rule_based"
    assert "Sam" in data["rationale"]["text"]


# ═══════════════════════════════════════════════════════════════════════════
# US3 — REST_DAY  (T045–T046)
# ═══════════════════════════════════════════════════════════════════════════


def test_rest_day_state(app_and_db):
    """T045 — PlannedWorkout(workout_type='rest') + morning_checkin →
    state=REST_DAY, headline 'Recovery is the workout'."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(
        db, athlete.id, plan.id, today,
        workout_type="rest",
        distance_km=None, pace_min_per_km=None,
        description="Recovery day",
    )
    _seed_health_snapshot(db, athlete.id, today)
    _seed_morning_notif(
        db, athlete.id,
        body=(
            "**Today.** Take the day fully off — recovery is the workout.\n\n"
            "Sam, HRV is sitting at 62 ms and sleep was 7.4 hours; the system is "
            "absorbing the load, not fighting it."
        ),
    )

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "REST_DAY"
    assert data["headline"]["title"] == "Recovery is the workout"
    assert data["rationale"]["source"] == "morning_checkin"
    drills = {c["drill_to"] for c in data["cover_lines"]}
    assert drills == {"morning"}


def test_rest_day_transitions_to_completed_on_bonus_run(app_and_db):
    """T046 — REST_DAY + impromptu CompletedWorkout → state=COMPLETED with is_bonus=true.
    Verifies state-resolution precedence (FR-003): COMPLETED beats REST_DAY."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(
        db, athlete.id, plan.id, today,
        workout_type="rest",
        distance_km=None, pace_min_per_km=None,
    )
    _seed_completed(db, athlete.id, today, planned_workout_id=None)

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "COMPLETED"
    assert data["modifiers"]["is_bonus"] is True


# ═══════════════════════════════════════════════════════════════════════════
# US4 — RACE_DAY  (T050–T051)
# ═══════════════════════════════════════════════════════════════════════════


def test_race_day_state(app_and_db):
    """T050 — PlannedWorkout(workout_type='race') → state=RACE_DAY,
    rationale.source=persona_static, cover_lines drill_to=null."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id, race_date=today, race_name="Berlin Marathon")
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(
        db, athlete.id, plan.id, today,
        workout_type="race",
        distance_km=42.195,
        pace_min_per_km=4.66,  # ~7:30/mi
    )

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "RACE_DAY"
    assert data["headline"]["title"] == "Berlin Marathon"
    assert data["rationale"]["source"] == "persona_static"
    # Classic persona's race-morning greeting
    assert "Trust the work" in data["rationale"]["text"]
    # All cover lines are read-only
    drills = {c["drill_to"] for c in data["cover_lines"]}
    assert drills == {None}


@pytest.mark.parametrize("coach_key,expected_phrase", [
    ("classic", "Trust the work"),
    ("maya",    "showed up for the weeks"),
    ("jordan",  "stayed healthy enough"),
])
def test_race_day_greeting_per_persona(app_and_db, coach_key, expected_phrase):
    """T051 — Each persona has its own race-morning greeting."""
    app, db, athlete = app_and_db
    athlete.coach_key = coach_key
    db.commit()
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id, race_date=today)
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(db, athlete.id, plan.id, today, workout_type="race")

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "RACE_DAY"
    assert expected_phrase in data["rationale"]["text"]


# ═══════════════════════════════════════════════════════════════════════════
# US5 — NO_PLAN  (T054)
# ═══════════════════════════════════════════════════════════════════════════


def test_no_plan_state(app_and_db):
    """T054 — athlete has no active Goal → NO_PLAN with 'Pick a race' CTA."""
    app, db, athlete = app_and_db
    # No goal seeded — athlete starts with onboarding_complete=true but no Goal

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "NO_PLAN"
    assert data["headline"]["title"] == "Ready to train for something?"
    assert data["cover_lines"] is None
    assert data["actions"]["cta"]["label"] == "Pick a race"
    assert data["actions"]["cta"]["chat_prompt"] == "I want to train for…"


# ═══════════════════════════════════════════════════════════════════════════
# US6 — OFF_PLAN  (T057–T058)
# ═══════════════════════════════════════════════════════════════════════════


def test_off_plan_state_regression(app_and_db):
    """T057 — Active Goal + zero PlannedWorkout rows for today → OFF_PLAN
    (NOT REST_DAY, NOT NO_PLAN). The explicit regression scenario from FR-033."""
    app, db, athlete = app_and_db
    _seed_goal(db, athlete.id)
    # No PlannedWorkout for today

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "OFF_PLAN"
    assert data["headline"]["title"] == "Your plan needs attention"
    assert data["cover_lines"] is None
    assert data["actions"]["cta"]["label"] == "Review my plan"
    assert data["actions"]["cta"]["chat_prompt"] == "I'm between training blocks."


def test_off_plan_transitions_to_completed_on_bonus_run(app_and_db):
    """T058 — OFF_PLAN + CompletedWorkout(planned_workout_id=null) → COMPLETED
    with is_bonus=true. Same precedence rule as REST_DAY."""
    app, db, athlete = app_and_db
    _seed_goal(db, athlete.id)
    today = _athlete_today(athlete)
    _seed_completed(db, athlete.id, today, planned_workout_id=None)

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "COMPLETED"
    assert data["modifiers"]["is_bonus"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Observability — WebEvent emission  (T059–T060)
# ═══════════════════════════════════════════════════════════════════════════


def test_first_ever_state_resolution_emits_web_event(app_and_db):
    """T060 — Athlete with no prior today.state_transition events → first call emits
    one with from_state=null and the resolved to_state."""
    app, db, athlete = app_and_db
    # NO_PLAN state (no Goal seeded) — simplest baseline.

    # The endpoint uses get_session() (production session factory) for the
    # WebEvent write so it survives across requests. Point that at the same
    # in-memory DB the rest of the test fixture is using.
    @contextmanager
    def fake_get_session_module():
        yield db

    with patch(
        "running_coach_ai.web.api.today.get_session", fake_get_session_module
    ):
        resp = _client(app, athlete.id).get("/api/today")
    assert resp.status_code == 200
    db.expire_all()

    events = (
        db.query(WebEvent)
        .filter(WebEvent.athlete_id == athlete.id, WebEvent.category == "today")
        .all()
    )
    assert len(events) == 1
    event = events[0]
    assert event.details_json["from_state"] is None
    assert event.details_json["to_state"] == "NO_PLAN"


def test_state_transition_emits_web_event(app_and_db):
    """T059 — Transition emits a new WebEvent; repeated identical state does NOT."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    pw = _seed_planned_workout(db, athlete.id, plan.id, today, workout_type="tempo")

    @contextmanager
    def fake_get_session_module():
        yield db

    with patch(
        "running_coach_ai.web.api.today.get_session", fake_get_session_module
    ):
        client = _client(app, athlete.id)
        # 1st call → PRE_RUN emits (from None)
        r1 = client.get("/api/today")
        assert r1.get_json()["state"] == "PRE_RUN"
        db.expire_all()
        n1 = db.query(WebEvent).filter(
            WebEvent.athlete_id == athlete.id, WebEvent.category == "today"
        ).count()
        assert n1 == 1

        # 2nd call without changes → no new event
        client.get("/api/today")
        db.expire_all()
        n2 = db.query(WebEvent).filter(
            WebEvent.athlete_id == athlete.id, WebEvent.category == "today"
        ).count()
        assert n2 == 1

        # 3rd call — add a CompletedWorkout → state transitions to COMPLETED
        _seed_completed(db, athlete.id, today, planned_workout_id=pw.id)
        r3 = client.get("/api/today")
        assert r3.get_json()["state"] == "COMPLETED"
        db.expire_all()
        events = (
            db.query(WebEvent)
            .filter(WebEvent.athlete_id == athlete.id, WebEvent.category == "today")
            .order_by(WebEvent.timestamp.asc())
            .all()
        )
        assert len(events) == 2
        assert events[1].details_json["from_state"] == "PRE_RUN"
        assert events[1].details_json["to_state"] == "COMPLETED"
