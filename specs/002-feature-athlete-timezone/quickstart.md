# Quickstart: Athlete Timezone Auto-Detection

**Branch**: `002-feature-athlete-timezone`

This guide helps a developer working on this feature understand what to build, where, and how to verify it works.

---

## What This Feature Does

1. **Onboarding** — when an athlete's home city is geocoded to coordinates, use `timezonefinder` to derive their IANA timezone instead of a hardcoded lookup.
2. **Activity ingestion** — after each new Garmin activity is parsed, extract the GPS starting coords and update the athlete's timezone (and reschedule their morning check-in) if they've changed location.
3. **Activity poller** — remove the server-time 06:00–22:00 execution gate so athletes in all timezones get their activities ingested promptly.

---

## Prerequisites

```bash
pip install timezonefinder>=6.0.0
# Or: add to requirements.txt and re-run pip install -r requirements.txt
```

---

## New Files to Create

### `running_coach_ai/coach/timezone_utils.py`

```python
"""Utility for IANA timezone derivation from GPS coordinates."""

import logging
from timezonefinder import TimezoneFinder

logger = logging.getLogger(__name__)
_tf = None  # lazily initialised module-level singleton

def _get_tf() -> TimezoneFinder:
    global _tf
    if _tf is None:
        _tf = TimezoneFinder()
    return _tf

def derive_timezone_from_coords(lat: float, lon: float) -> str | None:
    """Return IANA timezone string for (lat, lon), or None if unresolvable."""
    try:
        return _get_tf().timezone_at(lat=lat, lng=lon)
    except Exception as e:
        logger.warning("timezonefinder failed for (%s, %s): %s", lat, lon, e)
        return None
```

---

## Files to Modify

### 1. `requirements.txt`
Add: `timezonefinder>=6.0.0`

### 2. `running_coach_ai/garmin/parser.py`
Add `extract_activity_start_coords(detail: dict) -> tuple[float, float] | None` (see contract in `contracts/gps-timezone-extraction.md`).

### 3. `running_coach_ai/slack/onboarding.py`
In `_complete_onboarding`, after the city geocoding block that sets `home_lat`/`home_lon`, replace:
```python
athlete.timezone = tz   # hardcoded from city table
```
with:
```python
from running_coach_ai.coach.timezone_utils import derive_timezone_from_coords
athlete.timezone = derive_timezone_from_coords(lat, lon) or "UTC"
```

### 4. `running_coach_ai/scheduler/jobs.py`

**Remove the time gate** from `_run_activity_poll`:
```python
# DELETE these lines
if not (6 <= now.hour < 22):
    logger.debug("Outside active hours (%s), skipping activity poll", ...)
    return
```

**Add timezone update** in `_ingest_and_feedback`, after `generate_post_run_feedback(...)`:
```python
from running_coach_ai.garmin.parser import extract_activity_start_coords
from running_coach_ai.coach.timezone_utils import derive_timezone_from_coords

coords = extract_activity_start_coords(detail)
if coords:
    new_tz = derive_timezone_from_coords(*coords)
    if new_tz and new_tz != athlete.timezone:
        old_tz = athlete.timezone
        athlete.timezone = new_tz
        db_session.commit()
        logger.info("Timezone updated for Athlete %d: %s -> %s", athlete.id, old_tz, new_tz)
        try:
            import main as app_main
            from running_coach_ai.scheduler.jobs import register_athlete_morning_job
            register_athlete_morning_job(app_main.scheduler, athlete, app_main.handler.app.client)
        except Exception as sched_e:
            logger.error("Failed to re-register morning job for athlete %d: %s", athlete.id, sched_e)
```

---

## Testing

### Unit test: onboarding timezone derivation
```bash
pytest tests/unit/test_onboarding_timezone.py
```
- Mock `derive_timezone_from_coords` to return `"America/New_York"`
- Verify `athlete.timezone` is set to `"America/New_York"` after `_complete_onboarding`
- Test fallback: mock returns `None` → `athlete.timezone == "UTC"`

### Unit test: activity GPS extraction
```bash
pytest tests/unit/test_activity_timezone_update.py
```
- Provide a fake `detail` dict with `summaryDTO.startLatitude/startLongitude`
- Verify extraction returns correct tuple
- Provide a detail dict with all-zero / null coords — verify returns `None`

### Unit test: activity poll time gate removal
```bash
pytest tests/unit/test_activity_poll_no_gate.py
```
- Patch `datetime.now()` to return 03:00
- Call `_run_activity_poll` with a mocked Garmin client
- Verify it proceeds (does not return early)

### Run all unit tests
```bash
pytest tests/unit/
```

---

## Smoke Testing

After deploying the change:
1. Trigger an activity poll manually via `!admin` or by starting the scheduler.
2. Check logs for `"Timezone updated for Athlete X"` entries.
3. Verify `athletes.timezone` in SQLite matches the location of recent activities.
4. Confirm morning check-in times shifted when expected.
