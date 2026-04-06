# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

- Do not make any changes until you have 95% confidence in what you need to build. Ask follow-up questions until you reach that confidence.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python main.py

# Database migrations
alembic upgrade head           # apply all migrations
alembic revision --autogenerate -m "description"  # create new migration

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

# Docker (Synology NAS deployment)
docker-compose up -d
docker-compose logs -f coach
```

## Architecture

### Process model

`main.py` starts two concurrent systems on one process:
1. **Slack SocketModeHandler** (`handler.connect()`) — non-blocking, opens a WebSocket in a daemon thread. Handles all inbound DMs and @mentions.
2. **APScheduler BlockingScheduler** (`scheduler.start()`) — blocks the main thread; registers three recurring jobs at startup.

All shared state flows through SQLite (via SQLAlchemy). Bolt event handlers and scheduler jobs each create their own DB session per operation — sessions are never shared across threads.

### Message routing (`slack/bot.py` → `slack/onboarding.py` / `slack/conversation.py`)

Every inbound DM goes through this gate in order:
1. Ignore bot messages and message subtypes
2. Admin command intercept (`!admin ...`) if sender is `ADMIN_SLACK_USER_ID`
3. Allowed-list check — create `Athlete` row on first contact if in `ALLOWED_SLACK_USER_IDS`; decline otherwise
4. Route to `onboarding.handle()` if `athlete.onboarding_complete == False`, else `conversation.handle_message()`

### Onboarding (`slack/onboarding.py`)

Fully conversational — Claude drives the 8-question intake via `_SYSTEM_PROMPT`. When the athlete confirms their profile, Claude emits an `<onboarding_complete>{...json...}</onboarding_complete>` tag. The handler parses this, creates `Goal`, calls `generate_plan()`, uploads week 1 to Garmin, and registers the athlete's morning check-in job on the live scheduler without a restart.

Garmin credentials are scrubbed from conversation history and the Slack message is deleted immediately after parsing.

### Coaching conversation (`slack/conversation.py`)

`build_system_prompt()` assembles 8 context sections on every turn: persona, current date, athlete profile, training phase + this week, health data (today + 7-day HRV trend), recent completed workouts, upcoming Garmin calendar (next 4 weeks with sync status), weather, and coach memories.

Claude's response is post-processed for three XML side-effect tags before the text is sent to the athlete:
- `<plan>{json}</plan>` — mutates `PlannedWorkout` rows and re-syncs to Garmin if previously uploaded
- `<garmin_sync/>` — pushes next 4 weeks of future workouts to Garmin Connect
- `<remember>text</remember>` — creates a `CoachMemory` row

### Garmin integration (`garmin/`)

- **Auth** (`client.py:get_garmin_client`): loads cached garth OAuth tokens from `GARMIN_SESSION_DIR/{athlete_id}/`; falls back to full re-auth with decrypted credentials if expired.
- **Deletion API**: `delete_workout` and `remove_workout_schedule` use `garmin.garth.request("DELETE", "connectapi", path, api=True)` — the `garminconnect` library does **not** expose a `delete_workout` method.
- **Pace encoding** (`workout_builder.py`): Garmin's `pace.zone` target values are **speed in m/s as floats** (NOT sec/km). Formula: `speed_ms = 1000.0 / (pace_min_per_km * 60)`. tv1 (faster bound) = `speed_ms * 1.05`, tv2 (slower bound) = `speed_ms * 0.95`. Example: 9:30/mi (5.905 min/km) → speed = 2.824 m/s → tv1=2.965, tv2=2.683.
- **Sync scope**: `sync_week_to_garmin` only uploads workouts with `scheduled_date >= today`. Past dates are never written to Garmin.

### Scheduler jobs (`scheduler/jobs.py`)

| Job | Trigger | Action |
|-----|---------|--------|
| `morning_checkin_{id}` | Daily 07:00 athlete local time | Fetch live Garmin health → weather → Claude → adapt plan → DM |
| `activity_poll` | Every 30 min, 06:00–22:00 only | Poll new Garmin activities → telemetry → biomechanics → feedback DM |
| `weekly_review` | Sunday 20:00 system time | Aggregate week → Claude review → adapt next week → sync Garmin → DM |

New athletes get their morning job registered immediately in `onboarding._complete_onboarding()` via `register_athlete_morning_job()` — no restart needed.

### Unit conversions

All distances and paces are stored internally in **km / min-per-km**. The display layer always converts to **miles / min-per-mile** for athlete-facing output using helpers in `coach/persona.py`: `format_miles()`, `format_pace_mi()`, `km_to_mi()`, `mi_to_km()`. Paces inside `<plan>` JSON tags must be in min/km (internal format); Claude is instructed to convert.

### Data isolation

Every DB query on behalf of an athlete **must** include `athlete_id`. Use `database/session.py:scoped_query(db_session, Model, athlete_id)` for athlete-scoped lookups. This is a hard requirement — no cross-athlete data leakage is acceptable.

### Admin commands (`slack/admin.py`)

Sent as DMs by `ADMIN_SLACK_USER_ID`, prefixed `!admin`:

| Command | Effect |
|---------|--------|
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
