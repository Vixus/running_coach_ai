# Research: Athlete Timezone Auto-Detection

**Branch**: `002-feature-athlete-timezone` | **Date**: 2026-04-04

---

## Decision 1: Timezone Derivation Library

**Decision**: Use `timezonefinder>=6.0.0` for all IANA timezone derivation from GPS coordinates.

**Rationale**: It is already referenced by FR-001 in the spec. It is a pure-Python library (no external API calls), works offline, and resolves a lat/lon pair to an IANA timezone string in O(1) time using a precomputed polygon index. No rate limits, no credentials required.

**API shape**:
```python
from timezonefinder import TimezoneFinder

tf = TimezoneFinder()               # instantiate once; thread-safe for reads
tz = tf.timezone_at(lat=51.5, lng=-0.1)   # returns "Europe/London" or None
```

`timezone_at` uses an approximation algorithm that is accurate for the vast majority of locations. The slower `certain_timezone_at` method exists for higher-confidence disambiguation near borders, but is unnecessarily expensive for this use case.

**Return value**: An IANA timezone string (e.g., `"America/New_York"`) or `None` when coordinates fall in international waters or when resolution is impossible.

**Alternatives considered**:
- `pytz.FixedOffset` / manual UTC offset: rejected — offset is not the same as a named timezone and cannot drive APScheduler CronTriggers correctly.
- `geopy` + Nominatim reverse geocoding: rejected — adds a network dependency and rate-limiting risk; `timezonefinder` satisfies the requirement without external calls.

---

## Decision 2: Garmin GPS Coordinate Extraction

**Decision**: Extract the activity starting coordinates from `get_activity_details()` using `activityDetail.activity.summaryDTO.startLatitude` / `startLongitude` as the primary path, with a fallback to `lapDTOs[0].startLatitude` / `startLongitude`.

**Rationale**: The Garmin Connect API response from `get_activity_details()` embeds GPS summary data in the `summaryDTO` sub-object under `activityDetail.activity`. This is the canonical starting position for the activity. `lapDTOs[0]` is the first lap and also reliably carries start coordinates when recorded outdoors. When both paths return null (or 0.0), the activity had no GPS recording (e.g., treadmill), and timezone derivation is skipped per the spec requirement to retain the previous timezone.

**Extraction logic**:
```python
def extract_activity_start_coords(detail: dict) -> tuple[float, float] | None:
    activity = detail.get("activityDetail", {}).get("activity", detail)

    # Primary: summaryDTO
    summary_dto = activity.get("summaryDTO", {})
    lat = summary_dto.get("startLatitude")
    lon = summary_dto.get("startLongitude")

    # Fallback: first lap
    if not lat or not lon:
        laps = activity.get("lapDTOs", [])
        if laps:
            lat = laps[0].get("startLatitude")
            lon = laps[0].get("startLongitude")

    # Reject zero coordinates (treadmill / unrecorded GPS)
    if lat and lon and (abs(lat) > 0.001 or abs(lon) > 0.001):
        return float(lat), float(lon)
    return None
```

**Treadmill / No-GPS detection**: If both paths are null or exactly 0.0, the function returns `None`. The caller skips timezone update, preserving the previous stored timezone (per spec edge case requirement).

**Alternatives considered**:
- Parsing the GPS time-series stream from `activityDetailMetrics`: rejected — requires finding `directLatitude`/`directLongitude` metric keys, which are not guaranteed present and would add complexity for what is just the starting coordinate.
- Separate `get_activity_splits()` call: rejected — adds an extra API call; lap data is already embedded in `get_activity_details()`.

---

## Decision 3: Scheduler Re-Registration Pattern

**Decision**: Reuse the existing `register_athlete_morning_job(scheduler, athlete, slack_client)` function from `scheduler/jobs.py`, accessed via `import main as app_main`, mirroring the pattern already established in `slack/onboarding.py`.

**Rationale**: The pattern is already proven in production and avoids introducing a global scheduler registry or dependency injection mechanism for a single new call site. `replace_existing=True` on `scheduler.add_job()` ensures any existing job for the athlete is atomically replaced with the new timezone.

**Error handling**: If the scheduler re-registration fails (e.g., scheduler not yet started during bootstrap), the error is caught, logged at ERROR level, and the timezone DB update is NOT rolled back. The scheduler will pick up the correct timezone on the next restart via `register_jobs()` at startup, which always reads `athlete.timezone`.

**Alternatives considered**:
- Module-level `_global_scheduler` reference in jobs.py: rejected — creates a mutable global, harder to test, and unnecessary given the import pattern works.
- A `post_ingest_hooks` callback list: rejected — over-engineering for a single call site.

---

## Decision 4: Onboarding Timezone Derivation

**Decision**: In `_complete_onboarding`, after setting `home_lat` / `home_lon` from `_parse_city_to_coords`, immediately call `derive_timezone_from_coords(lat, lon)` (wrapping `timezonefinder`) to set `athlete.timezone`. Remove the hardcoded timezone column from the city lookup table; the coords alone are kept for weather.

**Rationale**: `_parse_city_to_coords` already returns coordinates. Adding `timezonefinder` on those coordinates produces a more accurate IANA timezone than the hardcoded strings in the lookup table, and it naturally extends to any coordinates returned by future geocoding improvements. The lookup table itself is left intact for its coordinate values (used by the weather client).

**Fall-through**:
- If `timezonefinder` returns `None` (city not in lookup or ocean): fall back to `"UTC"` and log a WARNING per spec FR-002.
- If `home_lat` / `home_lon` are `None` (city not found in lookup): also fall back to `"UTC"`.

---

## Decision 5: Activity Poll Time Gate Removal

**Decision**: Remove the `if not (6 <= now.hour < 22): return` guard from `_run_activity_poll`. No replacement gating logic is needed.

**Rationale**: The guard was a pragmatic limit to reduce Garmin API calls during night hours. However, it blocks activity ingestion for athletes in different timezones when the server's local hour is outside the window. With a global user base (spec user story 3), this gate is harmful. Removing it means ~16 extra no-op poll calls per athlete per day (assuming no new activities at night), which is well within Garmin's rate limits and was accepted in the spec's assumptions.

**Impact on existing log noise**: `_run_activity_poll` calls `poll_new_activities` which already handles the case of no new activities gracefully. The removal adds a small number of extra cycles that return immediately upon finding no new activities.

---

## Decision 6: New Utility Module Placement

**Decision**: Place `derive_timezone_from_coords` in a new `running_coach_ai/coach/timezone_utils.py` module.

**Rationale**: The function is a pure utility (no I/O, no DB). The `coach/` package is already the right home for cross-cutting coach logic utilities (see `persona.py`). Keeping it separate from `garmin/` and `scheduler/` allows both to import it without circular dependencies.

**What goes in the module**: 
- `derive_timezone_from_coords(lat: float, lon: float) -> str | None` — single, focused responsibility.
- The `TimezoneFinder` instance is created lazily (module-level singleton) to avoid repeated disk reads.
