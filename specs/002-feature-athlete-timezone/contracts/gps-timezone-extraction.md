# Contract: GPS Coordinate Extraction from Garmin Activities

**Branch**: `002-feature-athlete-timezone` | **Date**: 2026-04-04

Defines how the system extracts GPS starting coordinates from Garmin activity detail responses for timezone derivation.

---

## Trigger

Called during activity ingestion (`_ingest_and_feedback`) immediately after `parse_activity_summary` completes, before generating post-run feedback.

---

## Input

```
Method: get_activity_details(activity_id)
Already called earlier in the ingestion pipeline — the same `detail` dict is reused.
No additional Garmin API calls are made.
```

---

## GPS Extraction Path

```
detail
└── activityDetail
    └── activity
        ├── summaryDTO               ← PRIMARY
        │   ├── startLatitude        → lat
        │   └── startLongitude       → lon
        └── lapDTOs[]                ← FALLBACK
            └── [0]
                ├── startLatitude    → lat
                └── startLongitude   → lon
```

### Resolution Rules

| Condition                                                                 | Result                                        |
| ------------------------------------------------------------------------- | --------------------------------------------- |
| `summaryDTO.startLatitude` present and non-zero                           | Use summaryDTO values                         |
| `summaryDTO` null/absent, `lapDTOs[0].startLatitude` present and non-zero | Use first lap values                          |
| Both paths null or exactly 0.0                                            | Return `None` — no GPS (treadmill/indoor run) |
| Coordinates resolve to international waters (timezonefinder returns None) | Retain previous timezone                      |

---

## Output

```python
tuple[float, float] | None
# (latitude, longitude) or None if no GPS
```

---

## Timezone Derivation (downstream)

```python
from running_coach_ai.coach.timezone_utils import derive_timezone_from_coords

coords = extract_activity_start_coords(detail)
if coords:
    new_tz = derive_timezone_from_coords(*coords)
    if new_tz and new_tz != athlete.timezone:
        athlete.timezone = new_tz
        db_session.commit()
        # re-register morning job
        logger.info("Timezone updated for Athlete %d: %s -> %s", athlete.id, old_tz, new_tz)
```

---

## Zero-Coordinate Rejection

Garmin records `0.0` / `0.0` for activities with no GPS signal. This is distinguished from a valid coordinate of exactly 0°N 0°E (Gulf of Guinea) by the tolerance check `abs(lat) > 0.001 or abs(lon) > 0.001`. A legitimate activity at 0°N 0°E would still trigger timezone derivation; `timezonefinder` would return `"Africa/Abidjan"` for the nearest land mass, which is acceptable behaviour.

---

## Error Handling

- If `get_activity_details` already failed upstream, this function is never called (the ingestion pipeline aborts).
- If `timezonefinder` raises an unexpected exception, it is caught, logged at WARNING level, and the existing timezone is retained.
- Scheduler job re-registration failures are logged at ERROR level; the DB commit of the new timezone is NOT rolled back (the scheduler will self-heal on restart).
