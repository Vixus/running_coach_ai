# Data Model: Web Analytics Dashboard

**Branch**: `005-web-analytics-dashboard` | **Date**: 2026-04-18

---

## Existing models — changes required

### `CompletedWorkout` — new column (included in `add_run_feedback` migration)

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `coach_analysis` | Text | Yes | NULL | AI-generated post-run analysis narrative, populated by the activity poll scheduler job when it processes a new activity. NULL for historical activities processed before this migration. |

**Source in API**: `GET /api/activities` reads `completed_workouts.coach_analysis` and includes it as the `coach_analysis` field in each activity response object. Null values are omitted from the response.

---

### `Athlete` — new columns (Alembic migration)

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `web_username` | Text | Yes | NULL | Login username for the web dashboard |
| `web_password_hash` | Text | Yes | NULL | PBKDF2-SHA256 hash via werkzeug |
| `is_admin` | Boolean | No | False | Grants access to the Admin screen |

**Seeding**: On startup, if the athlete whose `slack_user_id` matches `ADMIN_SLACK_USER_ID` has `is_admin=False`, set it to True (idempotent).

---

## New models

### `RunFeedback`

Post-run subjective feedback submitted by the athlete via the web Activity Feed.

| Column | Type | Nullable | Constraints | Notes |
|--------|------|----------|-------------|-------|
| `id` | Integer PK | No | autoincrement | |
| `athlete_id` | Integer FK → athletes.id | No | | Data isolation key |
| `completed_workout_id` | Integer FK → completed_workouts.id | No | UNIQUE | One feedback per activity |
| `feel_score` | Integer | Yes | 0–4 | 0=Dead, 1=Rough, 2=OK, 3=Good, 4=Strong |
| `rpe` | Integer | Yes | 1–10 | Perceived exertion |
| `notes` | Text | Yes | | Free-text athlete notes |
| `created_at` | DateTime | No | default=utcnow | |
| `updated_at` | DateTime | No | default=utcnow, onupdate=utcnow | |

**Indexes**: `(athlete_id, completed_workout_id)`

**Validation**: `feel_score` must be 0–4 if provided; `rpe` must be 1–10 if provided.

---

### `WeeklyReviewSummary`

Persisted output of the Sunday weekly review job, read by the web Weekly Review screen.

| Column | Type | Nullable | Constraints | Notes |
|--------|------|----------|-------------|-------|
| `id` | Integer PK | No | autoincrement | |
| `athlete_id` | Integer FK → athletes.id | No | | Data isolation key |
| `week_start_date` | Date | No | | Monday of the reviewed week |
| `total_miles` | Float | Yes | | Aggregate: completed miles this week |
| `elevation_gain_ft` | Float | Yes | | Aggregate: total elevation gain |
| `avg_hrv` | Float | Yes | | Aggregate: mean HRV across week |
| `total_tss` | Float | Yes | | Aggregate: sum of training stress scores |
| `narrative` | Text | No | | AI-generated coach summary paragraph(s) |
| `daily_volume_json` | JSON | Yes | | Array [Mon–Sun] of miles per day |
| `body_battery_json` | JSON | Yes | | Weekly average Body Battery for the 8 calendar weeks ending on `week_start_date` (one float per week, `[week_-7, week_-6, ..., week_0]`), derived from the mean of `HealthSnapshot.body_battery_end` values within each calendar week. Weeks with no data use `null`. |
| `next_week_json` | JSON | Yes | | Array of {day, type, label} for week preview |
| `created_at` | DateTime | No | default=utcnow | |

**Constraints**: `UNIQUE(athlete_id, week_start_date)` — upsert on conflict (one row per week per athlete).

---

### `WebEvent`

Structured event log for the Admin observability screen. Dual-sink: DB row + stdout log line.

| Column | Type | Nullable | Constraints | Notes |
|--------|------|----------|-------------|-------|
| `id` | Integer PK | No | autoincrement | |
| `athlete_id` | Integer FK → athletes.id | Yes | | NULL for system-level events |
| `timestamp` | DateTime | No | default=utcnow | |
| `severity` | Text | No | info/warn/error | |
| `category` | Text | No | auth/garmin/claude/scheduler/http | |
| `message` | Text | No | | Human-readable short message |
| `details_json` | JSON | Yes | | Extra structured data (latency, route, etc.) |

**Indexes**: `(timestamp DESC)`, `(category, timestamp DESC)`

**Retention**: Rows older than 30 days are deleted by a cleanup pass — triggered lazily on Admin screen load (delete before query) or by a daily scheduler job.

---

## Relationship diagram (additions only)

```
Athlete ──< RunFeedback >── CompletedWorkout
Athlete ──< WeeklyReviewSummary
Athlete ──< WebEvent  (nullable)
```

---

## Migration strategy

Four Alembic migrations, applied in order:

1. `add_web_credentials_to_athletes` — adds `web_username`, `web_password_hash`, `is_admin` to `athletes`.
2. `add_run_feedback` — creates `run_feedback` table; also adds `coach_analysis` (Text nullable) to `completed_workouts`.
3. `add_weekly_review_summary_and_web_event` — creates `weekly_review_summaries` and `web_events` tables.
4. `add_source_to_conversation_messages` — adds `source` (Text nullable) to `conversation_messages`; enables unified Slack/web chat history tagging.

All migrations are additive (no column drops or renames) — safe for live deployments with a running bot process.
