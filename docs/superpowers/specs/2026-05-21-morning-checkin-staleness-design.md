# Morning Check-in: wait for fresh Garmin data, auto-refresh on backfill, mark stale data visibly

**Branch:** new branch off `007-today-card` (or directly off the deploy branch, TBD)
**Status:** design approved 2026-05-21

## Problem

This morning the Today Card displayed `HRV: 40` in the cover stats, the morning rationale, and the Morning Readiness section. The athlete's actual last-night HRV was **55 ms** per Garmin. The 40 was *yesterday's* `lastNightAvg`, surfaced through the 3-day stale-snapshot fallback in `coach/health_lookup.py`.

Diagnostic trace from `GET /api/admin/morning-diagnostic` at 07:54 EDT:

- `health_snapshot_today: null` — no snapshot written for today
- `latest_health_snapshot_date: "2026-05-20"` — most recent is yesterday
- `notification_today: {created_at_utc: "2026-05-21T10:01:10"}` — morning notification fired at 06:01 EDT
- `gate_verdict: "WAIT for health data"` — gate currently says wait, but a notification already went out

What happened:

1. **06:01 EDT** — morning job ran. Live Garmin fetch failed (rate-limit, network, or auth).
2. `_should_wait_for_morning_data` (`coach/adapter.py:81-93`) bypasses the gate when `garmin_fetch_failed=True`, so the job proceeded.
3. The stale-fallback at `adapter.py:173-181` returned yesterday's snapshot.
4. Claude generated a rationale referencing HRV 40, with a single muted `(data from 2026-05-20)` parenthetical appended to `health_text` — easy for the model to underweight.
5. The notification is locked in for the day via `last_morning_checkin_date=today`.
6. The 14:00 daily `health_backfill` will write today's real snapshot, but the morning notification's rationale paragraph stays misleading until tomorrow.
7. The cover stats grid renders the stale value with a 5px gray dot that is effectively invisible.

## Goal

Make morning check-in stop firing with stale data when retries are possible, mark stale-fallback rendering unmistakably visible, and re-fire the morning check-in when fresh data lands so all three rendering surfaces (Today Card stats, Morning Readiness section, rationale paragraph) stay consistent with each other.

## Non-goals

- No changes to the Garmin parser — `hrvSummary.lastNightAvg` is the correct field.
- No new athlete-facing settings.
- No change to the `dedup` model (one morning check-in per day) other than allowing in-place updates of the same Notification row when fresh data arrives.

## Design

### 1. Morning-job retry through noon — don't bypass the gate on fetch failure

`coach/adapter.py:81-93`:

```python
def _should_wait_for_morning_data(snapshot, garmin_fetch_failed, athlete):
    if not athlete.garmin_email:
        return False
    if garmin_fetch_failed:
        return False              # ← root cause: jumps straight to fallback
    return not _garmin_morning_data_complete(snapshot)
```

Change to: a failed Garmin fetch *before noon* causes the gate to wait too (the IntervalTrigger retries every 30 min and gets another shot at Garmin). The bypass only fires once we're past noon and the retry window is closed.

```python
def _should_wait_for_morning_data(
    snapshot, garmin_fetch_failed, athlete, *, now_local
) -> bool:
    if not athlete.garmin_email:
        return False
    past_noon = now_local.hour >= 12
    if garmin_fetch_failed:
        # Before noon: retry. After noon: don't keep waiting forever — proceed
        # with whatever stored snapshot we have (stale or not).
        return not past_noon
    return not _garmin_morning_data_complete(snapshot)
```

Callers must pass `now_local` (already computed in the morning-job body). Two call sites: `adapter.py:159` (morning job) and `scripts/diagnose_morning.py:125`. The diagnose script is read-only; pass `datetime.now(tz)`.

### 2. Noon cutoff behavior — stale data preferred over silence, "no data" as last resort

The job already handles the noon cutoff (`adapter.py:166-170`). Today the post-noon path simply *returns* without sending a notification — we'll change that to *fire with whatever's available*.

After the gate, in `run_morning_checkin`:

```python
# Existing: fall back to most recent stored snapshot if live fetch returned nothing.
if snapshot is None:
    fallback, fallback_is_stale = resolve_recent_snapshot(db_session, athlete.id, today)
    snapshot = fallback
    snapshot_is_stale = fallback_is_stale
else:
    snapshot_is_stale = (snapshot.date != today)

# NEW: post-noon "no data at all" path
if snapshot is None and now_local.hour >= 12:
    notify_no_morning_report(athlete, db_session)
    athlete.last_morning_checkin_date = today
    db_session.commit()
    return
```

`notify_no_morning_report` writes a short Notification:

> **No morning report today.** Garmin didn't sync your overnight data — check that your watch is paired, the battery is alive, and you have a recent sleep session.

Notification fields:
- `kind="morning_checkin"` (same kind so it's deduped and re-fireable)
- `morning_snapshot_date=None` (new column — see §5)
- `body` = the message above
- `details_json={"no_data": True}` so the frontend can render an explicit blank state

### 3. Re-fire when fresh data arrives

After a notification already fired today (whether with fresh or stale data), if a later snapshot write turns `morning_snapshot_date != today` into a fresh today's snapshot, re-fire the morning check-in *in place*.

**Trigger points** (two paths):

a. **Health-backfill job (14:00 daily)** — `scheduler/health_backfill.py`. After writing today's snapshot for an athlete, look up that athlete's most recent `morning_checkin` Notification with `created_at` ≥ today's local midnight. If found and `morning_snapshot_date IS DISTINCT FROM today` (covers both the stale-data case `≠ today` AND the "no data" case `IS NULL`), call `run_morning_checkin(athlete, force=True, refresh=True)`.

b. **Late morning ticks** — the 11:30/12:00 morning ticks already run and may succeed where 06:01 failed. Add the same logic to `adapter.py:run_morning_checkin`: after the body is generated, if a Notification already exists for today, UPDATE its `body`, `morning_snapshot_date`, and bump `updated_at`. Don't insert a new row.

**New helper** `coach/notify.py:upsert_morning_checkin(athlete, body, snapshot_date, db_session)`:
- If a `morning_checkin` Notification for today exists: update its `body`, `morning_snapshot_date`, `updated_at`.
- Else: insert a new one.
- Returns the Notification.

**Dedup is now:** "one morning check-in *Notification row* per day, but its body may be rewritten when fresher data lands." The `last_morning_checkin_date` field stays — it gates "have we attempted at all today?" — but it no longer gates "can we update the row?"

### 4. Make staleness visible — all three surfaces

**Cover stats (Today Card and Morning Readiness):**

Cover-line payload gets a new field:

```jsonc
{"label": "HRV ms", "value": 40, "drill_to": "morning",
 "is_stale": true, "stale_date": "May 20"}
```

Frontend renders the stale date as a small sub-label below the existing label ("HRV ms / from May 20"). The current 5px dot is removed.

**`magazine.js` cover-stats hydration** — replace the `data-stale` dot logic with an extra sub-label DOM node inserted into each stat block when `line.stale_date` is set.

**CSS** — new rule:

```css
.td-stat-l-sub{
  font-size:9px;letter-spacing:.1em;text-transform:uppercase;
  color:rgba(255,159,79,.85);   /* soft warning amber, distinct from accent */
  margin-top:2px;
}
```

Same payload shape and rendering applies to the Morning Readiness section in `web/api/magazine.py`.

**Rationale paragraph:**

When `snapshot_is_stale=True`, the morning-job prompt to Claude prepends a hard instruction:

> The athlete's overnight Garmin data has NOT synced today. The numbers below are from <date>. Open the rationale with a clear acknowledgment that you don't have today's readings yet — do not state HRV/sleep/RHR/Body Battery numbers as if they were last night's. Use the stale numbers only to reference trend ("HRV was X two days ago"), never as current readings.

After re-fire with fresh data, this instruction is omitted and Claude writes a normal rationale.

### 5. Schema — add `morning_snapshot_date` to Notification

Alembic migration adds one column:

```python
op.add_column(
    "notifications",
    sa.Column("morning_snapshot_date", sa.Date(), nullable=True),
)
```

Nullable because (a) older rows won't have it and (b) the "no data at all" notification leaves it null.

Existing `details_json` could carry this instead, but a typed column is cleaner for the backfill re-fire query (`WHERE morning_snapshot_date != today`).

### 6. Files touched

| File | Change |
|---|---|
| `running_coach_ai/coach/adapter.py` | `_should_wait_for_morning_data` takes `now_local`; gate returns True on fetch-fail-before-noon; post-noon "no data" path; staleness flag plumbed; prompt instruction when stale |
| `running_coach_ai/coach/notify.py` | New `upsert_morning_checkin` helper |
| `running_coach_ai/scheduler/morning.py` | Pass `now_local` through; no behavioral change beyond §1 |
| `running_coach_ai/scheduler/health_backfill.py` | After writing today's snapshot, re-fire morning check-in if existing Notification has stale `morning_snapshot_date` |
| `running_coach_ai/database/models.py` | `Notification.morning_snapshot_date` column |
| `running_coach_ai/database/migrations/versions/r4n5o6p7q8r9_add_morning_snapshot_date.py` | Alembic migration |
| `running_coach_ai/web/api/today.py` | Cover-line builders emit `stale_date` |
| `running_coach_ai/web/api/magazine.py` | Morning Readiness section emits `stale_date` |
| `running_coach_ai/web/static/magazine.html` | Sub-label markup for cover stats |
| `running_coach_ai/web/static/magazine.css` | `.td-stat-l-sub` rule; remove `data-stale` dot |
| `running_coach_ai/web/static/magazine.js` | Sub-label hydration; remove dot logic |
| `scripts/diagnose_morning.py` | Pass `now_local` to the gate |
| `tests/unit/test_morning_checkin_polling.py` | Gate behavior with fetch-fail + before/after noon |
| `tests/unit/test_morning_checkin_dedup.py` | Re-fire updates same notification row (no new insert) |
| `tests/integration/web/test_today_api.py` | `stale_date` rendering on stale fallback |

### 7. Verification plan

After deploy, the test scenarios:

1. **Tomorrow's first morning tick (06:00 EDT)** — Garmin fetch succeeds → fresh snapshot, notification fires normally, no `stale_date` on stats, rationale references today's numbers.
2. **Garmin fetch fails at 06:01** — gate returns True, no notification fires yet. 06:30 tick succeeds; notification fires with today's data.
3. **Garmin fetch fails until 11:30** — 12:00 tick fires with stale data from yesterday's snapshot, `morning_snapshot_date=yesterday`, all three surfaces show stale markers, Claude's rationale leads with "I don't have today's HRV yet."
4. **Same as #3, but at 14:00 backfill succeeds** — Notification row is updated in place, `morning_snapshot_date=today`, stats refresh on next page load, rationale text rewritten without stale framing.
5. **Garmin entirely unreachable all day** — at 12:00, "No morning report today" notification fires. Stats grid shows blank values. Stays that way until tomorrow.

### 8. Open question

The user prefers "overwrite silently" for the re-fire (no "updated" badge). After re-fire, the athlete who already read the stale notification may not realize the rationale changed. We accept this trade-off — the user explicitly chose silent overwrite — but worth a follow-up tick to consider: emit a small "Morning report refreshed with fresh data" WebEvent (not a Notification) so the admin Events tab shows the re-fire happened.
