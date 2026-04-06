# Data Model: Athlete Timezone Auto-Detection

**Branch**: `002-feature-athlete-timezone` | **Date**: 2026-04-04

---

## Schema Changes

**No new tables or columns are required.** The `athletes.timezone` column already exists.

```sql
-- Existing column (already present in schema)
athletes.timezone  TEXT  NULLABLE
```

The column is currently populated by the onboarding city lookup for athletes in the hardcoded city list; it remains `NULL` for all other athletes. This feature makes the field reliably populated for all athletes.

---

## Entity: Athlete (modified behaviour only)

| Column | Type | Nullable | Change |
|--------|------|----------|--------|
| `timezone` | `TEXT` | Yes | Now set via `timezonefinder` on `home_lat`/`home_lon` during onboarding (was hardcoded lookup). Updated after each activity ingestion if GPS indicates a new timezone. |
| `home_lat` | `Float` | Yes | Unchanged. Source of truth for onboarding timezone derivation. |
| `home_lon` | `Float` | Yes | Unchanged. Source of truth for onboarding timezone derivation. |

### Timezone Field Contract

- **Format**: IANA timezone string (e.g., `"America/New_York"`, `"Europe/London"`, `"Asia/Seoul"`)
- **Fallback**: `"UTC"` when coordinates are unavailable or `timezonefinder` cannot resolve
- **NULL**: Only acceptable transiently during onboarding before `_complete_onboarding` runs. Post-feature, all onboarded athletes will have a non-NULL `timezone`.
- **Updates**: Only changed when the newly derived timezone differs from the stored value (i.e., no no-op writes)

---

## State Transition

```
[Athlete Created]
      │
      ▼
[Onboarding In Progress]
  timezone = NULL
      │
      ▼ _complete_onboarding()
      │  ← derive_timezone_from_coords(home_lat, home_lon)
      │    fallback: "UTC"
      ▼
[Onboarding Complete]
  timezone = "Home/Timezone"   ← e.g., "America/New_York"
      │
      ▼ Activity ingested with GPS in new timezone
      │  ← derive_timezone_from_coords(activity_start_lat, activity_start_lon)
      │    diff check: if new_tz != athlete.timezone → update
      ▼
[Timezone Updated]
  timezone = "Travel/Timezone"  ← e.g., "Asia/Seoul"
  morning job re-registered at 07:00 new timezone
```

---

## Alembic Migration

**None required.** No schema changes. The `athletes.timezone` column was introduced in the initial schema (`Base.metadata.create_all(engine)`).

---

## Existing Athletes (Migration Path)

Per spec clarification: "Update naturally on next activity." On the first activity poll after deployment, `extract_activity_start_coords` will return GPS coordinates if the athlete ran outdoors. If the derived timezone differs from `NULL` or the stored value, the athlete's timezone is updated and the scheduler job re-registered.

Athletes with `timezone = NULL` who have no upcoming activities will continue using `"UTC"` as the scheduler fallback until their next outdoor activity triggers an update.
