# Morning Check-in Staleness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop morning check-ins from firing with yesterday's HRV when Garmin hasn't synced; if no fresh data by noon prefer stale data over silence; auto re-fire when fresh data arrives later; show staleness visibly on all three rendering surfaces.

**Architecture:** The gate `_should_wait_for_morning_data` is widened to also defer on a failed Garmin fetch when it's still before noon. After the noon cutoff, the morning job falls through to whatever stored snapshot is available (preferred) or writes a short "no morning report" notification (last resort). A new `morning_snapshot_date` column on `Notification` lets later jobs (the 14:00 `health_backfill` plus any later morning tick) detect a stale-based morning notification and re-run `run_morning_checkin(force=True)`, which uses a new `upsert_morning_checkin` helper to update the body in place rather than inserting a duplicate. The cover-line payload gains `stale_date` and the frontend renders it as a small amber sub-label beneath each affected stat; the existing invisible 5px gray dot is removed.

**Tech Stack:** Python 3.11, Flask 3.x, SQLAlchemy, APScheduler, Alembic, pytest, in-memory SQLite for integration tests. Frontend is plain ES2020 + handwritten CSS.

**Reference spec:** `docs/superpowers/specs/2026-05-21-morning-checkin-staleness-design.md`

---

## File Structure

| Path | Responsibility | Action |
|---|---|---|
| `running_coach_ai/database/models.py` | SQLAlchemy ORM definitions | **Modify:** add `Notification.morning_snapshot_date` (Task 1) |
| `running_coach_ai/database/migrations/versions/r4n5o6p7q8r9_add_morning_snapshot_date.py` | Alembic migration | **Create** (Task 1) |
| `running_coach_ai/coach/adapter.py` | Morning check-in orchestration | **Modify:** gate, noon-cutoff fallback, staleness flag, prompt warning (Tasks 2, 4, 5) |
| `running_coach_ai/coach/notify.py` | Notification writes | **Modify:** add `upsert_morning_checkin` helper (Task 3) |
| `running_coach_ai/scheduler/health_backfill.py` | Daily 14:00 health backfill job | **Modify:** trigger re-fire after writing today's snapshot (Task 6) |
| `running_coach_ai/web/api/today.py` | Today Card endpoint | **Modify:** `_morning_cover_lines` emits `stale_date` (Task 7) |
| `running_coach_ai/web/api/magazine.py` | Magazine endpoint | **Modify:** morning section emits `stale_date` (Task 7) |
| `running_coach_ai/web/static/magazine.html` | Cover stats markup | **Modify:** sub-label slot under each stat label (Task 8) |
| `running_coach_ai/web/static/magazine.css` | Today Card styles | **Modify:** add `.td-stat-l-sub` rule, remove `data-stale` dot (Task 8) |
| `running_coach_ai/web/static/magazine.js` | `tdRender` hydration | **Modify:** populate stale sub-label (Task 8) |
| `scripts/diagnose_morning.py` | Diagnostic script | **Modify:** pass `now_local` to gate (Task 2) |
| `running_coach_ai/web/api/admin.py` | `/api/admin/morning-diagnostic` endpoint | **Modify:** pass `now_local` to gate (Task 2) |
| `tests/unit/test_morning_checkin_polling.py` | Gate behavior tests | **Modify:** new tests for fetch-fail-before-noon, fire-with-stale-at-noon |
| `tests/unit/test_morning_checkin_dedup.py` | Dedup + upsert tests | **Modify:** new tests for in-place update |
| `tests/integration/web/test_today_api.py` | Today Card integration tests | **Modify:** stale_date rendering on stale fallback |

---

## Task 1: Add `Notification.morning_snapshot_date` column + Alembic migration

**Files:**
- Modify: `running_coach_ai/database/models.py`
- Create: `running_coach_ai/database/migrations/versions/r4n5o6p7q8r9_add_morning_snapshot_date.py`

### Goal

Add a nullable `Date` column to the `notifications` table. It will track which day's snapshot fed the morning check-in body — `today` for fresh-data fires, an earlier date for stale-data fires, `NULL` for "no morning report today" fires.

- [ ] **Step 1.1: Modify the SQLAlchemy model**

Open `running_coach_ai/database/models.py`. Find the `Notification` class (around line 365). After `read_at = Column(DateTime, nullable=True)`, add:

```python
    morning_snapshot_date = Column(Date, nullable=True)
```

If `Date` is not already in the imports at the top of `models.py`, add it (look for the existing SQLAlchemy import line; `Date` lives in `sqlalchemy`). Run `grep -n "^from sqlalchemy" running_coach_ai/database/models.py` first — if `Date` is not in the import list, add it.

- [ ] **Step 1.2: Create the Alembic migration**

Create the new migration file at `running_coach_ai/database/migrations/versions/r4n5o6p7q8r9_add_morning_snapshot_date.py` with this exact content:

```python
"""Add morning_snapshot_date to notifications.

Records which day's health snapshot fed a morning check-in body so the
14:00 health-backfill job (and later morning ticks) can detect stale-
based notifications and re-fire the check-in in place.

Revision ID: r4n5o6p7q8r9
Revises: q3m4n5o6p7q8
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa

revision = "r4n5o6p7q8r9"
down_revision = "q3m4n5o6p7q8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "notifications",
        sa.Column("morning_snapshot_date", sa.Date(), nullable=True),
    )


def downgrade():
    op.drop_column("notifications", "morning_snapshot_date")
```

- [ ] **Step 1.3: Apply the migration to the local dev DB**

Run: `alembic upgrade head`

Expected output: `INFO  [alembic.runtime.migration] Running upgrade q3m4n5o6p7q8 -> r4n5o6p7q8r9, Add morning_snapshot_date to notifications.`

If alembic errors out claiming the revision can't be found, run `alembic current` to inspect the head and confirm `q3m4n5o6p7q8` is the previous head. If it isn't, update `down_revision` to whatever `alembic current` reports.

- [ ] **Step 1.4: Verify the column exists**

Run (assuming SQLite at the default path; adjust if `DB_PATH` is set):

```bash
python -c "import sqlite3; c=sqlite3.connect('coach.db' if __import__('os').path.exists('coach.db') else '/data/coach.db'); print([r[1] for r in c.execute('PRAGMA table_info(notifications)').fetchall()])"
```

Expected: the printed list of column names contains `morning_snapshot_date`.

If your local dev DB lives elsewhere (e.g. you don't have one), skip this step and verify in the integration tests instead — `Base.metadata.create_all(engine)` in the test harness picks up the model change automatically.

- [ ] **Step 1.5: Commit**

```bash
git add running_coach_ai/database/models.py running_coach_ai/database/migrations/versions/r4n5o6p7q8r9_add_morning_snapshot_date.py
git commit -m "Add morning_snapshot_date column to notifications

Records which day's health snapshot fed the morning check-in body so
later jobs (health_backfill at 14:00, later morning ticks) can detect
a stale-based notification and re-fire the check-in in place."
```

---

## Task 2: Defer the morning gate when Garmin fetch fails before noon

**Files:**
- Modify: `running_coach_ai/coach/adapter.py`
- Modify: `scripts/diagnose_morning.py`
- Modify: `running_coach_ai/web/api/admin.py`
- Modify: `tests/unit/test_morning_checkin_polling.py`

### Goal

When `garmin_fetch_failed=True` is the *only* reason we're not yet able to proceed, defer to the next 30-min tick if we're still before noon local. Past noon, fall through with whatever stored data exists (existing post-noon behavior preserved). Tests pin both branches.

- [ ] **Step 2.1: Write failing unit tests**

Open `tests/unit/test_morning_checkin_polling.py`. Append these two tests at the end of the file (preserve all existing tests):

```python
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
```

- [ ] **Step 2.2: Run the new tests to verify they fail**

Run: `pytest tests/unit/test_morning_checkin_polling.py::test_should_wait_when_fetch_fails_before_noon tests/unit/test_morning_checkin_polling.py::test_should_not_wait_when_fetch_fails_after_noon -v`

Expected: 2 failures. The most likely failure is a `TypeError: _should_wait_for_morning_data() got an unexpected keyword argument 'now_local'` — that's correct, we haven't added the parameter yet.

Also check whether any *existing* tests in this file call `_should_wait_for_morning_data` — `grep -n "_should_wait_for_morning_data" tests/unit/test_morning_checkin_polling.py`. If yes, those will also break in Step 2.3 when we change the signature; we'll fix them in Step 2.4.

- [ ] **Step 2.3: Update `_should_wait_for_morning_data` to accept and use `now_local`**

Open `running_coach_ai/coach/adapter.py`. Replace the existing function at lines 81-93:

```python
def _should_wait_for_morning_data(snapshot, garmin_fetch_failed: bool, athlete: Athlete) -> bool:
    """True when we should defer the check-in until Garmin processes more data.

    - Athletes without Garmin credentials never wait (we have no source of data).
    - Failed live fetch (auth/network) is treated as 'proceed with stored data'
      rather than waiting forever — the failure has already been logged.
    - With Garmin credentials and a successful (or no-op) fetch: wait until either
      Training Readiness is published OR sleep_score is captured.
    """
    if not athlete.garmin_email:
        return False
    if garmin_fetch_failed:
        return False
    return not _garmin_morning_data_complete(snapshot)
```

with:

```python
def _should_wait_for_morning_data(
    snapshot,
    garmin_fetch_failed: bool,
    athlete: Athlete,
    *,
    now_local: datetime,
) -> bool:
    """True when we should defer the check-in until Garmin processes more data.

    - Athletes without Garmin credentials never wait (we have no source of data).
    - A failed live Garmin fetch *before noon local* still defers, so the
      30-minute IntervalTrigger gets another shot at Garmin. Past noon we fall
      through with whatever stored snapshot exists (FR: prefer stale over
      silence at the noon cutoff).
    - With Garmin credentials and a successful (or no-op) fetch: wait until
      either Training Readiness is published OR sleep_score is captured.
    """
    if not athlete.garmin_email:
        return False
    past_noon = now_local.hour >= 12
    if garmin_fetch_failed:
        return not past_noon
    return not _garmin_morning_data_complete(snapshot)
```

- [ ] **Step 2.4: Update the call site inside `run_morning_checkin`**

In `running_coach_ai/coach/adapter.py`, find the call site at line 159:

```python
    if not force and _should_wait_for_morning_data(snapshot, garmin_fetch_failed, athlete):
```

Replace with:

```python
    if not force and _should_wait_for_morning_data(
        snapshot, garmin_fetch_failed, athlete, now_local=now_local
    ):
```

`now_local` is already computed at line 121.

- [ ] **Step 2.5: Update `scripts/diagnose_morning.py`**

In `scripts/diagnose_morning.py`, find the call at line 125:

```python
    wait = _should_wait_for_morning_data(snap, garmin_fetch_failed=False, athlete=a)
```

Replace with:

```python
    wait = _should_wait_for_morning_data(
        snap, garmin_fetch_failed=False, athlete=a, now_local=now_local
    )
```

`now_local` is already computed at line 72 of that script.

- [ ] **Step 2.6: Update `running_coach_ai/web/api/admin.py`**

Search the file for `_should_wait_for_morning_data`:

```bash
grep -n "_should_wait_for_morning_data" running_coach_ai/web/api/admin.py
```

For each call, append `now_local=now_local` to the kwargs. The endpoint already computes `now_local` per athlete (see the existing `now_local = datetime.now(tz)` pattern in `admin_morning_diagnostic`). If the call sits inside an athlete-loop where `now_local` is in scope, just pass it; otherwise compute it from `tz` immediately above the call.

- [ ] **Step 2.7: Re-run the unit tests — all green**

Run: `pytest tests/unit/test_morning_checkin_polling.py -v`

Expected: all tests pass (the three new ones + every pre-existing test in the file). If a pre-existing test fails with `got an unexpected keyword argument 'now_local'`, that test was calling the gate without it — update those call sites to pass `now_local=datetime(2026,1,1,8,0,tzinfo=ZoneInfo("UTC"))` (or any morning timestamp; the existing tests don't care about clock specifically).

- [ ] **Step 2.8: Lint**

Run: `ruff check running_coach_ai/coach/adapter.py scripts/diagnose_morning.py running_coach_ai/web/api/admin.py tests/unit/test_morning_checkin_polling.py`

Expected: clean. If ruff complains about an unused import in any file, remove it.

- [ ] **Step 2.9: Commit**

```bash
git add running_coach_ai/coach/adapter.py scripts/diagnose_morning.py running_coach_ai/web/api/admin.py tests/unit/test_morning_checkin_polling.py
git commit -m "Defer morning gate when Garmin fetch fails before noon

Previously a failed live-fetch immediately bypassed the wait gate and
locked in yesterday's snapshot as today's check-in. Now we defer until
the next 30-min tick has a chance to retry; past noon we still fall
through so we never wait forever."
```

---

## Task 3: Add `upsert_morning_checkin` helper to `coach/notify.py`

**Files:**
- Modify: `running_coach_ai/coach/notify.py`
- Modify: `tests/unit/test_morning_checkin_dedup.py`

### Goal

A single helper that either inserts a new `morning_checkin` Notification for today or updates an existing one's `body` + `morning_snapshot_date` in place. The "in-place update" path is what enables the silent overwrite when fresh data arrives later.

- [ ] **Step 3.1: Write failing unit tests**

Append to `tests/unit/test_morning_checkin_dedup.py`:

```python
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

    # Exactly one row in DB for today
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
        morning_snapshot_date=date(2026, 5, 20),  # yesterday
        today_local=date(2026, 5, 21),
    )
    db.commit()
    first_id = first.id

    second = upsert_morning_checkin(
        db, athlete,
        body="Fresh-data body",
        morning_snapshot_date=date(2026, 5, 21),  # today
        today_local=date(2026, 5, 21),
    )
    db.commit()

    assert second.id == first_id  # same row, not a new insert
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

    assert today_notif.id != yesterday_id  # new row, yesterday's left alone
    rows = db.query(Notification).filter(
        Notification.athlete_id == athlete.id, Notification.kind == "morning_checkin"
    ).order_by(Notification.created_at.asc()).all()
    assert len(rows) == 2
    assert rows[0].id == yesterday_id
    assert rows[0].body == "Yesterday's body"
```

If `tests/unit/test_morning_checkin_dedup.py` doesn't already define an `in_memory_db` fixture, search for one in `conftest.py` files at the same directory level (`grep -rn "in_memory_db" tests/`). If no shared fixture exists, prepend this fixture to the test file:

```python
import pytest
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
```

- [ ] **Step 3.2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_morning_checkin_dedup.py -v -k "upsert_morning_checkin"`

Expected: 3 failures with `ImportError: cannot import name 'upsert_morning_checkin' from 'running_coach_ai.coach.notify'`.

- [ ] **Step 3.3: Implement `upsert_morning_checkin`**

Append to `running_coach_ai/coach/notify.py`:

```python
def upsert_morning_checkin(
    db: Session,
    athlete: Athlete,
    *,
    body: str,
    morning_snapshot_date,  # date | None
    today_local,            # date
    title: str = "Morning check-in",
    action_path: str = "/#morning",
) -> Notification:
    """Insert or update today's morning_checkin Notification.

    "Today" is athlete-local. If a `morning_checkin` row already exists with
    created_at on `today_local`, update its body / morning_snapshot_date /
    updated_at fields and return it. Otherwise insert a new row.

    The two-row-per-day case (yesterday's row + today's upsert) is guarded
    against by filtering created_at to today_local's bounds.

    Caller is responsible for db.commit().
    """
    from datetime import datetime, time, timedelta, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(athlete.timezone or "America/New_York")
    day_start_local = datetime.combine(today_local, time.min, tzinfo=tz)
    day_start_utc = day_start_local.astimezone(timezone.utc).replace(tzinfo=None)
    day_end_utc = (day_start_local + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)

    existing = (
        db.query(Notification)
        .filter(
            Notification.athlete_id == athlete.id,
            Notification.kind == "morning_checkin",
            Notification.created_at >= day_start_utc,
            Notification.created_at < day_end_utc,
        )
        .order_by(Notification.created_at.desc())
        .first()
    )

    if existing is not None:
        existing.body = body
        existing.morning_snapshot_date = morning_snapshot_date
        existing.read_at = None  # silent overwrite — surface as unread again
        db.flush()
        logger.info(
            "upsert_morning_checkin athlete=%d UPDATED notif id=%d snap=%s",
            athlete.id, existing.id, morning_snapshot_date,
        )
        return existing

    n = Notification(
        athlete_id=athlete.id,
        kind="morning_checkin",
        title=title,
        body=body,
        action_path=action_path,
        morning_snapshot_date=morning_snapshot_date,
    )
    db.add(n)
    db.flush()
    logger.info(
        "upsert_morning_checkin athlete=%d INSERTED notif id=%d snap=%s",
        athlete.id, n.id, morning_snapshot_date,
    )
    return n
```

- [ ] **Step 3.4: Re-run the tests — all green**

Run: `pytest tests/unit/test_morning_checkin_dedup.py -v -k "upsert_morning_checkin"`

Expected: 3 passed.

Also run the full file to make sure no existing test regressed:

```
pytest tests/unit/test_morning_checkin_dedup.py -v
```

- [ ] **Step 3.5: Lint**

Run: `ruff check running_coach_ai/coach/notify.py tests/unit/test_morning_checkin_dedup.py`

Expected: clean.

- [ ] **Step 3.6: Commit**

```bash
git add running_coach_ai/coach/notify.py tests/unit/test_morning_checkin_dedup.py
git commit -m "Add upsert_morning_checkin helper for in-place body updates

Lets later jobs (health_backfill at 14:00, later morning ticks) rewrite
the morning_checkin notification body when fresh data lands, without
inserting a duplicate row."
```

---

## Task 4: Wire `run_morning_checkin` to use the upsert + track snapshot staleness + add stale-data prompt warning

**Files:**
- Modify: `running_coach_ai/coach/adapter.py`

### Goal

Replace the current `notify(...)` call inside `run_morning_checkin` with `upsert_morning_checkin(...)`, tag the notification with `morning_snapshot_date`, track whether the snapshot we're using is stale, and prepend a hard staleness instruction to the prompt when it is.

- [ ] **Step 4.1: Update `run_morning_checkin` body**

Open `running_coach_ai/coach/adapter.py`. Find and replace lines 172-196 (the fallback + health_text section) with:

```python
    # Fall back to the most recent stored snapshot if live fetch returned nothing at all.
    snapshot_is_stale = False
    if snapshot is None:
        from running_coach_ai.coach.health_lookup import resolve_recent_snapshot
        fallback, fallback_is_stale = resolve_recent_snapshot(db_session, athlete.id, today)
        if fallback is not None:
            snapshot = fallback
            snapshot_is_stale = fallback_is_stale  # True by definition (today's was missing)
            logger.info(
                "Using stale health snapshot from %s for athlete %d (no fresh data)",
                snapshot.date, athlete.id,
            )
    else:
        snapshot_is_stale = (snapshot.date != today)

    if snapshot:
        date_label = "today" if snapshot.date == today else snapshot.date.isoformat()
        health_parts = [
            f"HRV: {snapshot.hrv_score} ({snapshot.hrv_status or 'N/A'})",
            f"Sleep score: {snapshot.sleep_score}",
            f"Resting HR: {snapshot.resting_hr} bpm",
            f"Body battery: {snapshot.body_battery_start}",
            f"Stress avg: {snapshot.stress_avg}",
        ]
        if snapshot.training_readiness is not None:
            health_parts.append(f"Training readiness: {snapshot.training_readiness}")
        health_text = ", ".join(health_parts)
        if snapshot.date != today:
            health_text += f" (data from {date_label})"
```

(The only structural change vs. the original is the `snapshot_is_stale` flag now propagates downstream.)

- [ ] **Step 4.2: Add the stale-data prompt prefix**

In `running_coach_ai/coach/adapter.py`, find the prompt build (line 247-252):

```python
    # Build prompt and call Claude
    prompt = MORNING_CHECKIN_PROMPT.format(
        name=athlete.name,
        todays_session=session_text,
        health_data=health_text,
        weather=weather_text,
    )
```

Replace with:

```python
    # Build prompt and call Claude
    prompt = MORNING_CHECKIN_PROMPT.format(
        name=athlete.name,
        todays_session=session_text,
        health_data=health_text,
        weather=weather_text,
    )

    # When the snapshot we're working with is from a previous day (because
    # Garmin hadn't synced last night's data by the noon cutoff), tell Claude
    # explicitly so it doesn't restate yesterday's HRV as if it were today's.
    if snapshot is not None and snapshot_is_stale:
        stale_date_label = snapshot.date.isoformat()
        prompt = (
            f"IMPORTANT: Garmin has NOT synced this morning's overnight data. "
            f"The health numbers below are from {stale_date_label}, not today. "
            f"Open the **Today.** tagline with a clear acknowledgment that today's "
            f"readings aren't available yet. Do not state HRV/sleep/Body Battery/RHR "
            f"as if they were last night's. You may reference the stored numbers as "
            f"a trend or context (e.g. 'two days ago HRV was X'), but never as "
            f"current readings.\n\n" + prompt
        )
```

- [ ] **Step 4.3: Replace `notify(...)` with `upsert_morning_checkin(...)`**

In `running_coach_ai/coach/adapter.py`, find the notify block (lines 274-280):

```python
        notify(
            db_session, athlete,
            kind="morning_checkin",
            title="Morning check-in",
            body=response,
            action_path="/#morning",
        )
```

Replace with:

```python
        from running_coach_ai.coach.notify import upsert_morning_checkin
        upsert_morning_checkin(
            db_session, athlete,
            body=response,
            morning_snapshot_date=(snapshot.date if snapshot else None),
            today_local=today,
        )
```

The existing `from running_coach_ai.coach.notify import notify` import a few lines above can stay (it's still used elsewhere) or be removed if grep says it isn't. Verify with:

```bash
grep -n "from running_coach_ai.coach.notify import" running_coach_ai/coach/adapter.py
grep -n "\bnotify(" running_coach_ai/coach/adapter.py
```

If `notify(` appears nowhere else in the file, swap the line `from running_coach_ai.coach.notify import notify` for `from running_coach_ai.coach.notify import upsert_morning_checkin` and remove the local import inside the function.

- [ ] **Step 4.4: Run all the morning-checkin tests**

Run: `pytest tests/unit/test_morning_checkin_polling.py tests/unit/test_morning_checkin_dedup.py -v`

Expected: all pass. If `test_morning_checkin_dedup` had a test that asserted exactly one `notify()` call, it might need updating — but since `upsert_morning_checkin` produces the same row count behavior for the first-call-of-the-day case, it should still pass.

- [ ] **Step 4.5: Lint**

Run: `ruff check running_coach_ai/coach/adapter.py`

Expected: clean.

- [ ] **Step 4.6: Commit**

```bash
git add running_coach_ai/coach/adapter.py
git commit -m "Route morning check-in through upsert + tag snapshot date

Tracks whether the snapshot we're using is from today or earlier; when
stale, prepends a strong instruction to the Claude prompt so the
rationale acknowledges Garmin hasn't synced rather than restating
yesterday's HRV as if it were last night's. Notification rows now
carry morning_snapshot_date so later jobs can detect stale-based
fires and re-fire."
```

---

## Task 5: Add the noon "no data at all" fallback path

**Files:**
- Modify: `running_coach_ai/coach/adapter.py`
- Modify: `tests/unit/test_morning_checkin_polling.py`

### Goal

If we reach noon local with no live fetch, no stored snapshot, and no stale fallback (e.g. a brand-new athlete whose Garmin has never returned anything), write a short "no morning report today" notification instead of returning silently. `morning_snapshot_date=None` on that row marks it eligible for re-fire when the 14:00 backfill (or a later tick) gets data.

- [ ] **Step 5.1: Write a failing test**

Append to `tests/unit/test_morning_checkin_polling.py`:

```python
def test_noon_no_data_writes_no_report_notification(in_memory_db_with_athlete, monkeypatch):
    """After noon, with NO snapshot at all, fire a 'no morning report today'
    notification with morning_snapshot_date=None."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import patch
    from running_coach_ai.coach.adapter import run_morning_checkin
    from running_coach_ai.database.models import Notification

    db, athlete = in_memory_db_with_athlete
    athlete.garmin_email = None  # no credentials → gate returns False, falls through
    db.commit()

    today = date(2026, 5, 21)
    fake_now = datetime(2026, 5, 21, 12, 30, tzinfo=ZoneInfo("America/New_York"))

    # Patch datetime.now inside the adapter module to control "now"
    import running_coach_ai.coach.adapter as adapter_mod
    real_datetime = adapter_mod.datetime

    class _FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz else fake_now.replace(tzinfo=None)

    with patch.object(adapter_mod, "datetime", _FakeDatetime):
        run_morning_checkin(athlete, db)

    notif = db.query(Notification).filter(
        Notification.athlete_id == athlete.id, Notification.kind == "morning_checkin"
    ).first()
    assert notif is not None
    assert notif.morning_snapshot_date is None
    assert "No morning report today" in notif.body
```

If `in_memory_db_with_athlete` doesn't exist in the file, define it the same way as Task 3's fixture (just rename `in_memory_db` to `in_memory_db_with_athlete` to keep them distinct).

- [ ] **Step 5.2: Run to verify it fails**

Run: `pytest tests/unit/test_morning_checkin_polling.py::test_noon_no_data_writes_no_report_notification -v`

Expected: failure. Most likely the existing code at adapter.py line 130 sets `health_text = "No Garmin data available."` and at lines 240-281 still calls Claude with that prompt and writes a Claude-generated body — the test will fail because the body won't contain the literal string "No morning report today".

- [ ] **Step 5.3: Add the no-data branch**

In `running_coach_ai/coach/adapter.py`, find the just-finished Task 4 block (the snapshot resolution at lines 172-196 after Task 4's edits). Add this new block immediately after that block (before the weather fetch at line 198):

```python
    # If we've reached noon with no snapshot at all (no fresh fetch, no stored
    # today's row, no recent fallback), surface that explicitly rather than
    # silently going dark for the day. The next health-backfill / morning tick
    # that gets data will overwrite this row in place via upsert_morning_checkin.
    if snapshot is None and now_local.hour >= 12:
        from running_coach_ai.coach.notify import upsert_morning_checkin
        no_data_body = (
            "**Today.** No morning report yet — Garmin hasn't synced your overnight data.\n\n"
            "Check that your watch is paired, the battery is alive, and you've recorded "
            "a sleep session. I'll refresh this report automatically as soon as your data "
            "comes through."
        )
        athlete.last_morning_checkin_date = today
        upsert_morning_checkin(
            db_session, athlete,
            body=no_data_body,
            morning_snapshot_date=None,
            today_local=today,
        )
        db_session.commit()
        logger.info("Morning check-in for athlete %d: no-data notification written", athlete.id)
        return
```

- [ ] **Step 5.4: Run all morning-checkin tests**

Run: `pytest tests/unit/test_morning_checkin_polling.py tests/unit/test_morning_checkin_dedup.py -v`

Expected: all pass including the new test. If the previous adapter behavior was "return silently" and some pre-existing test counted "no notification" as success in this scenario, that test will need updating — read its docstring and either replace its assertion with "notification body contains 'No morning report'" or delete the obsolete test if the new behavior subsumes it.

- [ ] **Step 5.5: Lint**

Run: `ruff check running_coach_ai/coach/adapter.py tests/unit/test_morning_checkin_polling.py`

Expected: clean.

- [ ] **Step 5.6: Commit**

```bash
git add running_coach_ai/coach/adapter.py tests/unit/test_morning_checkin_polling.py
git commit -m "Write 'no morning report today' notification at noon when no data

Previously the morning job would silently return for the day if Garmin
had never returned any snapshot (live or stored). That leaves the
dashboard dark. Now we write an explicit notification with
morning_snapshot_date=NULL so the next data-bearing tick or the 14:00
health-backfill re-fires the check-in via upsert."
```

---

## Task 6: Re-fire morning check-in from `health_backfill` when fresh data arrives

**Files:**
- Modify: `running_coach_ai/scheduler/health_backfill.py`
- Modify: `tests/unit/scheduler/test_jobs.py` (or create `tests/unit/scheduler/test_health_backfill.py` if the former is too crowded)

### Goal

After the 14:00 backfill job successfully writes today's snapshot for an athlete, check whether that athlete has a `morning_checkin` Notification for today whose `morning_snapshot_date` is stale (not equal to today, or NULL). If so, call `run_morning_checkin(athlete, force=True)` to regenerate the body — the upsert helper updates the same row in place.

- [ ] **Step 6.1: Write a failing test**

Decide first where the test lives. Run:

```bash
ls tests/unit/scheduler/
```

If `test_health_backfill.py` doesn't exist, create it at `tests/unit/scheduler/test_health_backfill.py`. Use this content:

```python
"""Tests for the health-backfill job's morning-checkin re-fire trigger."""
from __future__ import annotations

from datetime import date, datetime, timedelta
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
        garmin_password_encrypted="enc",
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

    # Patch the Garmin client and parser so backfill writes today's snapshot
    # without making any network calls
    fresh_snap = HealthSnapshot(
        athlete_id=athlete.id, date=today,
        hrv_score=55, hrv_status="balanced",
        sleep_score=85, sleep_duration_seconds=int(7.4 * 3600),
        resting_hr=48, body_battery_start=78, body_battery_end=72,
    )
    db.add(fresh_snap)
    db.commit()

    # Patch get_session to use our in-memory session, mock the Garmin pipeline,
    # and mock run_morning_checkin so we just observe that it was called
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
        "running_coach_ai.coach.adapter.run_morning_checkin"
    ) as mock_run:
        from running_coach_ai.scheduler.health_backfill import _run_health_backfill
        _run_health_backfill()

    # run_morning_checkin should have been called with force=True
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
        "running_coach_ai.coach.adapter.run_morning_checkin"
    ) as mock_run:
        from running_coach_ai.scheduler.health_backfill import _run_health_backfill
        _run_health_backfill()

    assert not mock_run.called, "run_morning_checkin should NOT have been invoked"
```

- [ ] **Step 6.2: Run to verify failure**

Run: `pytest tests/unit/scheduler/test_health_backfill.py -v`

Expected: 2 failures. The first will fail because `_run_health_backfill` doesn't currently invoke `run_morning_checkin`.

- [ ] **Step 6.3: Add the re-fire trigger to `health_backfill`**

In `running_coach_ai/scheduler/health_backfill.py`, find the per-athlete try block (the one that calls `parse_health_snapshot` at line 75). After the existing `logger.info("Health backfill: updated snapshot ...")` line, add:

```python
                # If a morning_checkin notification already fired today with
                # stale or null morning_snapshot_date, re-fire so the rationale
                # text matches the now-fresh snapshot. Upsert in coach/notify.py
                # updates the same row in place.
                from datetime import datetime as _dt, time as _time, timedelta as _td, timezone as _tz
                day_start_local = _dt.combine(today, _time.min, tzinfo=tz)
                day_start_utc = day_start_local.astimezone(_tz.utc).replace(tzinfo=None)
                day_end_utc = (day_start_local + _td(days=1)).astimezone(_tz.utc).replace(tzinfo=None)

                from running_coach_ai.database.models import Notification as _Notification
                from sqlalchemy import or_ as _or, and_ as _and

                stale_morning = (
                    db_session.query(_Notification)
                    .filter(
                        _Notification.athlete_id == athlete.id,
                        _Notification.kind == "morning_checkin",
                        _Notification.created_at >= day_start_utc,
                        _Notification.created_at < day_end_utc,
                        _or(
                            _Notification.morning_snapshot_date.is_(None),
                            _Notification.morning_snapshot_date != today,
                        ),
                    )
                    .first()
                )
                if stale_morning is not None:
                    from running_coach_ai.coach.adapter import run_morning_checkin
                    logger.info(
                        "Health backfill: re-firing morning check-in for athlete %d (stale notif id=%d)",
                        athlete.id, stale_morning.id,
                    )
                    try:
                        run_morning_checkin(athlete, db_session, force=True)
                    except Exception as e:
                        logger.error(
                            "Re-fire of morning_checkin failed for athlete %d: %s",
                            athlete.id, e,
                        )
```

- [ ] **Step 6.4: Re-run the tests**

Run: `pytest tests/unit/scheduler/test_health_backfill.py -v`

Expected: 2 passed.

If the first test still fails because `mock_run.called` is False, double-check that:
- The seeded `Notification.morning_snapshot_date` is `yesterday` (stale)
- The seeded `HealthSnapshot.date` is `today`
- The patched `parse_health_snapshot` returns the snapshot (not None)
- The condition `morning_snapshot_date IS NULL OR morning_snapshot_date != today` matches the stale row

- [ ] **Step 6.5: Lint**

Run: `ruff check running_coach_ai/scheduler/health_backfill.py tests/unit/scheduler/test_health_backfill.py`

Expected: clean.

- [ ] **Step 6.6: Commit**

```bash
git add running_coach_ai/scheduler/health_backfill.py tests/unit/scheduler/test_health_backfill.py
git commit -m "Re-fire morning check-in from health_backfill when fresh data lands

After the 14:00 backfill writes today's snapshot for an athlete, look
for a morning_checkin notification whose morning_snapshot_date is
stale or NULL and re-run run_morning_checkin(force=True). The upsert
helper updates the same notification row so the athlete sees one
refreshed report, not two."
```

---

## Task 7: Emit `stale_date` on cover-line payloads — Today Card + Morning Readiness

**Files:**
- Modify: `running_coach_ai/web/api/today.py`
- Modify: `running_coach_ai/web/api/magazine.py`
- Modify: `tests/integration/web/test_today_api.py`

### Goal

Each cover-line in `_morning_cover_lines` (Today Card) and the morning section payload in `magazine.py` gets a new optional `stale_date: str | None` field. It's `None` when fresh, and a short formatted date string ("May 20") when stale.

- [ ] **Step 7.1: Write a failing integration test**

Append to `tests/integration/web/test_today_api.py` (in the US1 section, after `test_pre_run_state_with_morning_checkin`):

```python
def test_stale_health_snapshot_marks_cover_lines_with_stale_date(app_and_db):
    """When today's snapshot is missing but a 3-day-old one exists, the cover
    stats render with a stale_date label like 'May 20'."""
    from datetime import timedelta
    app, db, athlete = app_and_db
    today = _athlete_today(athlete)
    goal = _seed_goal(db, athlete.id)
    plan = _seed_plan(db, athlete.id, goal.id)
    _seed_planned_workout(db, athlete.id, plan.id, today, workout_type="tempo")
    # Seed yesterday's snapshot only — today's is missing
    yesterday = today - timedelta(days=1)
    _seed_health_snapshot(db, athlete.id, yesterday, hrv=40, hrv_status="balanced")

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

    assert data["state"] == "PRE_RUN"
    # Every cover line should carry the stale_date label
    expected_stale_label = yesterday.strftime("%b %-d") if hasattr(yesterday, "strftime") else None
    # Cross-platform — Windows strftime doesn't support %-d. Accept either form.
    expected_options = {yesterday.strftime("%b ") + str(yesterday.day), yesterday.strftime("%B ") + str(yesterday.day)}
    for line in data["cover_lines"]:
        assert line["is_stale"] is True
        assert line["stale_date"] in expected_options, (
            f"unexpected stale_date label: {line['stale_date']}"
        )
```

- [ ] **Step 7.2: Run to verify failure**

Run: `pytest tests/integration/web/test_today_api.py::test_stale_health_snapshot_marks_cover_lines_with_stale_date -v`

Expected: failure with `KeyError: 'stale_date'` because the field doesn't exist yet.

- [ ] **Step 7.3: Add a helper to format a stale-date label**

In `running_coach_ai/web/api/today.py`, add this near the other helpers (just below `_format_today_pretty` is a good spot):

```python
def _format_stale_date(snap_date: date | None) -> str | None:
    """Return 'May 20'-style label for a stale snapshot's date, or None.

    Windows-safe (no %-d) — uses `snap_date.day` to drop the leading zero.
    """
    if snap_date is None:
        return None
    return f"{snap_date.strftime('%b')} {snap_date.day}"
```

- [ ] **Step 7.4: Update `_morning_cover_lines` to emit `stale_date`**

In `running_coach_ai/web/api/today.py`, replace the existing `_morning_cover_lines` (around line 533):

```python
def _morning_cover_lines(snap: HealthSnapshot | None, is_stale: bool) -> list[dict]:
    """Four cover lines (HRV / Body Battery / Sleep / RHR) for PRE_RUN and REST_DAY."""
    hrv = snap.hrv_score if (snap and snap.hrv_score is not None) else "—"
    bb = snap.body_battery_end if (snap and snap.body_battery_end is not None) else (
        snap.body_battery_start if (snap and snap.body_battery_start is not None) else "—"
    )
    sleep_h = round(snap.sleep_duration_seconds / 3600.0, 1) if (snap and snap.sleep_duration_seconds) else "—"
    rhr = snap.resting_hr if (snap and snap.resting_hr is not None) else "—"

    return [
        {"label": "HRV ms",   "value": hrv,     "drill_to": "morning", "is_stale": is_stale},
        {"label": "Body Bat", "value": bb,      "drill_to": "morning", "is_stale": is_stale},
        {"label": "Sleep h",  "value": sleep_h, "drill_to": "morning", "is_stale": is_stale},
        {"label": "RHR",      "value": rhr,     "drill_to": "morning", "is_stale": is_stale},
    ]
```

with:

```python
def _morning_cover_lines(snap: HealthSnapshot | None, is_stale: bool) -> list[dict]:
    """Four cover lines (HRV / Body Battery / Sleep / RHR) for PRE_RUN and REST_DAY."""
    hrv = snap.hrv_score if (snap and snap.hrv_score is not None) else "—"
    bb = snap.body_battery_end if (snap and snap.body_battery_end is not None) else (
        snap.body_battery_start if (snap and snap.body_battery_start is not None) else "—"
    )
    sleep_h = round(snap.sleep_duration_seconds / 3600.0, 1) if (snap and snap.sleep_duration_seconds) else "—"
    rhr = snap.resting_hr if (snap and snap.resting_hr is not None) else "—"
    stale_date = _format_stale_date(snap.date) if (snap is not None and is_stale) else None

    return [
        {"label": "HRV ms",   "value": hrv,     "drill_to": "morning", "is_stale": is_stale, "stale_date": stale_date},
        {"label": "Body Bat", "value": bb,      "drill_to": "morning", "is_stale": is_stale, "stale_date": stale_date},
        {"label": "Sleep h",  "value": sleep_h, "drill_to": "morning", "is_stale": is_stale, "stale_date": stale_date},
        {"label": "RHR",      "value": rhr,     "drill_to": "morning", "is_stale": is_stale, "stale_date": stale_date},
    ]
```

- [ ] **Step 7.5: Update the magazine endpoint's morning section**

Open `running_coach_ai/web/api/magazine.py`. Find the morning snapshot return block around line 374-383:

```python
            "sleep_hours": sleep_h,
            "resting_hr": snap.resting_hr,
            "date_iso": snap.date.isoformat(),
            "is_stale": snap_is_stale,
        }
```

After `"is_stale": snap_is_stale,` add:

```python
            "stale_date": (
                f"{snap.date.strftime('%b')} {snap.date.day}"
                if snap_is_stale else None
            ),
```

(Inlining the helper rather than importing across modules — magazine.py already builds its own date strings nearby.)

- [ ] **Step 7.6: Run the new integration test**

Run: `pytest tests/integration/web/test_today_api.py::test_stale_health_snapshot_marks_cover_lines_with_stale_date -v`

Expected: pass.

Also run the whole Today Card integration suite to confirm no regression:

```
pytest tests/integration/web/test_today_api.py -v
```

- [ ] **Step 7.7: Lint**

Run: `ruff check running_coach_ai/web/api/today.py running_coach_ai/web/api/magazine.py tests/integration/web/test_today_api.py`

Expected: clean.

- [ ] **Step 7.8: Commit**

```bash
git add running_coach_ai/web/api/today.py running_coach_ai/web/api/magazine.py tests/integration/web/test_today_api.py
git commit -m "Emit stale_date on morning cover-line payloads

Today Card cover stats and Morning Readiness section both surface a
short 'May 20'-style label when the snapshot is older than today, so
the frontend can render an unmissable staleness marker instead of the
invisible 5px dot."
```

---

## Task 8: Frontend — render the stale sub-label on cover stats; remove the 5px dot

**Files:**
- Modify: `running_coach_ai/web/static/magazine.html`
- Modify: `running_coach_ai/web/static/magazine.css`
- Modify: `running_coach_ai/web/static/magazine.js`

### Goal

Add a sub-label slot beneath each stat's existing label that renders "FROM MAY 20" in soft amber when `line.stale_date` is set. Remove the existing 5px gray dot (`.td-stat-v[data-stale="1"]::after`). Visually verifiable but no automated test.

- [ ] **Step 8.1: Add sub-label markup in `magazine.html`**

In `running_coach_ai/web/static/magazine.html`, find the four `<button class="td-stat">` blocks (around lines 124-139). Each looks like:

```html
    <button class="td-stat" data-idx="0" type="button">
      <div class="td-stat-v" id="td-stat-v-0">—</div>
      <div class="td-stat-l" id="td-stat-l-0">—</div>
    </button>
```

For each of the four buttons (idx 0, 1, 2, 3), add a sub-label `<div>` after the existing `.td-stat-l`:

```html
    <button class="td-stat" data-idx="0" type="button">
      <div class="td-stat-v" id="td-stat-v-0">—</div>
      <div class="td-stat-l" id="td-stat-l-0">—</div>
      <div class="td-stat-l-sub" id="td-stat-l-sub-0" hidden></div>
    </button>
```

Apply to all four buttons (idx 0 through 3).

- [ ] **Step 8.2: Add CSS for the sub-label and remove the dot**

In `running_coach_ai/web/static/magazine.css`, find:

```css
.td-stat-v[data-stale="1"]::after{content:'';display:inline-block;width:5px;height:5px;border-radius:50%;background:rgba(255,255,255,.35);margin-left:6px;vertical-align:middle;}
```

Delete that rule entirely.

Then find the `.td-stat-l` rule (around line 147):

```css
.td-stat-l{font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.45);margin-top:4px;}
```

Add immediately after it:

```css
.td-stat-l-sub{font-size:8.5px;letter-spacing:.1em;text-transform:uppercase;color:rgba(255,159,79,.85);margin-top:2px;}
```

(Amber tone, distinct from the persona accent color.)

- [ ] **Step 8.3: Hydrate the sub-label in `magazine.js`**

In `running_coach_ai/web/static/magazine.js`, find the cover-lines hydration block (around line 2835, starts with `// Cover lines / stats`). Inside the `lines.slice(0, 4).forEach(...)` loop body, after the line-label hydration (`if (l) l.textContent = line.label || '';`), add:

```javascript
      // Stale sub-label ("FROM MAY 20") — present only when payload includes stale_date
      const sub = document.getElementById('td-stat-l-sub-' + i);
      if (sub) {
        if (line.stale_date) {
          sub.textContent = ('FROM ' + String(line.stale_date)).toUpperCase();
          sub.hidden = false;
        } else {
          sub.textContent = '';
          sub.hidden = true;
        }
      }
```

Also remove the now-obsolete `data-stale` attribute manipulation a few lines above:

```javascript
        if (line.is_stale || stale) v.setAttribute('data-stale', '1');
        else v.removeAttribute('data-stale');
```

Delete both lines. (The `stale` outer flag was the global "we're showing cached data" marker for the stale banner — it remains in use for the `#td-stale` banner element a bit further down; that's untouched.)

- [ ] **Step 8.4: Smoke-check via grep**

Run:

```bash
grep -n "data-stale" running_coach_ai/web/static/magazine.css running_coach_ai/web/static/magazine.js
```

Expected: no matches in either file.

Run:

```bash
grep -n "td-stat-l-sub" running_coach_ai/web/static/magazine.html running_coach_ai/web/static/magazine.css running_coach_ai/web/static/magazine.js
```

Expected: matches in all three files (markup, CSS rule, hydration).

- [ ] **Step 8.5: Commit**

```bash
git add running_coach_ai/web/static/magazine.html running_coach_ai/web/static/magazine.css running_coach_ai/web/static/magazine.js
git commit -m "Render stale-date sub-label under cover stats; drop 5px dot

When the cover-line payload includes stale_date (a short 'May 20'
label), render it in soft amber beneath each existing stat label. The
previous 5px gray dot indicator was effectively invisible — this is
unmissable. Apply the same treatment across HRV / Body Battery /
Sleep / RHR slots."
```

---

## Task 9: Final verification

After all eight task commits land:

- [ ] **Run the full affected test surface**

```bash
pytest tests/unit/test_morning_checkin_polling.py tests/unit/test_morning_checkin_dedup.py tests/unit/scheduler/test_health_backfill.py tests/integration/web/test_today_api.py tests/unit/test_today_rationale.py -v
```

Expected: all pass.

- [ ] **Run lint on touched files**

```bash
ruff check running_coach_ai/coach/adapter.py running_coach_ai/coach/notify.py running_coach_ai/scheduler/health_backfill.py running_coach_ai/web/api/today.py running_coach_ai/web/api/magazine.py running_coach_ai/web/api/admin.py running_coach_ai/database/models.py scripts/diagnose_morning.py
```

Expected: clean.

- [ ] **Working tree clean**

```bash
git status
```

Expected: only the design doc + plan doc and the 8 task commits (no uncommitted leftover).

- [ ] **Push to origin so Railway picks up the deploy**

```bash
git push -u origin 008-morning-staleness
```

(The branch tracks a new origin reference since this is its first push. Railway should be configured to track this branch.)

- [ ] **Tomorrow morning verification**

After tomorrow's 06:00 EDT morning tick:

1. Open `https://<railway-domain>/api/admin/morning-diagnostic`. Confirm `health_snapshot_today` is populated for your athlete, and the `notification_today.morning_snapshot_date` matches today's date.
2. Open the Today Card. Confirm the cover stats show today's numbers with no amber "FROM <date>" sub-label.
3. If Garmin sync was flaky and the snapshot didn't arrive until 14:00, confirm the morning notification body got rewritten after the backfill (the rationale should reflect today's numbers, not an apology about missing data).
