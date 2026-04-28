# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

- Do not make any changes until you have 95% confidence in what you need to build. Ask follow-up questions until you reach that confidence.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the Slack bot + scheduler
python main.py

# Run the web dashboard
python web.py

# Database migrations
alembic upgrade head                             # apply all migrations
alembic revision --autogenerate -m "description" # create new migration

# Linting
ruff check .
ruff check . --fix

# Tests
pytest tests/unit/
pytest tests/integration/      # requires test DB + mock clients
pytest tests/unit/test_foo.py  # single file
pytest -k "test_name"          # single test by name

# Generate Fernet encryption key (run once for new deploys)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Scripts
python scripts/diagnose_garmin.py                    # diagnose Garmin auth issues
python scripts/set_web_credentials.py --slack-id U123 --username admin --password secret --admin
python scripts/backfill_activities.py                # import historical Garmin activities
python scripts/backfill_workout_durations.py         # populate target_duration_seconds on existing rows
python scripts/reprocess_feedback.py                 # re-run coach analysis on completed workouts

# Docker (Synology NAS deployment)
docker-compose up -d
docker-compose logs -f coach

# Troubleshooting Garmin rate limits (429 errors may be IP-based)
# Solutions: use a VPN, wait longer between auth attempts, try a residential IP
```

## Environment variables

All vars are loaded via `pydantic-settings` from `.env` (see `.env.example`):

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | — | Claude API key |
| `SLACK_BOT_TOKEN` | yes | — | `xoxb-...` |
| `SLACK_SIGNING_SECRET` | yes | — | Bolt webhook secret |
| `SLACK_APP_TOKEN` | yes | — | Socket Mode `xapp-...` |
| `DB_PATH` | no | `/data/coach.db` | SQLite file path |
| `GARMIN_SESSION_DIR` | no | `/data/garmin_sessions/` | Per-athlete garth OAuth token cache |
| `ENCRYPTION_KEY` | yes | — | Fernet key for Garmin passwords at rest |
| `ALLOWED_SLACK_USER_IDS` | yes | — | Comma-separated Slack user IDs |
| `ADMIN_SLACK_USER_ID` | yes | — | Single admin Slack user ID |
| `WEB_SECRET_KEY` | yes | — | Flask session secret |
| `WEB_PORT` | no | `8080` | Web dashboard port |
| `LOG_LEVEL` | no | `INFO` | Python logging level |
| `LOG_FILE` | no | `""` | If set, also log to rotating file (10 MB × 5) |
| `GARMIN_TIMEOUT` | no | `30` | Garmin API call timeout (seconds) |

## Architecture

### Process model

Two separate entry points share the same SQLite database:

- **`main.py`** — Slack bot + scheduler on one process: `SocketModeHandler` (daemon thread) + `APScheduler BlockingScheduler` (main thread).
- **`web.py`** — Flask dashboard via `create_app()` from `running_coach_ai/web/app.py`. Runs on `WEB_PORT`.

All shared state flows through SQLite. Each Bolt handler and scheduler job creates its own DB session per operation — sessions are never shared across threads. SQLite WAL mode is enabled on every connection (`web.py`) to allow concurrent readers alongside the Slack writer.

### Message routing (`slack/bot.py` → `slack/onboarding.py` / `slack/conversation.py`)

Every inbound DM goes through this gate in order:

1. Ignore bot messages and message subtypes
2. Admin command intercept (`!admin ...`) if sender is `ADMIN_SLACK_USER_ID`
3. Allowed-list check — create `Athlete` row on first contact if in `ALLOWED_SLACK_USER_IDS`; decline otherwise
4. Route to `onboarding.handle()` if `athlete.onboarding_complete == False`, else `conversation.handle_message()`

### Onboarding (`slack/onboarding.py`)

Fully conversational — Claude drives the 8-question intake via `_SYSTEM_PROMPT`. When the athlete confirms their profile, Claude emits an `<onboarding_complete>{...json...}</onboarding_complete>` tag. The handler parses this, creates `Goal`, calls `generate_plan()`, uploads week 1 to Garmin, and registers the athlete's morning check-in job on the live scheduler without a restart.

**Garmin creds modal flow**: When profile JSON is ready but Garmin credentials haven't been entered yet, the JSON is stored in `athlete.pending_onboarding_data` (with `pending_onboarding_data_created_at` for TTL). Garmin credentials are scrubbed from conversation history and the Slack message is deleted immediately after parsing.

### Coaching conversation (`slack/conversation.py`)

`build_system_prompt()` assembles 8 context sections on every turn: persona, current date, athlete profile, training phase + this week, health data (today + 7-day HRV trend), recent completed workouts, upcoming Garmin calendar (next 4 weeks with sync status), weather, and coach memories.

**`process_message(athlete, user_text, db_session, source="slack", coach_key=None) -> str`** is the shared coaching function used by both Slack and the web chat API. It temporarily overrides `athlete.coach_key` in a `try/finally` block (ephemeral — does not persist to DB), then calls Claude and returns the response text.

Claude's response is post-processed for five XML side-effect tags before the text is sent to the athlete:

| Tag | Effect |
|---|---|
| `<plan>{json}</plan>` | Mutates `PlannedWorkout` rows; re-syncs to Garmin if previously uploaded |
| `<garmin_sync/>` | Pushes next 4 weeks of future workouts to Garmin Connect |
| `<remember>text</remember>` | Creates a `CoachMemory` row |
| `<switch_prescription>time\|distance</switch_prescription>` | Switches `athlete.prescription_style` and converts all upcoming workouts; always followed by `<garmin_sync/>` |
| `<coach_switch>key</coach_switch>` | Updates `athlete.coach_key` to switch active coaching persona |

### Coach personas (`coach/personas.py`)

The `PERSONAS` registry maps keys → `CoachPersona` dataclasses:

| Key | Display Name | Philosophy |
|---|---|---|
| `classic` | Coach Alex | Polarized training purist, HRV-obsessed, data-precise (default) |
| `maya` | Coach Maya | Consistency-first, low-friction planning for busy athletes |
| `jordan` | Coach Jordan | Resilience and longevity, injury-prevention first |

Legacy key aliases (`sofia` → `maya`, `miles` → `jordan`) are handled by `LEGACY_COACH_KEY_ALIASES`. Use `get_persona(coach_key)` to resolve any key with fallback to `classic`. Use `is_valid_coach_key(key)` to validate before saving to DB.

**`prescription_style`** (`"time"` | `"distance"` | `None`) on `Athlete` controls how Claude structures workouts in `<plan>` JSON: `time` → use `target_duration_seconds`, omit `target_distance_km` for easy/long_run/tempo/strides; `distance` → use `target_distance_km` in whole miles, omit `target_duration_seconds`. Intervals always use `target_zones_json` regardless.

Note: `coach/persona.py` contains a legacy `COACH_PERSONA` constant and `call_claude()` used by the planner and adapter. The per-persona `persona_block` strings from `personas.py` are used by `conversation.py` and `adapter.py` via `get_persona()`.

### Web dashboard (`running_coach_ai/web/`)

Flask 3.x app factory (`create_app()`) with blueprints deferred inside the factory. Auth uses Flask sessions (`athlete_id` in session) with `@login_required` / `@admin_required` decorators from `web/auth.py`.

**API routes:**

| Blueprint | Routes |
|---|---|
| `web/api/dashboard.py` | `GET /api/dashboard` — health snapshot, active goal, upcoming workouts |
| `web/api/activities.py` | `GET /api/activities`, `POST /api/activities/<id>/feedback` |
| `web/api/plan.py` | `GET /api/plan/upcoming` — upcoming planned workouts |
| `web/api/chat.py` | `GET /api/chat/history`, `POST /api/chat/message` (calls `process_message()`) |
| `web/api/review.py` | `GET /api/review/weekly` — weekly review summaries |
| `web/api/admin.py` | Admin-only endpoints (requires `is_admin=True`) |
| `web/auth.py` | `POST /auth/login`, `POST /auth/logout` |

**`WebEvent` dual-sink logging** (`web/events.py`): `WebEventHandler` writes log records to the `web_events` table; `web_event()` is a convenience helper for explicit event writes. Attached to `running_coach_ai.web`, `running_coach_ai.garmin`, and `running_coach_ai.coach.personas` namespaces.

**Admin seeding**: `create_app()` idempotently sets `is_admin=True` for the `ADMIN_SLACK_USER_ID` athlete on startup.

### Garmin integration (`garmin/`)

- **Auth** (`client.py:get_garmin_client`): loads cached garth OAuth tokens from `GARMIN_SESSION_DIR/{athlete_id}/`; falls back to full re-auth with decrypted credentials if expired.
- **Deletion API**: `delete_workout` and `remove_workout_schedule` use `garmin.garth.request("DELETE", "connectapi", path, api=True)` — the `garminconnect` library does **not** expose a `delete_workout` method.
- **Pace encoding** (`workout_builder.py`): Garmin's `pace.zone` target values are **speed in m/s as floats** (NOT sec/km). Formula: `speed_ms = 1000.0 / (pace_min_per_km * 60)`. tv1 (faster bound) = `speed_ms * 1.05`, tv2 (slower bound) = `speed_ms * 0.95`. Example: 9:30/mi (5.905 min/km) → speed = 2.824 m/s → tv1=2.965, tv2=2.683.
- **Sync scope**: `sync_week_to_garmin` only uploads workouts with `scheduled_date >= today`. Past dates are never written to Garmin.

### Scheduler jobs (`scheduler/jobs.py`)

| Job | Trigger | Action |
|---|---|---|
| `morning_checkin_{id}` | Daily 07:00 athlete local time | Fetch live Garmin health → weather → Claude → adapt plan → DM |
| `activity_poll` | Every 30 min, 06:00–22:00 only | Poll new Garmin activities → telemetry → biomechanics → feedback DM |
| `weekly_review` | Sunday 20:00 system time | Aggregate week → Claude review → adapt next week → sync Garmin → DM |

Morning check-in gate: skips if `athlete.last_morning_checkin_date == today` (dedup) or before 06:00 local. For athletes with Garmin, waits for `training_readiness` (or sleep_score fallback) to be populated; retries on next tick until 12:00, then skips for the day.

New athletes get their morning job registered immediately in `onboarding._complete_onboarding()` via `register_athlete_morning_job()` — no restart needed.

### Unit conversions

All distances and paces are stored internally in **km / min-per-km**. The display layer always converts to **miles / min-per-mile** for athlete-facing output using helpers in `coach/persona.py`: `format_miles()`, `format_pace_mi()`, `km_to_mi()`, `mi_to_km()`. Paces inside `<plan>` JSON tags must be in min/km (internal format); Claude is instructed to convert.

### Data isolation

Every DB query on behalf of an athlete **must** include `athlete_id`. Use `database/session.py:scoped_query(db_session, Model, athlete_id)` for athlete-scoped lookups. This is a hard requirement — no cross-athlete data leakage is acceptable.

### Admin commands (`slack/admin.py`)

Sent as DMs by `ADMIN_SLACK_USER_ID`, prefixed `!admin`:

| Command | Effect |
|---|---|
| `!admin add <uid>` | Grant access; create Athlete row |
| `!admin remove <uid>` | Revoke access; data retained |
| `!admin list` | List all athletes and status |
| `!admin resync-garmin [<uid>]` | Re-upload all upcoming workouts to Garmin |
| `!admin clean-garmin [<uid>]` | Wipe entire Garmin library, clear DB IDs, re-sync fresh |

### Key constraints

- **Garmin MFA**: Athletes must disable MFA on Garmin Connect — TOTP/push MFA cannot be automated.
- **Garmin session files**: Stored at `GARMIN_SESSION_DIR/{athlete_id}/`. Loss forces re-authentication.
- **Encryption key**: Losing `ENCRYPTION_KEY` makes all stored Garmin passwords unreadable.
- **DB schema**: `main.py` calls `Base.metadata.create_all(engine)` at startup (idempotent). Alembic is used for schema migrations on existing deployments.
- **speckit workflow**: Feature development follows `specify → clarify → plan → tasks → implement` using the slash commands in `.claude/commands/`. Specs live in `specs/001-ai-running-coach/`.
