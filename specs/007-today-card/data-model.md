# Phase 1 Data Model: Today Card

**Branch**: `007-today-card`
**Date**: 2026-05-15

This feature introduces **zero new SQL tables** and **zero migrations**. All persisted data flows through existing models. Two changes to in-memory dataclasses and one new WebEvent kind are documented below.

---

## 1. Existing models read by `/api/today`

The endpoint resolves state and assembles its response payload by reading the following existing models, all scoped by `athlete_id`:

| Model | Source file | Used for | New columns? |
|---|---|---|---|
| `Athlete` | `database/models.py` | `athlete.timezone` (for athlete-local today), `athlete.coach_key` (persona resolution), `athlete.name` (cover star), `athlete.lthr_bpm` (RACE_DAY HR cap calculation) | No |
| `Goal` | `database/models.py` | `Goal.active` (state resolution NO_PLAN vs others), `Goal.race_name` / `Goal.race_date` (RACE_DAY headline + cover lines) | No |
| `PlannedWorkout` | `database/models.py` | `PlannedWorkout.scheduled_date == today` (state resolution PRE_RUN vs REST_DAY vs RACE_DAY; absence of a row also resolves to REST_DAY), `workout_type`, `target_distance_km`, `target_pace_min_per_km`, `target_duration_seconds`, `workout_name`, `description`, `garmin_workout_id` (on_watch modifier), `target_zones_json` (RACE_DAY HR cap) | No |
| `CompletedWorkout` | `database/models.py` | `CompletedWorkout.date == today` (state resolution COMPLETED), `distance_km`, `avg_pace_min_per_km`, `avg_hr`, `training_load`, `coach_analysis` (rationale source for COMPLETED), `planned_workout_id` (is_bonus modifier) | No |
| `HealthSnapshot` | `database/models.py` | `HealthSnapshot.date == today` for PRE_RUN/REST_DAY cover lines (HRV, body battery, sleep, RHR), with 3-day stale fallback | No |
| `Notification` | `database/models.py` | `Notification(kind="morning_checkin")` body, freshness window 24h, for PRE_RUN/REST_DAY rationale source | No |
| `WebEvent` | `database/models.py` | Reading the most recent `kind="today.state_transition"` event for the athlete (transition detection) AND writing new transition events | No (new `kind` value enum-extended in code only) |
| `TrainingPlan` | `database/models.py` | `TrainingPlan.current_phase`, `TrainingPlan.current_week` (passed to `rule_based_morning` for periodization context per FR-008c) | No |

No model fields are added. No indexes are added (existing `athlete_id` + `date` / `scheduled_date` indexes already serve every query).

---

## 2. `CoachPersona` dataclass — two new fields

`coach/personas.py:7` currently defines:

```python
@dataclass(frozen=True)
class CoachPersona:
    key: str
    name: str
    description: str
    persona_block: str
    greeting: str = ""
```

This feature extends the dataclass with two fields:

```python
@dataclass(frozen=True)
class CoachPersona:
    key: str
    name: str
    description: str
    persona_block: str
    greeting: str = ""
    accent_color: str = "#b8ff4f"           # NEW (FR-021, FR-031)
    race_morning_greeting: str = ""          # NEW (FR-007, FR-031)
```

Default values keep existing call sites compatible. The `PERSONAS` registry at `coach/personas.py:317` is updated to populate both fields for `classic`, `maya`, and `jordan` (see research.md Decisions 4 and 5 for the canonical values).

`LEGACY_COACH_KEY_ALIASES` (`sofia → maya`, `miles → jordan`) requires no change — `get_persona(coach_key)` resolves through the alias map and returns the canonical persona, including the new fields, automatically.

**Validation**: `accent_color` is a 7-character `#RRGGBB` hex string. `race_morning_greeting` is a non-empty string under 280 characters (cover-line space at 375px mobile). These constraints are enforced by unit tests in `tests/unit/test_coach_personas.py` (extended, not new).

---

## 3. `WebEvent` kind — new value

The new endpoint emits `WebEvent` rows of a previously unused `kind` value:

```text
kind:    "today.state_transition"
level:   "info"
component: "today_api"
message:   "{from_state} → {to_state}"
extra:     {
  "from_state": "PRE_RUN" | "COMPLETED" | "REST_DAY" | "RACE_DAY" | "NO_PLAN" | null,
  "to_state":   "PRE_RUN" | "COMPLETED" | "REST_DAY" | "RACE_DAY" | "NO_PLAN",
  "athlete_id": int,
  "timestamp":  ISO 8601 UTC string,
}
```

The `from_state` is `null` for the first-ever resolution for an athlete (per FR-032a, the implicit baseline). Transition detection logic uses `MAX(created_at)` over prior `today.state_transition` rows for the athlete. Only emit when `from_state != to_state`.

The admin Events view filter dropdown (per CLAUDE.md, lives in the admin tab — `web/api/admin.py`) gains the new kind in its allow-list. This is a one-line change in the existing dropdown options; no schema change.

---

## 4. State machine

The card has five terminal states; the endpoint computes the state per request using the precedence ladder defined in FR-003:

```text
┌──────────────────────────────────────────────────────────────┐
│  is athlete.goal.active null?                                │
│      ── yes ──→ NO_PLAN                                      │
│      ── no  ──→ continue                                     │
├──────────────────────────────────────────────────────────────┤
│  PlannedWorkout(today).workout_type == "race"?               │
│      ── yes ──→ RACE_DAY                                     │
│      ── no  ──→ continue                                     │
├──────────────────────────────────────────────────────────────┤
│  CompletedWorkout(today) exists?                             │
│      ── yes ──→ COMPLETED                                    │
│      ── no  ──→ continue                                     │
├──────────────────────────────────────────────────────────────┤
│  PlannedWorkout(today).workout_type == "rest"                │
│  OR PlannedWorkout(today) row is absent?                     │
│      ── yes ──→ REST_DAY                                     │
│      ── no  ──→ continue                                     │
├──────────────────────────────────────────────────────────────┤
│                  ──→ PRE_RUN                                 │
└──────────────────────────────────────────────────────────────┘
```

A day with an active Goal but no PlannedWorkout row routes directly into REST_DAY — it is treated as a recovery day with tips (Sleep / Fuel / Move cues strip), not a distinct alarm state.

**Transitions** are not stored in the DB; they emerge from the underlying data. Detection compares the resolved state against the most recent `today.state_transition` WebEvent for the athlete. Typical transition flow over a calendar day:

```text
Midnight (athlete-local) — state re-evaluates based on tomorrow's data
↓
06:30am — Athlete opens app → state = PRE_RUN (or REST_DAY / RACE_DAY)
↓
08:00am — Activity poll detects new Garmin activity → CompletedWorkout written
         → next /api/today fetch → state transitions PRE_RUN → COMPLETED
         → WebEvent emitted
↓
Midnight (next athlete-local day) — state re-evaluates for next day
```

No new tables track state history; the `today.state_transition` WebEvent rows are the historical record. Querying transitions is `SELECT * FROM web_events WHERE kind = 'today.state_transition' AND athlete_id = ? ORDER BY created_at`.

---

## 5. Rationale source resolution

The `rationale.source` field in the response takes one of these values, determined by the resolution ladder in FR-005/FR-006/FR-007:

| `rationale.source` | When used | Body extracted from |
|---|---|---|
| `morning_checkin` | PRE_RUN, REST_DAY: a `Notification(kind="morning_checkin")` exists within 24h | First paragraph of `Notification.body` via `coach/today_rationale.py:extract_rationale_paragraph()` |
| `coach_analysis` | COMPLETED: today's `CompletedWorkout.coach_analysis` is non-empty | First paragraph of `CompletedWorkout.coach_analysis` via same extractor |
| `rule_based` | PRE_RUN, REST_DAY, COMPLETED: no Claude content available | Template from `coach/today_rationale.py:rule_based_morning()` (two-branch, with vs. without snapshot) |
| `placeholder` | PRE_RUN, REST_DAY: no morning_checkin AND athlete-local time before 7:00am | Static copy: `"Coach is checking in soon — pull this up after 7am for today's call."` |
| `persona_static` | RACE_DAY (race-morning greeting), NO_PLAN (Pick a race CTA) | `CoachPersona.race_morning_greeting` (RACE_DAY) OR a static-per-state string (NO_PLAN) |

The `coach` field in the response is always the active persona's display name (`get_persona(athlete.coach_key).name`), regardless of `rationale.source` — voice continuity is maintained even when the rationale itself is rule-based or static.

---

## 6. Drill-in targets

Each `cover_line` entry includes a `drill_to` field directing the frontend to a section already present in the magazine. No new sections are added; existing IDs are reused:

| `drill_to` | Section HTML id | Used by states |
|---|---|---|
| `"morning"` | `<section id="morning">` (existing Morning Readiness) | PRE_RUN, REST_DAY |
| `"last_run"` | `<section id="featrun">` (existing Last Run / Featured Run) | COMPLETED |
| `null` | (no drill) | RACE_DAY (cover lines are read-only) |

For NO_PLAN, `cover_lines` is omitted entirely (FR-012), so `drill_to` does not apply.

---

## 7. Caching surfaces

| Surface | Key | Scope | Invalidation |
|---|---|---|---|
| Frontend `localStorage` | `runcoach.today.{athlete_id}` | Per-athlete | Replaced on every successful `/api/today` response; cleared on logout (existing pattern — extends auth logout flow by one line) |
| Server-side state cache | (none) | — | The endpoint is stateless; transition detection reads the prior WebEvent row |

No Claude responses are cached at request time because none are made at request time.

---

## 8. Backwards compatibility

- `/api/magazine` continues to return the existing payload unchanged (per FR-032).
- `Athlete`, `Goal`, `PlannedWorkout`, `CompletedWorkout`, `HealthSnapshot`, `Notification`, `WebEvent` schemas are unchanged — no migration is required to deploy this feature.
- The new `CoachPersona.accent_color` and `CoachPersona.race_morning_greeting` fields have defaults, so any code that constructs `CoachPersona(...)` instances directly (none in the current codebase, but worth noting for future extensions) continues to work without modification.
