# Today Card — collapse OFF_PLAN into REST_DAY Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe any day with no planned workout as a Rest Day with concrete recovery tips, removing the alarming "Your plan needs attention" copy and the OFF_PLAN state entirely.

**Architecture:** Drop the OFF_PLAN branch from the state resolver in `web/api/today.py` so an active Goal with no `PlannedWorkout` row falls through to `REST_DAY`. Add a new pure helper `rule_based_rest()` in `coach/today_rationale.py` that produces rest-appropriate rationale text (no "run it" framing) — fixes a latent bug where the existing REST_DAY path called `rule_based_morning()` which always told the athlete to "run it as written." Emit a new static `cues` list on REST_DAY payloads (Sleep / Fuel / Move) and render it in the magazine frontend as a small strip between the rationale and the cover stats. Reconcile the `007-today-card` spec and CLAUDE.md to document a five-state model.

**Tech Stack:** Python 3.11, Flask 3.x, SQLAlchemy, APScheduler, pytest, in-memory SQLite for integration tests. Frontend is plain ES2020 + handwritten CSS (no bundler).

**Reference spec:** `docs/superpowers/specs/2026-05-19-today-card-rest-day-design.md`

---

## File Structure

| Path | Responsibility | Action |
|---|---|---|
| `running_coach_ai/coach/today_rationale.py` | Pure-function rationale templates for the Today Card | **Modify:** add `rule_based_rest()` (Task 1) |
| `tests/unit/test_today_rationale.py` | Unit tests for the rationale helpers | **Create:** new file with 5 tests for `rule_based_rest()` (Task 1) |
| `running_coach_ai/web/api/today.py` | `/api/today` endpoint + state resolver + per-state payload builders | **Modify:** drop OFF_PLAN; route REST_DAY through `rule_based_rest`; emit `cues` field (Task 2) |
| `tests/integration/web/test_today_api.py` | Integration tests for `/api/today` | **Modify:** rewrite US6 tests, add 2 new rest-day rationale tests, update module docstring (Task 2) |
| `running_coach_ai/web/static/magazine.html` | Today Card markup | **Modify:** add `#td-cues` markup, drop OFF_PLAN comment (Task 3) |
| `running_coach_ai/web/static/magazine.css` | Today Card styles | **Modify:** add `.td-cues`, delete OFF_PLAN selectors (Task 3) |
| `running_coach_ai/web/static/magazine.js` | `tdRender()` hydration | **Modify:** hydrate `#td-cues`, drop `OFF_PLAN: 'Plan Check'` byline (Task 3) |
| `specs/007-today-card/*` | Speckit feature spec docs | **Modify:** strike OFF_PLAN from 6 spec files (Task 4) |
| `CLAUDE.md` | Project guidance | **Modify:** update Today Card section to five states (Task 5) |

---

## Task 1: Add `rule_based_rest()` to today_rationale.py

**Files:**
- Create: `tests/unit/test_today_rationale.py`
- Modify: `running_coach_ai/coach/today_rationale.py`

### Goal

Pure function that returns a coach-voice rest-day paragraph. No DB, no Garmin, no Claude. Three templates picked by snapshot bucketing (`low-recovery`, `recharged`, `steady`). Each template addresses the athlete by first name, gives concrete recovery tips, and includes the periodization clause when plan/goal context is available.

- [ ] **Step 1.1: Write the failing tests**

Create `tests/unit/test_today_rationale.py` with this exact content:

```python
"""Unit tests for rule-based rationale helpers (no DB, no I/O)."""

from __future__ import annotations

from types import SimpleNamespace

from running_coach_ai.coach.today_rationale import rule_based_rest


# ─── Test doubles ─────────────────────────────────────────────────────────


def _snap(hrv_score=None, hrv_status=None, sleep_h=None, body_battery_start=None):
    return SimpleNamespace(
        hrv_score=hrv_score,
        hrv_status=hrv_status,
        sleep_duration_seconds=(int(sleep_h * 3600) if sleep_h is not None else None),
        body_battery_start=body_battery_start,
        body_battery_end=body_battery_start,
        resting_hr=48,
    )


def _plan(phase="base", week=4):
    return SimpleNamespace(current_phase=phase, current_week=week)


def _goal(race_name="Berlin Marathon"):
    return SimpleNamespace(race_name=race_name)


# ─── rule_based_rest ──────────────────────────────────────────────────────


def test_rest_low_recovery_low_hrv():
    """Low HRV → low-recovery template — names rest framing, never 'run it'."""
    snap = _snap(hrv_score=42, hrv_status="low", sleep_h=7.2)
    text = rule_based_rest(snap, _plan(), _goal(), "Sam Runner")
    assert "Sam" in text
    assert "run it" not in text.lower()
    assert "as written" not in text.lower()
    assert "asking for room" in text.lower()


def test_rest_low_recovery_short_sleep():
    """Short sleep (< 6.5 h) → low-recovery template even when HRV is fine."""
    snap = _snap(hrv_score=72, hrv_status="balanced", sleep_h=5.5)
    text = rule_based_rest(snap, _plan(), _goal(), "Sam Runner")
    assert "asking for room" in text.lower()
    assert "5.5" in text  # references the observed sleep hours


def test_rest_recharged_high_hrv_solid_sleep():
    """High HRV + ≥ 7 h sleep → recharged template."""
    snap = _snap(hrv_score=92, hrv_status="high", sleep_h=7.8)
    text = rule_based_rest(snap, _plan(), _goal(), "Sam Runner")
    assert "Sam" in text
    assert "run it" not in text.lower()
    assert "after rest" in text.lower()


def test_rest_steady_no_snapshot():
    """Missing snapshot → steady template; still names rest framing and tips."""
    text = rule_based_rest(None, _plan(), _goal(), "Sam Runner")
    assert "Sam" in text
    assert "right less" in text.lower()
    assert "run it" not in text.lower()


def test_rest_periodization_clause_when_plan_present():
    """When plan + goal present, weave 'week N of phase on the road to race' in."""
    snap = _snap(hrv_score=92, hrv_status="high", sleep_h=7.8)
    text = rule_based_rest(
        snap, _plan(phase="build", week=6), _goal("Chicago Marathon"), "Sam Runner"
    )
    assert "week 6" in text.lower()
    assert "build block" in text.lower()
    assert "Chicago Marathon" in text


def test_rest_no_plan_omits_periodization_cleanly():
    """No plan/goal → no orphaned 'week N' fragments; sentence still reads."""
    snap = _snap(hrv_score=92, hrv_status="high", sleep_h=7.8)
    text = rule_based_rest(snap, None, None, "Sam Runner")
    assert "Sam" in text
    assert "week " not in text.lower()
    assert "road to" not in text.lower()
```

- [ ] **Step 1.2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_today_rationale.py -v`

Expected: 6 failures, all with `ImportError: cannot import name 'rule_based_rest' from 'running_coach_ai.coach.today_rationale'`.

- [ ] **Step 1.3: Implement `rule_based_rest()` in today_rationale.py**

Open `running_coach_ai/coach/today_rationale.py`. Find the `rule_based_morning` definition (currently around line 324) and add the new function **immediately above it** so the rest-day helper lives next to its sibling.

Insert this code before the `def rule_based_morning(` line:

```python
# ─── Rest-day templates ────────────────────────────────────────────────────


def _rest_low_recovery_template(
    snap: "HealthSnapshot", periodization: str, name: str
) -> str:
    hrv_score = snap.hrv_score if snap.hrv_score is not None else "—"
    sleep_h = _sleep_hours(snap)
    sleep_clause = (
        f"sleep at {sleep_h} h" if sleep_h is not None else f"HRV at {hrv_score} ms"
    )
    return (
        f"{name}, {sleep_clause} — your body is asking for room today. "
        f"{periodization} Sleep early, eat real food, stay off the legs except "
        f"for an easy walk if you're stiff. Nothing more — the work is letting "
        f"the system absorb the load."
    )


def _rest_recharged_template(
    snap: "HealthSnapshot", periodization: str, name: str
) -> str:
    hrv_score = snap.hrv_score if snap.hrv_score is not None else "—"
    sleep_h = _sleep_hours(snap)
    sleep_clause = (
        f"slept {sleep_h} hours and HRV is up at {hrv_score} ms"
        if sleep_h is not None
        else f"HRV is up at {hrv_score} ms"
    )
    return (
        f"{name}, you {sleep_clause}. That's last week's work landing. "
        f"{periodization} Don't rush the next session — sleep, fuel, walk if "
        f"you want to move. The fitness shows up after rest, not in spite of it."
    )


def _rest_steady_template(
    snap: "HealthSnapshot | None", periodization: str, name: str
) -> str:
    return (
        f"{name}, today isn't about doing less — it's about doing the right "
        f"less. {periodization} Sleep early, hydrate to clear-pale-yellow, and "
        f"give the hips and ankles 10 minutes of mobility. Walk if you want; "
        f"don't run."
    )


def rule_based_rest(
    snap: "HealthSnapshot | None",
    plan: "TrainingPlan | None",
    goal: "Goal | None",
    athlete_name: str,
) -> str:
    """Return a single coach-voice rest-day paragraph for the Today Card.

    Picks one of three templates by snapshot bucket:
    - low-recovery: HRV `low` OR sleep < 6.5 h
    - recharged:    HRV `high` AND sleep ≥ 7.0 h
    - steady:       everything else, including missing snapshot

    None of the templates use "run it" framing — rest day means rest. The
    periodization clause ("week N of the base block on the road to Berlin")
    is appended when plan+goal are available, same shape as the morning
    helper (FR-008c).
    """
    first_name = (athlete_name or "Athlete").split()[0]
    periodization = _periodization_clause(plan, goal, "rest day")

    if snap is not None:
        hrv = _hrv_bucket(snap)
        sleep_h = _sleep_hours(snap)

        # low-recovery: HRV low OR short sleep
        if hrv == "low" or (sleep_h is not None and sleep_h < 6.5):
            return _rest_low_recovery_template(snap, periodization, first_name)

        # recharged: high HRV AND solid sleep
        if hrv == "high" and sleep_h is not None and sleep_h >= 7.0:
            return _rest_recharged_template(snap, periodization, first_name)

    # steady fallback (covers normal bucket + missing snapshot)
    return _rest_steady_template(snap, periodization, first_name)


```

- [ ] **Step 1.4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_today_rationale.py -v`

Expected: 6 passed.

If `test_rest_periodization_clause_when_plan_present` fails on the `"build block"` assertion, check that `_periodization_clause` actually produces `"of the build block"` — it should, since `phase="build"` and `phase.replace("_", " ")` is `"build"` → the existing helper produces `"of the build block"`.

- [ ] **Step 1.5: Run lint to keep the tree clean**

Run: `ruff check running_coach_ai/coach/today_rationale.py tests/unit/test_today_rationale.py`

Expected: `All checks passed!`

- [ ] **Step 1.6: Commit**

```bash
git add running_coach_ai/coach/today_rationale.py tests/unit/test_today_rationale.py
git commit -m "Add rule_based_rest for rest-day Today Card rationale

Three templates (low-recovery / recharged / steady) picked by HRV +
sleep bucketing. Replaces the latent bug where REST_DAY routed through
rule_based_morning and told athletes to 'run it as written' on a rest
day."
```

---

## Task 2: Backend — drop OFF_PLAN, route REST_DAY through rule_based_rest, emit cues

**Files:**
- Modify: `tests/integration/web/test_today_api.py`
- Modify: `running_coach_ai/web/api/today.py`

### Goal

Remove the OFF_PLAN state entirely. Any active Goal with no `PlannedWorkout` for today resolves to REST_DAY. `_build_rest_day` no longer takes a `planned` argument, calls `rule_based_rest()`, and emits a static three-item `cues` list on the payload.

- [ ] **Step 2.1: Update the test module docstring + section header**

In `tests/integration/web/test_today_api.py`:

Replace lines 1–13 (the module docstring) with:

```python
"""Integration tests for GET /api/today across all 5 states.

Per spec 007:
  - US1 (PRE_RUN, P1) — T027–T033
  - US2 (COMPLETED, P1) — T039–T042
  - US3 (REST_DAY, P2) — T045–T046
  - US4 (RACE_DAY, P2) — T050–T051
  - US5 (NO_PLAN, P3) — T054
  - US6 (no-planned-row → REST_DAY, P3) — T057–T058
  - Polish (observability) — T059–T060

Tests run against in-memory SQLite (FR-035). No Claude/Garmin calls.
"""
```

Replace the section divider at line 576–578:

```python
# ═══════════════════════════════════════════════════════════════════════════
# US6 — OFF_PLAN  (T057–T058)
# ═══════════════════════════════════════════════════════════════════════════
```

with:

```python
# ═══════════════════════════════════════════════════════════════════════════
# US6 — no-planned-row → REST_DAY  (T057–T058)
# ═══════════════════════════════════════════════════════════════════════════
```

- [ ] **Step 2.2: Rewrite the two OFF_PLAN tests**

Replace `test_off_plan_state_regression` (lines 581–594) with:

```python
def test_no_planned_row_treated_as_rest_day(app_and_db):
    """T057 — Active Goal + zero PlannedWorkout rows for today → REST_DAY.

    Reframed from OFF_PLAN: a "between training blocks" day is just a rest
    day with recovery tips, not an alarming 'plan needs attention' state.
    Regression for the spec-007 zero-row scenario (FR-033)."""
    app, db, athlete = app_and_db
    _seed_goal(db, athlete.id)
    # No PlannedWorkout for today

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "REST_DAY"
    assert data["headline"]["title"] == "Recovery is the workout"
    assert data["actions"]["cta"] is None
    # Cover lines are the 4-up morning grid (HRV / Body Battery / Sleep / RHR)
    assert isinstance(data["cover_lines"], list)
    assert len(data["cover_lines"]) == 4
    # Static cues strip
    assert isinstance(data["cues"], list)
    assert len(data["cues"]) == 3
    labels = [c["label"] for c in data["cues"]]
    assert labels == ["Sleep", "Fuel", "Move"]
```

Replace `test_off_plan_transitions_to_completed_on_bonus_run` (lines 597–end-of-function) with:

```python
def test_no_planned_row_transitions_to_completed_on_bonus_run(app_and_db):
    """T058 — Active Goal + no PlannedWorkout + bonus CompletedWorkout → COMPLETED
    with is_bonus=true. State precedence (FR-003): COMPLETED beats REST_DAY."""
    app, db, athlete = app_and_db
    _seed_goal(db, athlete.id)
    today = _athlete_today(athlete)
    _seed_completed(db, athlete.id, today, planned_workout_id=None)

    resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "COMPLETED"
    assert data["modifiers"]["is_bonus"] is True
```

(Note: the original `test_off_plan_transitions_to_completed_on_bonus_run` body may have additional assertions after `_seed_completed`. If so, preserve them — only the function name and the seeded preconditions change. Open the file and read lines 597–610 to confirm the full original body before replacing, and keep any extra asserts.)

- [ ] **Step 2.3: Add two new rest-day rationale tests**

Insert these two new tests immediately after the US3 section (after `test_rest_day_transitions_to_completed_on_bonus_run`, before the US4 divider). Find the closing line of `test_rest_day_transitions_to_completed_on_bonus_run` and add a blank line, then:

```python
def test_rest_day_rationale_avoids_run_framing(app_and_db):
    """REST_DAY rationale must not tell the athlete to 'run it' — rest is rest.

    Guards against the latent bug where REST_DAY routed through
    rule_based_morning and produced 'Run it as written' on a rest day."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(db, athlete.id, plan.id, today, workout_type="rest")
    # High readiness — most likely to trip the bug since 'recharged' templates
    # in rule_based_morning all say 'run it as written'.
    _seed_health_snapshot(
        db, athlete.id, today, hrv=92, hrv_status="high", sleep_h=7.8, bb=85, rhr=46
    )

    # Force "after 7am local" so the rationale ladder reaches rule_based.
    import running_coach_ai.web.api.today as today_mod
    real_datetime = today_mod.datetime

    class _FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            base = real_datetime(today.year, today.month, today.day, 10, 0, 0)
            return base.replace(tzinfo=tz) if tz else base

    with patch.object(today_mod, "datetime", _FakeDatetime):
        resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "REST_DAY"
    assert data["rationale"]["source"] == "rule_based"
    text = data["rationale"]["text"].lower()
    assert "run it" not in text
    assert "as written" not in text
    assert "target pace" not in text
    assert "easy end" not in text


def test_rest_day_low_recovery_template(app_and_db):
    """Low HRV + short sleep on a rest day → low-recovery template."""
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(db, athlete.id, plan.id, today, workout_type="rest")
    _seed_health_snapshot(
        db, athlete.id, today, hrv=38, hrv_status="low", sleep_h=5.5, bb=35, rhr=58
    )

    import running_coach_ai.web.api.today as today_mod
    real_datetime = today_mod.datetime

    class _FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            base = real_datetime(today.year, today.month, today.day, 10, 0, 0)
            return base.replace(tzinfo=tz) if tz else base

    with patch.object(today_mod, "datetime", _FakeDatetime):
        resp = _client(app, athlete.id).get("/api/today")
    data = resp.get_json()
    assert data["state"] == "REST_DAY"
    assert data["rationale"]["source"] == "rule_based"
    assert "asking for room" in data["rationale"]["text"].lower()
```

The `_seed_planned_workout` signature is `(db, athlete_id, plan_id, scheduled_date, workout_type="tempo", ...)` — pass `plan.id` as the third positional arg. The `_seed_health_snapshot` helper takes `hrv`, `hrv_status`, `sleep_h`, `bb`, `rhr` kwargs (see lines 165–182 of the file). The `_FakeDatetime` pattern is copied verbatim from `test_pre_run_rationale_rule_based_when_no_notification` (lines 285–317) — without it, a real "before 7am" timestamp would route to the placeholder branch instead of `rule_based`, and the test would fail spuriously based on what time of day it runs.

- [ ] **Step 2.4: Run the integration tests to verify the new ones fail**

Run: `pytest tests/integration/web/test_today_api.py -v -k "rest_day or no_planned_row"`

Expected: `test_rest_day_state` and `test_rest_day_transitions_to_completed_on_bonus_run` PASS (untouched). The four new/renamed tests FAIL:
- `test_no_planned_row_treated_as_rest_day` — fails because state still resolves to `OFF_PLAN`.
- `test_no_planned_row_transitions_to_completed_on_bonus_run` — should already pass since COMPLETED beats both OFF_PLAN and REST_DAY (verify; if it fails, that's an extra bug worth flagging).
- `test_rest_day_rationale_avoids_run_framing` — fails because `rule_based_morning` produces "run it as written" prose.
- `test_rest_day_low_recovery_template` — fails for the same reason; "asking for room" comes from `rule_based_rest`.

- [ ] **Step 2.5: Drop OFF_PLAN from the state resolver**

Open `running_coach_ai/web/api/today.py`. Update the module docstring (lines 1–21) — replace the "Six states are supported" block with:

```python
"""GET /api/today — Today Card data for the magazine dashboard.

Returns a single JSON object whose `state` field drives the cover treatment.
Five states are supported (per spec 007 FR-003):

    NO_PLAN   →  athlete has no active Goal
    RACE_DAY  →  today's PlannedWorkout.workout_type == "race"
    COMPLETED →  today's CompletedWorkout exists
    REST_DAY  →  today's PlannedWorkout.workout_type == "rest", OR
                 active Goal exists but no PlannedWorkout row for today
    PRE_RUN   →  default — there's a workout today and it hasn't been completed

Rationale text resolution ladder per FR-005/FR-006/FR-007:

    morning_checkin → coach_analysis → rule_based → placeholder (before 7am)
                                                  → persona_static (race/no-plan)

All "today" computation uses `athlete.timezone` (FR-002). Zero Claude calls
at request time (FR-004). State-transition WebEvents (FR-032a/b) are emitted
via a Flask after-request hook so the HTTP response is never blocked.
"""
```

In `_resolve_state` (lines 91–130), replace the body's final branches:

```python
    if planned is not None and planned.workout_type == "race":
        return "RACE_DAY", goal, planned, completed
    if completed is not None:
        return "COMPLETED", goal, planned, completed
    if planned is not None and planned.workout_type == "rest":
        return "REST_DAY", goal, planned, completed
    if planned is None:
        return "OFF_PLAN", goal, None, None
    return "PRE_RUN", goal, planned, completed
```

with:

```python
    if planned is not None and planned.workout_type == "race":
        return "RACE_DAY", goal, planned, completed
    if completed is not None:
        return "COMPLETED", goal, planned, completed
    if planned is None or planned.workout_type == "rest":
        return "REST_DAY", goal, planned, completed
    return "PRE_RUN", goal, planned, completed
```

- [ ] **Step 2.6: Add the `_REST_DAY_CUES` constant**

In `running_coach_ai/web/api/today.py`, add this constant **immediately after the `_TYPE_LABEL` dict** (which ends around line 73). Insert before the next function definition:

```python
# Static cues rendered in the rest-day cover strip (spec 007 rest-day spec
# 2026-05-19). Same three for every rest day in v1; can be made dynamic
# later without a contract change.
_REST_DAY_CUES = [
    {"label": "Sleep", "copy": "in bed early"},
    {"label": "Fuel",  "copy": "carbs + protein"},
    {"label": "Move",  "copy": "walk or mobility"},
]
```

- [ ] **Step 2.7: Switch import: add `rule_based_rest`**

In `running_coach_ai/web/api/today.py` find the import block:

```python
from running_coach_ai.coach.today_rationale import (
    extract_rationale_paragraph,
    rule_based_completed,
    rule_based_morning,
)
```

Replace with:

```python
from running_coach_ai.coach.today_rationale import (
    extract_rationale_paragraph,
    rule_based_completed,
    rule_based_morning,
    rule_based_rest,
)
```

- [ ] **Step 2.8: Rewrite `_build_rest_day` — drop `planned`, route through `rule_based_rest`, emit `cues`**

In `running_coach_ai/web/api/today.py`, replace the entire `_build_rest_day` function (currently lines 359–409) with:

```python
def _build_rest_day(
    db,
    athlete: Athlete,
    goal: Goal,
    today_local: date,
    now_local: datetime,
) -> dict:
    """REST_DAY payload — fires for both explicit rest rows and zero-row days.

    Reuses the FR-005 rationale ladder (morning_checkin → placeholder before 7am
    → rule-based) but the rule-based fallback is rest-specific via
    `rule_based_rest` — never "run it as written" framing on a rest day.
    Cover lines are the 4-up morning readiness grid (FR-013) and the new
    `cues` field exposes a static recovery-tip strip.
    """
    snap, is_stale = _resolve_health_snapshot(db, athlete.id, today_local)
    plan = _current_training_plan(db, goal)
    plan_view = _plan_with_current_week(plan, today_local)

    paragraph, _ = _morning_checkin_paragraph(db, athlete.id, now_local)
    if paragraph:
        rationale_text = paragraph
        rationale_source = "morning_checkin"
    elif _is_before_7am_local(now_local):
        rationale_text = (
            "Coach is checking in soon — pull this up after 7am for today's call."
        )
        rationale_source = "placeholder"
    else:
        rationale_text = rule_based_rest(snap, plan_view, goal, athlete.name)
        rationale_source = "rule_based"

    return {
        "state": "REST_DAY",
        "headline": {
            "eyebrow":  "Rest Day",
            "ribbon":   "Rest Day",
            "title":    "Recovery is the workout",
            "subtitle": None,
        },
        "rationale": {
            "text":         rationale_text,
            "source":       rationale_source,
            "coach":        _coach_display_name(athlete),
            "accent_color": _accent(athlete),
        },
        "modifiers": {
            "on_watch": False,
            "is_bonus": False,
        },
        "cover_lines": _morning_cover_lines(snap, is_stale),
        "cues": list(_REST_DAY_CUES),
        "actions": {
            "headline_chat_prompt":  "How should I make the most of today's recovery?",
            "rationale_chat_prompt": "I have a question about today's plan.",
            "cta": None,
        },
    }
```

- [ ] **Step 2.9: Delete `_build_off_plan`**

In `running_coach_ai/web/api/today.py`, find `_build_off_plan` (currently around lines 514–548) and delete the entire function. It's no longer referenced.

- [ ] **Step 2.10: Update the dispatch in the `today()` endpoint**

In `running_coach_ai/web/api/today.py` find the dispatch block in the `today()` endpoint (currently around lines 689–700):

```python
        if state == "NO_PLAN":
            payload = _build_no_plan(athlete)
        elif state == "RACE_DAY":
            payload = _build_race_day(db, athlete, planned, goal)
        elif state == "COMPLETED":
            payload = _build_completed(db, athlete, planned, completed, goal, today_local)
        elif state == "REST_DAY":
            payload = _build_rest_day(db, athlete, planned, goal, today_local, now_local)
        elif state == "OFF_PLAN":
            payload = _build_off_plan(athlete)
        else:  # PRE_RUN
            payload = _build_pre_run(db, athlete, planned, goal, today_local, now_local)
```

Replace with:

```python
        if state == "NO_PLAN":
            payload = _build_no_plan(athlete)
        elif state == "RACE_DAY":
            payload = _build_race_day(db, athlete, planned, goal)
        elif state == "COMPLETED":
            payload = _build_completed(db, athlete, planned, completed, goal, today_local)
        elif state == "REST_DAY":
            payload = _build_rest_day(db, athlete, goal, today_local, now_local)
        else:  # PRE_RUN
            payload = _build_pre_run(db, athlete, planned, goal, today_local, now_local)
```

(Two changes: `_build_rest_day` no longer takes `planned`, and the `OFF_PLAN` branch is gone.)

- [ ] **Step 2.11: Run the integration tests to verify they pass**

Run: `pytest tests/integration/web/test_today_api.py -v`

Expected: all tests pass. Watch for:
- `test_no_planned_row_treated_as_rest_day` — PASS.
- `test_no_planned_row_transitions_to_completed_on_bonus_run` — PASS.
- `test_rest_day_rationale_avoids_run_framing` — PASS.
- `test_rest_day_low_recovery_template` — PASS.
- All US1/US2/US3/US4/US5 tests — still PASS (no regression).

If `test_rest_day_state` (US3, line 453) still uses `source="morning_checkin"` and the rationale doesn't actually call `rule_based_rest`, that's expected — that test seeds a Notification, so the morning_checkin branch wins regardless.

- [ ] **Step 2.12: Run unit tests + linter — make sure nothing else broke**

Run: `pytest tests/unit/test_today_rationale.py -v && ruff check running_coach_ai/web/api/today.py tests/integration/web/test_today_api.py`

Expected: 6 unit tests pass + ruff clean.

- [ ] **Step 2.13: Commit**

```bash
git add running_coach_ai/web/api/today.py tests/integration/web/test_today_api.py
git commit -m "Collapse OFF_PLAN into REST_DAY on the Today Card

A day with no PlannedWorkout (athlete between training blocks, or a plan
that skips zero-row days) now resolves to REST_DAY with rest-appropriate
rationale via rule_based_rest, not the alarming 'Your plan needs
attention' OFF_PLAN state. Adds a static three-item cues field for the
frontend recovery-tip strip."
```

---

## Task 3: Frontend — markup, CSS, JS for cues strip; remove OFF_PLAN refs

**Files:**
- Modify: `running_coach_ai/web/static/magazine.html`
- Modify: `running_coach_ai/web/static/magazine.css`
- Modify: `running_coach_ai/web/static/magazine.js`

### Goal

Add a small "cues" strip (Sleep / Fuel / Move) between the rationale and the cover stats on REST_DAY. Hydrate it from `payload.cues`. Remove OFF_PLAN references throughout the frontend.

There are no automated frontend tests in this repo — verify by visual smoke test in the browser at the end (see Step 3.7).

- [ ] **Step 3.1: Add `#td-cues` markup in magazine.html**

In `running_coach_ai/web/static/magazine.html`, update the section comment on line 94:

```html
<!-- Today article: workout / rest / race / no-plan / off-plan / completed -->
```

to:

```html
<!-- Today article: workout / rest / race / no-plan / completed -->
```

Then, between `<div class="td-on-watch" id="td-on-watch" hidden>Ready on watch ✓</div>` (line 112) and the `<!-- CTA button -->` comment (line 114), insert the cues markup:

```html
      <!-- Recovery cues strip (REST_DAY only, hydrated from payload.cues) -->
      <div class="td-cues" id="td-cues" hidden>
        <span><strong></strong></span><span><strong></strong></span><span><strong></strong></span>
      </div>

```

Also update line 114's CTA comment:

```html
      <!-- CTA button (only rendered for NO_PLAN / OFF_PLAN) -->
```

to:

```html
      <!-- CTA button (only rendered for NO_PLAN) -->
```

- [ ] **Step 3.2: Add `.td-cues` styles in magazine.css**

In `running_coach_ai/web/static/magazine.css`, update the section comment on line 119:

```css
/* Article — workout / rest / race / no-plan / off-plan article */
```

to:

```css
/* Article — workout / rest / race / no-plan / completed article */
```

Update the CTA section comment on line 135:

```css
/* CTA button (NO_PLAN / OFF_PLAN) */
```

to:

```css
/* CTA button (NO_PLAN only) */
```

After the `.td-on-watch` rule (line 133) and before the `/* CTA button */` comment (line 135), add:

```css
.td-cues{
  display:flex;gap:16px;flex-wrap:wrap;
  font-size:10px;color:rgba(255,255,255,.65);
  margin:10px 0 14px;padding:8px 12px;
  background:rgba(184,255,79,.06);
  border-left:1px solid rgba(184,255,79,.4);
  border-radius:0 3px 3px 0;
  position:relative;z-index:3;
}
.td-cues span strong{
  color:rgba(184,255,79,.85);font-weight:600;
  margin-right:4px;letter-spacing:.08em;
  text-transform:uppercase;font-size:9px;
}
```

- [ ] **Step 3.3: Delete OFF_PLAN CSS selectors**

In `running_coach_ai/web/static/magazine.css`, find the state-visibility block (currently lines 167–173):

```css
/* State-specific visibility toggles */
#today[data-state="NO_PLAN"] .td-stats,
#today[data-state="OFF_PLAN"] .td-stats{display:none;}
#today[data-state="NO_PLAN"] .td-headline,
#today[data-state="OFF_PLAN"] .td-headline{cursor:default;}
#today[data-state="NO_PLAN"] .td-rationale,
#today[data-state="OFF_PLAN"] .td-rationale{cursor:default;border-left-color:rgba(255,255,255,.2);}
```

Replace with:

```css
/* State-specific visibility toggles */
#today[data-state="NO_PLAN"] .td-stats{display:none;}
#today[data-state="NO_PLAN"] .td-headline{cursor:default;}
#today[data-state="NO_PLAN"] .td-rationale{cursor:default;border-left-color:rgba(255,255,255,.2);}
```

- [ ] **Step 3.4: Drop the `OFF_PLAN` byline mapping in tdRender**

In `running_coach_ai/web/static/magazine.js`, find the byline mapping (currently lines 2766–2773):

```javascript
  const byPart = ({
    PRE_RUN:    'Morning Briefing',
    COMPLETED:  'Post-Run Analysis',
    REST_DAY:   'Recovery Note',
    RACE_DAY:   'Race-Morning Briefing',
    NO_PLAN:    'Welcome',
    OFF_PLAN:   'Plan Check',
  })[payload.state] || 'Today';
```

Replace with:

```javascript
  const byPart = ({
    PRE_RUN:    'Morning Briefing',
    COMPLETED:  'Post-Run Analysis',
    REST_DAY:   'Recovery Note',
    RACE_DAY:   'Race-Morning Briefing',
    NO_PLAN:    'Welcome',
  })[payload.state] || 'Today';
```

- [ ] **Step 3.5: Update the CTA comment in tdRender**

In `running_coach_ai/web/static/magazine.js`, line 2799:

```javascript
  // CTA button (NO_PLAN / OFF_PLAN)
```

to:

```javascript
  // CTA button (NO_PLAN only)
```

- [ ] **Step 3.6: Hydrate `#td-cues` in tdRender**

In `running_coach_ai/web/static/magazine.js`, find the cover-lines/stats block (currently starts at line 2835 with `// Cover lines / stats`). Insert the cues hydration **immediately before** that block:

```javascript
  // Recovery cues strip (REST_DAY only — hidden otherwise)
  const cuesEl = document.getElementById('td-cues');
  const cues = Array.isArray(payload.cues) ? payload.cues : [];
  if (cuesEl) {
    if (cues.length > 0) {
      cuesEl.hidden = false;
      const spans = cuesEl.querySelectorAll('span');
      cues.slice(0, spans.length).forEach((cue, i) => {
        const span = spans[i];
        const strong = span.querySelector('strong');
        if (strong) strong.textContent = (cue.label || '').toUpperCase();
        // Append the copy after the <strong>, replacing any prior text node
        // (clear then re-append keeps the markup deterministic across renders)
        while (span.childNodes.length > 1) span.removeChild(span.lastChild);
        span.appendChild(document.createTextNode(cue.copy || ''));
      });
      // Hide any leftover spans if fewer cues than slots
      spans.forEach((s, i) => { s.hidden = i >= cues.length; });
    } else {
      cuesEl.hidden = true;
    }
  }

```

- [ ] **Step 3.7: Verify visually in the browser**

Start the web server in dev mode (a fresh shell — don't background this since you'll watch the output):

```bash
python web.py
```

In a browser, log in as a test athlete that has an active Goal but no `PlannedWorkout` row for today (or temporarily delete today's planned row in `sqlite3 /data/coach.db`). Navigate to `/app#today`.

Check:
- [ ] Byline reads "Today · Coach Alex · Recovery Note" (not "Plan Check").
- [ ] Headline reads "Recovery is the workout" (not "Your plan needs attention").
- [ ] Ribbon says "Rest Day" in lime.
- [ ] The cues strip renders between the rationale and the stats grid, with three lime-uppercase labels (SLEEP / FUEL / MOVE) and the copy text after each.
- [ ] No "Review my plan" CTA button.
- [ ] The 4-up stats grid is present (HRV / Body Battery / Sleep / RHR).
- [ ] The rationale paragraph reads as rest-appropriate (no "run it" / "as written").

Stop the server (Ctrl-C) when done.

If you can't get a real athlete into the no-planned-row state, also try a date with an explicit `rest` PlannedWorkout — the same UI should render (the cues strip is the only new element, and it appears for both REST_DAY origins).

- [ ] **Step 3.8: Run lint**

Run: `ruff check .` (the frontend isn't linted by ruff, but this guards against any accidental Python edits earlier in the task).

Expected: clean.

- [ ] **Step 3.9: Commit**

```bash
git add running_coach_ai/web/static/magazine.html running_coach_ai/web/static/magazine.css running_coach_ai/web/static/magazine.js
git commit -m "Add rest-day cues strip and drop OFF_PLAN refs from Today Card UI

New small strip between rationale and stats renders payload.cues for
REST_DAY (Sleep / Fuel / Move with lime accent). OFF_PLAN byline,
CSS selectors, and section comments removed — the state no longer
exists on the backend."
```

---

## Task 4: Spec reconciliation — strike OFF_PLAN from specs/007-today-card/

**Files:**
- Modify: `specs/007-today-card/spec.md`
- Modify: `specs/007-today-card/tasks.md`
- Modify: `specs/007-today-card/contracts/today-api.md`
- Modify: `specs/007-today-card/data-model.md`
- Modify: `specs/007-today-card/plan.md`
- Modify: `specs/007-today-card/quickstart.md`

### Goal

Update the speckit spec files for branch `007-today-card` to reflect the five-state model. The shipped spec documented OFF_PLAN as a first-class state — that's stale once Task 2 ships.

- [ ] **Step 4.1: Grep for every OFF_PLAN reference across the spec**

Run: `grep -rn "OFF_PLAN\|off_plan\|off-plan\|off plan" specs/007-today-card/`

Note every file:line that matches. Each one needs to be either deleted (if it's a state-list entry, US6 header, or a CTA spec for OFF_PLAN) or rewritten (if it's a no-planned-row scenario that now resolves to REST_DAY).

- [ ] **Step 4.2: Edit `specs/007-today-card/spec.md`**

Open the file. Make these edits:

1. **FR-003 (state list / precedence):** Change the state count from six to five. Remove the OFF_PLAN row from the state table. Update the precedence order to omit OFF_PLAN. Add a note: "A day with an active Goal but no PlannedWorkout row resolves to REST_DAY (treated as a recovery day with tips, not an alarm state)."
2. **FR-007b:** Delete entirely (was the OFF_PLAN rationale spec).
3. **FR-012:** Update to say only NO_PLAN renders a CTA (delete the OFF_PLAN clause).
4. **FR-033:** Rewrite — was "zero PlannedWorkout rows for today → OFF_PLAN regression." Now: "zero PlannedWorkout rows for today → REST_DAY (regression for the OFF_PLAN-collapse change, see docs/superpowers/specs/2026-05-19-today-card-rest-day-design.md)."
5. **US6 (P3 user story):** Rewrite the user story from "between training blocks" framing to "any day without a scheduled workout shows as a rest day with concrete recovery tips." Keep US6 as the user story label; downstream tasks reference T057/T058 by name.
6. **Acceptance scenarios** mentioning OFF_PLAN: rewrite to mention REST_DAY.

If any FR text uses "six states" or "Six states," change to "five states / Five states."

- [ ] **Step 4.3: Edit `specs/007-today-card/tasks.md`**

Find the US6 section header. Rename to "US6 — no-planned-row → REST_DAY." Rewrite T057 and T058 task bodies to match the renamed tests in Task 2.3 of this plan (`test_no_planned_row_treated_as_rest_day` and `test_no_planned_row_transitions_to_completed_on_bonus_run`). Do not renumber other tasks.

- [ ] **Step 4.4: Edit `specs/007-today-card/contracts/today-api.md`**

1. Remove the OFF_PLAN response example block entirely.
2. In the state enum / list, delete `OFF_PLAN`.
3. In the REST_DAY response example, add the new `cues` field with the three default entries:

```json
"cues": [
  {"label": "Sleep", "copy": "in bed early"},
  {"label": "Fuel",  "copy": "carbs + protein"},
  {"label": "Move",  "copy": "walk or mobility"}
]
```

Add a one-sentence note that `cues` is present only on REST_DAY payloads.

- [ ] **Step 4.5: Edit `specs/007-today-card/data-model.md`**

Remove OFF_PLAN from any state diagram or state table. If there's a precedence diagram, update it to drop the OFF_PLAN node and route "no planned row" directly into REST_DAY.

- [ ] **Step 4.6: Edit `specs/007-today-card/plan.md`**

Find any "six states" / "Six states" phrasing and change to "five states." Find any OFF_PLAN references in the architecture description and update / delete as appropriate.

- [ ] **Step 4.7: Edit `specs/007-today-card/quickstart.md`**

In the test matrix table, remove the OFF_PLAN row or rewrite it as the no-planned-row → REST_DAY scenario.

- [ ] **Step 4.8: Verify nothing was missed**

Run: `grep -rn "OFF_PLAN\|off_plan\|off-plan" specs/007-today-card/`

Expected: no matches. If any remain, edit them out.

- [ ] **Step 4.9: Commit**

```bash
git add specs/007-today-card/
git commit -m "Reconcile spec 007 to the five-state Today Card model

Removes OFF_PLAN from spec.md, tasks.md, contracts, data-model, plan,
and quickstart. Adds the cues field to the REST_DAY response contract."
```

---

## Task 5: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

### Goal

Update the project-level guidance file so future Claude sessions read accurate state-model description.

- [ ] **Step 5.1: Edit CLAUDE.md**

Open `CLAUDE.md`. Find the "Today Card" section header. In the first sentence, change "Six states (`PRE_RUN`, `COMPLETED`, `REST_DAY`, `RACE_DAY`, `NO_PLAN`, `OFF_PLAN`)" to "Five states (`PRE_RUN`, `COMPLETED`, `REST_DAY`, `RACE_DAY`, `NO_PLAN`)".

In the rationale-source-ladder description, add a clause: "REST_DAY uses `rule_based_rest` for its rule-based fallback (not `rule_based_morning`) — rest-appropriate prose, no 'run it' framing."

- [ ] **Step 5.2: Verify the change**

Run: `grep -n "OFF_PLAN\|Six states\|six states" CLAUDE.md`

Expected: no matches.

- [ ] **Step 5.3: Commit**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md Today Card section to five states"
```

---

## Final verification

After all five tasks are committed:

- [ ] **Run the full test suite** for the touched areas:

```bash
pytest tests/unit/test_today_rationale.py tests/integration/web/test_today_api.py -v
```

Expected: all tests pass.

- [ ] **Run lint:**

```bash
ruff check .
```

Expected: clean.

- [ ] **Check the working tree is clean:**

```bash
git status
```

Expected: nothing to commit beyond the five Task commits.

- [ ] **Visual smoke test (if not done in Task 3.7):**

Start `python web.py`, log in as an athlete with no PlannedWorkout today, confirm:
- Headline reads "Recovery is the workout"
- No "Your plan needs attention"
- Cues strip renders below the rationale, above the stats
- No "Review my plan" CTA
