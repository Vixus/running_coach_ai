# Data Model: AI Running Coach

**Branch**: `001-ai-running-coach` | **Date**: 2026-03-31

---

## Overview

All entities are stored in a single SQLite database. Every table includes an `athlete_id` foreign key (except `Athlete` itself) — this is the isolation boundary. No query should ever omit an `athlete_id` filter when operating on behalf of a specific athlete.

---

## Entity Relationship Summary

```
Athlete ──< Goal ──< TrainingPlan ──< PlannedWorkout
   │                                       │
   ├──< HealthSnapshot                     │ (nullable FK)
   ├──< CoachMemory                        ▼
   ├──< ConversationMessage         CompletedWorkout ──< WorkoutTelemetry
   └──< RunningProfile
```

---

## Entities

### Athlete

Central entity. One row per registered user. Source of truth for identity, credentials, and onboarding state.

| Field                       | Type     | Constraints             | Notes                                                                                                |
| --------------------------- | -------- | ----------------------- | ---------------------------------------------------------------------------------------------------- |
| `id`                        | INTEGER  | PK, auto                | Internal athlete ID                                                                                  |
| `slack_user_id`             | TEXT     | UNIQUE, NOT NULL        | Slack user ID (e.g. `U012AB3CD`)                                                                     |
| `slack_dm_channel_id`       | TEXT     | NULLABLE                | Cached DM channel ID for proactive messages                                                          |
| `name`                      | TEXT     | NULLABLE                | Set during onboarding                                                                                |
| `age`                       | INTEGER  | NULLABLE                | Set during onboarding                                                                                |
| `home_lat`                  | REAL     | NULLABLE                | For weather fetch                                                                                    |
| `home_lon`                  | REAL     | NULLABLE                | For weather fetch                                                                                    |
| `timezone`                  | TEXT     | NULLABLE                | IANA timezone string (e.g. `Europe/London`)                                                          |
| `garmin_email`              | TEXT     | NULLABLE                | Set during onboarding                                                                                |
| `garmin_password_encrypted` | BYTES    | NULLABLE                | Fernet-encrypted; never stored in plaintext                                                          |
| `onboarding_complete`       | BOOLEAN  | NOT NULL, default False | True after full intake confirmed                                                                     |
| `onboarding_step`           | INTEGER  | NOT NULL, default 0     | Last completed onboarding question index (for resume)                                                |
| `allowed`                   | BOOLEAN  | NOT NULL, default True  | False = access revoked; data retained                                                                |
| `last_morning_checkin_date` | DATE     | NULLABLE                | Most recent date a morning DM was successfully sent; deduplication guard for the 30-min polling loop |
| `created_at`                | DATETIME | NOT NULL                | Row creation timestamp                                                                               |

**State transitions**:

- `allowed=True, onboarding_complete=False` → athlete in onboarding
- `allowed=True, onboarding_complete=True` → active athlete
- `allowed=False` → access revoked; all other fields retained; can be re-enabled

**Validation**:

- `slack_user_id` must be unique across all rows (including `allowed=False`)
- `onboarding_step` must only advance (never regress except on explicit reset)

---

### Goal

An athlete's target race. Multiple concurrent Goals per athlete are supported (clarification Q3). Each Goal has one active TrainingPlan.

| Field                       | Type     | Constraints            | Notes                                          |
| --------------------------- | -------- | ---------------------- | ---------------------------------------------- |
| `id`                        | INTEGER  | PK, auto               |                                                |
| `athlete_id`                | INTEGER  | FK(Athlete), NOT NULL  |                                                |
| `race_type`                 | TEXT     | NOT NULL               | e.g. `marathon`, `half_marathon`, `10k`        |
| `race_name`                 | TEXT     | NULLABLE               | Optional event name                            |
| `race_date`                 | DATE     | NOT NULL               | Target race date                               |
| `target_time_seconds`       | INTEGER  | NOT NULL               | Goal finish time                               |
| `current_weekly_mileage_km` | REAL     | NULLABLE               | Baseline at plan creation                      |
| `experience_level`          | TEXT     | NOT NULL               | `beginner` / `intermediate` / `advanced`       |
| `training_days_per_week`    | INTEGER  | NOT NULL               | 1–7                                            |
| `active`                    | BOOLEAN  | NOT NULL, default True | False after race date passes or goal abandoned |
| `created_at`                | DATETIME | NOT NULL               |                                                |

**Validation**:

- `race_date` must be in the future at creation time
- `training_days_per_week` 1–7
- `experience_level` must be one of the three enum values

---

### TrainingPlan

A generated week-by-week plan for a specific Goal. One active plan per Goal at a time. When a plan is regenerated (mid-cycle), the old plan is archived (`active=False`) and a new one created.

| Field           | Type     | Constraints            | Notes                                |
| --------------- | -------- | ---------------------- | ------------------------------------ |
| `id`            | INTEGER  | PK, auto               |                                      |
| `athlete_id`    | INTEGER  | FK(Athlete), NOT NULL  | Denormalised for fast scoped queries |
| `goal_id`       | INTEGER  | FK(Goal), NOT NULL     |                                      |
| `generated_at`  | DATETIME | NOT NULL               | When Claude generated this plan      |
| `valid_from`    | DATE     | NOT NULL               | First day of plan coverage           |
| `valid_to`      | DATE     | NOT NULL               | Race date                            |
| `plan_json`     | JSON     | NOT NULL               | Full week-by-week structured plan    |
| `current_phase` | TEXT     | NOT NULL               | `base` / `build` / `peak` / `taper`  |
| `active`        | BOOLEAN  | NOT NULL, default True | Only one active plan per goal        |

**plan_json structure**:

```json
{
  "weeks": [
    {
      "week_number": 1,
      "phase": "base",
      "start_date": "2026-04-07",
      "target_km": 45,
      "days": [
        { "date": "2026-04-07", "type": "easy", "distance_km": 8, "description": "..." },
        ...
      ]
    }
  ]
}
```

---

### PlannedWorkout

One scheduled session from a TrainingPlan. Each day entry in `plan_json` materialises as a `PlannedWorkout` row for DB querying and status tracking.

| Field                    | Type     | Constraints                 | Notes                                                                            |
| ------------------------ | -------- | --------------------------- | -------------------------------------------------------------------------------- |
| `id`                     | INTEGER  | PK, auto                    |                                                                                  |
| `plan_id`                | INTEGER  | FK(TrainingPlan), NOT NULL  |                                                                                  |
| `athlete_id`             | INTEGER  | FK(Athlete), NOT NULL       | Denormalised                                                                     |
| `scheduled_date`         | DATE     | NOT NULL                    |                                                                                  |
| `workout_type`           | TEXT     | NOT NULL                    | `easy` / `long_run` / `tempo` / `intervals` / `strides` / `rest` / `cross_train` |
| `description`            | TEXT     | NULLABLE                    | Coach-written session description                                                |
| `target_distance_km`     | REAL     | NULLABLE                    |                                                                                  |
| `target_pace_min_per_km` | REAL     | NULLABLE                    | Target average pace                                                              |
| `target_zones_json`      | JSON     | NULLABLE                    | HR/pace zone breakdown for structured sessions                                   |
| `garmin_workout_id`      | TEXT     | NULLABLE                    | ID after upload to Garmin Connect                                                |
| `garmin_schedule_id`     | TEXT     | NULLABLE                    | Calendar schedule ID for deletion                                                |
| `last_garmin_synced_at`  | DATETIME | NULLABLE                    | Last successful Garmin upload/resync timestamp                                   |
| `status`                 | TEXT     | NOT NULL, default `planned` | `planned` / `completed` / `skipped` / `modified`                                 |
| `modified_reason`        | TEXT     | NULLABLE                    | Why the session was changed (coach note)                                         |
| `created_at`             | DATETIME | NOT NULL                    | Row creation timestamp                                                           |
| `updated_at`             | DATETIME | NOT NULL                    | Last row mutation timestamp                                                      |

**target_zones_json structure** (for intervals):

```json
{
  "steps": [
    { "type": "warmup", "duration_min": 10, "pace_min_per_km": 5.5 },
    {
      "type": "interval",
      "reps": 6,
      "distance_m": 1000,
      "pace_min_per_km": 4.2,
      "recovery_min": 2
    },
    { "type": "cooldown", "duration_min": 10, "pace_min_per_km": 5.5 }
  ]
}
```

**Status transitions**:

- `planned` → `completed` (when matched activity detected)
- `planned` → `skipped` (manually by coach or athlete)
- `planned` → `modified` (session changed by coach before completion)
- `modified` → `completed` or `skipped`

**Timestamp semantics**:

- `created_at` is set once when the `PlannedWorkout` row is first inserted.
- `updated_at` is refreshed on any mutation to workout content or status.
- `last_garmin_synced_at` is updated only after successful Garmin upload/schedule or resync for that workout.

---

### CompletedWorkout

A recorded activity retrieved from the athlete's Garmin account. May or may not match a `PlannedWorkout`.

| Field                         | Type     | Constraints                  | Notes                                  |
| ----------------------------- | -------- | ---------------------------- | -------------------------------------- |
| `id`                          | INTEGER  | PK, auto                     |                                        |
| `athlete_id`                  | INTEGER  | FK(Athlete), NOT NULL        |                                        |
| `planned_workout_id`          | INTEGER  | FK(PlannedWorkout), NULLABLE | Null if unplanned activity             |
| `garmin_activity_id`          | TEXT     | UNIQUE, NOT NULL             | Garmin's activity ID                   |
| `date`                        | DATE     | NOT NULL                     | Activity date                          |
| `distance_km`                 | REAL     | NULLABLE                     |                                        |
| `duration_seconds`            | INTEGER  | NULLABLE                     |                                        |
| `avg_hr`                      | INTEGER  | NULLABLE                     |                                        |
| `max_hr`                      | INTEGER  | NULLABLE                     |                                        |
| `avg_pace_min_per_km`         | REAL     | NULLABLE                     |                                        |
| `max_pace_min_per_km`         | REAL     | NULLABLE                     |                                        |
| `avg_cadence_spm`             | INTEGER  | NULLABLE                     | Steps per minute                       |
| `max_cadence_spm`             | INTEGER  | NULLABLE                     |                                        |
| `avg_stride_length_m`         | REAL     | NULLABLE                     |                                        |
| `avg_ground_contact_time_ms`  | REAL     | NULLABLE                     |                                        |
| `avg_vertical_oscillation_cm` | REAL     | NULLABLE                     |                                        |
| `avg_vertical_ratio_pct`      | REAL     | NULLABLE                     |                                        |
| `avg_power_w`                 | REAL     | NULLABLE                     | Garmin Running Power                   |
| `max_power_w`                 | REAL     | NULLABLE                     |                                        |
| `elevation_gain_m`            | REAL     | NULLABLE                     |                                        |
| `training_load`               | REAL     | NULLABLE                     | Garmin training load score             |
| `aerobic_training_effect`     | REAL     | NULLABLE                     | 0.0–5.0                                |
| `anaerobic_training_effect`   | REAL     | NULLABLE                     | 0.0–5.0                                |
| `vo2max_estimate`             | REAL     | NULLABLE                     |                                        |
| `calories`                    | INTEGER  | NULLABLE                     |                                        |
| `telemetry_channels_json`     | JSON     | NULLABLE                     | List of stream names actually recorded |
| `feedback_given`              | BOOLEAN  | NOT NULL, default False      | True after coach feedback sent         |
| `created_at`                  | DATETIME | NOT NULL                     | When row was inserted                  |

**Uniqueness**: `garmin_activity_id` is unique — prevents duplicate ingestion of the same activity.

---

### WorkoutTelemetry

Full time-series telemetry for a completed workout. One row per activity. JSON columns store arrays indexed by sample number. Missing streams are stored as NULL (not empty array).

| Field                        | Type     | Constraints                            | Notes                                      |
| ---------------------------- | -------- | -------------------------------------- | ------------------------------------------ |
| `id`                         | INTEGER  | PK, auto                               |                                            |
| `athlete_id`                 | INTEGER  | FK(Athlete), NOT NULL                  |                                            |
| `completed_workout_id`       | INTEGER  | FK(CompletedWorkout), UNIQUE, NOT NULL | One telemetry row per activity             |
| `sample_interval_seconds`    | INTEGER  | NULLABLE                               | Native device recording rate (typically 1) |
| `heart_rate_json`            | JSON     | NULLABLE                               | Array of BPM values                        |
| `pace_json`                  | JSON     | NULLABLE                               | Array of min/km values                     |
| `cadence_json`               | JSON     | NULLABLE                               | Array of SPM values                        |
| `stride_length_json`         | JSON     | NULLABLE                               | Array of metres                            |
| `ground_contact_time_json`   | JSON     | NULLABLE                               | Array of milliseconds                      |
| `vertical_oscillation_json`  | JSON     | NULLABLE                               | Array of centimetres                       |
| `vertical_ratio_json`        | JSON     | NULLABLE                               | Array of percentages                       |
| `power_json`                 | JSON     | NULLABLE                               | Array of watts                             |
| `elevation_json`             | JSON     | NULLABLE                               | Array of metres                            |
| `air_temperature_json`       | JSON     | NULLABLE                               | Array of °C                                |
| `respiration_rate_json`      | JSON     | NULLABLE                               | Array of breaths/min                       |
| `performance_condition_json` | JSON     | NULLABLE                               | Array of real-time fitness estimates       |
| `laps_json`                  | JSON     | NULLABLE                               | Array of lap objects (see structure below) |
| `recorded_at`                | DATETIME | NOT NULL                               | When telemetry was fetched                 |

**laps_json structure**:

```json
[
  {
    "lap_number": 1,
    "distance_m": 1000,
    "duration_seconds": 285,
    "avg_hr": 142,
    "max_hr": 156,
    "avg_pace_min_per_km": 4.75,
    "avg_cadence_spm": 174,
    "avg_ground_contact_time_ms": 245,
    "avg_vertical_oscillation_cm": 8.2,
    "avg_power_w": 215
  }
]
```

---

### RunningProfile

Derived biomechanical fingerprint for an athlete. Updated after each `WorkoutTelemetry` insert. One row per athlete (upsert pattern).

| Field                             | Type     | Constraints                   | Notes                                          |
| --------------------------------- | -------- | ----------------------------- | ---------------------------------------------- |
| `id`                              | INTEGER  | PK, auto                      |                                                |
| `athlete_id`                      | INTEGER  | FK(Athlete), UNIQUE, NOT NULL | One profile per athlete                        |
| `updated_at`                      | DATETIME | NOT NULL                      | Last recalculation time                        |
| `typical_cadence_easy_spm`        | REAL     | NULLABLE                      | 30-day rolling avg on easy runs                |
| `typical_cadence_hard_spm`        | REAL     | NULLABLE                      | 30-day rolling avg on tempo/intervals          |
| `typical_ground_contact_easy_ms`  | REAL     | NULLABLE                      |                                                |
| `typical_ground_contact_hard_ms`  | REAL     | NULLABLE                      |                                                |
| `typical_vertical_oscillation_cm` | REAL     | NULLABLE                      |                                                |
| `typical_vertical_ratio_pct`      | REAL     | NULLABLE                      |                                                |
| `cadence_trend`                   | TEXT     | NULLABLE                      | `improving` / `stable` / `declining`           |
| `hr_drift_pct`                    | REAL     | NULLABLE                      | Avg cardiac drift % on long runs               |
| `hr_pace_decoupling`              | REAL     | NULLABLE                      | Aerobic decoupling coefficient                 |
| `easy_hr_zone_compliance_pct`     | REAL     | NULLABLE                      | % of easy-run time in zone 1–2                 |
| `preferred_effort_distribution`   | JSON     | NULLABLE                      | Pacing tendency (even/positive/negative split) |
| `notes_json`                      | JSON     | NULLABLE                      | Coach-written observations array               |

---

### HealthSnapshot

Daily health indicators per athlete. One row per athlete per date.

| Field                    | Type    | Constraints           | Notes                                                |
| ------------------------ | ------- | --------------------- | ---------------------------------------------------- |
| `id`                     | INTEGER | PK, auto              |                                                      |
| `athlete_id`             | INTEGER | FK(Athlete), NOT NULL |                                                      |
| `date`                   | DATE    | NOT NULL              |                                                      |
| `hrv_score`              | REAL    | NULLABLE              | HRV status value                                     |
| `hrv_status`             | TEXT    | NULLABLE              | `poor` / `low` / `balanced` / `high` (Garmin labels) |
| `resting_hr`             | INTEGER | NULLABLE              | BPM                                                  |
| `sleep_score`            | INTEGER | NULLABLE              | 0–100                                                |
| `sleep_duration_seconds` | INTEGER | NULLABLE              |                                                      |
| `body_battery_start`     | INTEGER | NULLABLE              | Body battery at start of day                         |
| `body_battery_end`       | INTEGER | NULLABLE              | Body battery at end of day (if available)            |
| `stress_avg`             | INTEGER | NULLABLE              | 0–100                                                |
| `steps`                  | INTEGER | NULLABLE              |                                                      |
| `spo2_avg`               | REAL    | NULLABLE              | % (device-dependent)                                 |

**Uniqueness**: Composite unique constraint on `(athlete_id, date)`.

---

### ConversationMessage

Rolling conversation history per athlete. Used as context window for each Claude call.

| Field        | Type     | Constraints           | Notes                                                     |
| ------------ | -------- | --------------------- | --------------------------------------------------------- |
| `id`         | INTEGER  | PK, auto              |                                                           |
| `athlete_id` | INTEGER  | FK(Athlete), NOT NULL |                                                           |
| `slack_ts`   | TEXT     | NULLABLE              | Slack message timestamp (for threading if needed)         |
| `role`       | TEXT     | NOT NULL              | `user` / `assistant`                                      |
| `content`    | TEXT     | NOT NULL              | Full message text (includes XML tags for assistant turns) |
| `created_at` | DATETIME | NOT NULL              |                                                           |

**Rolling window**: Queries always order by `created_at DESC LIMIT 30`. Older rows are kept in DB (for audit/history) but not sent to Claude. No automatic deletion.

---

### CoachMemory

Long-term facts the coach should always remember, persisted across the rolling conversation window.

| Field        | Type     | Constraints            | Notes                                                                   |
| ------------ | -------- | ---------------------- | ----------------------------------------------------------------------- |
| `id`         | INTEGER  | PK, auto               |                                                                         |
| `athlete_id` | INTEGER  | FK(Athlete), NOT NULL  |                                                                         |
| `category`   | TEXT     | NOT NULL               | `injury` / `preference` / `goal_note` / `personal` / `performance_flag` |
| `content`    | TEXT     | NOT NULL               | Free-text fact (extracted from `<remember>` tags)                       |
| `created_at` | DATETIME | NOT NULL               |                                                                         |
| `active`     | BOOLEAN  | NOT NULL, default True | False = coach has retired this memory                                   |
| `source`     | TEXT     | NULLABLE               | `conversation` / `biomechanics` / `manual`                              |

**Injection rule**: All `active=True` memories for an athlete are injected into every Claude system prompt, regardless of age.

---

## Indexes

| Table                 | Index                           | Purpose                           |
| --------------------- | ------------------------------- | --------------------------------- |
| `Athlete`             | `UNIQUE(slack_user_id)`         | Fast lookup on every Slack event  |
| `Goal`                | `(athlete_id, active)`          | Active goals for plan assembly    |
| `PlannedWorkout`      | `(athlete_id, scheduled_date)`  | Today's session lookup            |
| `PlannedWorkout`      | `(athlete_id, status)`          | Filtering by status               |
| `CompletedWorkout`    | `UNIQUE(garmin_activity_id)`    | Dedup on activity ingestion       |
| `CompletedWorkout`    | `(athlete_id, date DESC)`       | Recent workouts for context       |
| `HealthSnapshot`      | `UNIQUE(athlete_id, date)`      | One snapshot per day              |
| `ConversationMessage` | `(athlete_id, created_at DESC)` | Rolling window fetch              |
| `CoachMemory`         | `(athlete_id, active)`          | Active memories for system prompt |

---

## Data Isolation Invariant

Every SELECT, INSERT, UPDATE, and DELETE that operates on behalf of an athlete MUST include `WHERE athlete_id = :athlete_id`. This is enforced by convention and code review — there is no row-level security in SQLite. Integration tests MUST verify that data from athlete A cannot appear in queries for athlete B.
