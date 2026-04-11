# AI Running Coach

A self-hosted AI running coach that integrates Slack, Garmin Connect, and Claude to deliver personalized training plans, daily check-ins, and post-run coaching for endurance athletes.

## Features

- **Conversational coaching via Slack** — athletes DM the bot to chat with their coach, adjust workouts, and get real-time feedback
- **Multiple coach personas** — choose from Coach Alex (elite/data-driven), Coach Maya (consistency-first), or Coach Jordan (resilience/injury-prevention)
- **Garmin Connect integration** — training plans are pushed directly to the athlete's watch; completed workouts are automatically pulled and analyzed
- **Daily morning check-ins** — automated readiness assessments using live HRV, sleep, body battery, and weather data
- **Activity feedback** — post-run biomechanics and telemetry analysis delivered via Slack within 10 minutes of completing a run
- **Weekly plan reviews** — automated Sunday review that adapts the upcoming week and re-syncs to Garmin
- **Onboarding flow** — fully conversational intake covering race goals, experience, timezone, and Garmin credentials

## Architecture

```
main.py
├── Slack SocketModeHandler  (daemon thread — handles all inbound DMs)
└── APScheduler BlockingScheduler  (main thread — morning check-in, activity poll, weekly review)

All shared state → SQLite via SQLAlchemy
```

Key modules:

| Path | Responsibility |
|------|---------------|
| `running_coach_ai/slack/bot.py` | Message routing and event handler registration |
| `running_coach_ai/slack/onboarding.py` | Conversational athlete intake (name, race, goal, fitness, coach selection) |
| `running_coach_ai/slack/conversation.py` | Per-turn coaching conversation and system prompt assembly |
| `running_coach_ai/coach/personas.py` | Coach persona registry (Alex, Maya, Jordan) |
| `running_coach_ai/coach/planner.py` | Training plan generation |
| `running_coach_ai/coach/adapter.py` | Plan adaptation logic |
| `running_coach_ai/garmin/client.py` | Garmin Connect auth and session management |
| `running_coach_ai/garmin/workout_builder.py` | Garmin workout payload construction |
| `running_coach_ai/scheduler/jobs.py` | Morning check-in, activity poll, weekly review jobs |
| `running_coach_ai/database/models.py` | SQLAlchemy ORM models |

## Requirements

- Python 3.11+
- A Slack app with Socket Mode enabled
- Garmin Connect account(s) with MFA disabled
- Anthropic API key

## Setup

**1. Clone and install dependencies**

```bash
pip install -r requirements.txt
```

**2. Configure environment**

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
ANTHROPIC_API_KEY=sk-ant-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_APP_TOKEN=xapp-...

DB_PATH=/data/coach.db
GARMIN_SESSION_DIR=/data/garmin_sessions/

# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=

ALLOWED_SLACK_USER_IDS=U012AB3CD,U034EF5GH
ADMIN_SLACK_USER_ID=U012AB3CD
LOG_LEVEL=INFO
```

**3. Run database migrations**

```bash
alembic upgrade head
```

**4. Start the bot**

```bash
python main.py
```

## Docker (Synology NAS / self-hosted)

```bash
docker-compose up -d
docker-compose logs -f coach
```

Data persists in `./data/` (SQLite database + Garmin session tokens).

## Admin Commands

Send these as DMs from the `ADMIN_SLACK_USER_ID` account:

| Command | Effect |
|---------|--------|
| `!admin add <uid>` | Grant access; create athlete row (or re-enable a removed athlete) |
| `!admin remove <uid>` | Revoke access (data retained; reversible with `add`) |
| `!admin list` | List all athletes with name and onboarding status |
| `!admin resync-garmin [<uid>]` | Preview resync; add `--confirm` to execute; add `--confirm --verify` to also verify after |
| `!admin clean-garmin [<uid>]` | Preview full wipe + re-sync; add `--confirm` to execute (deletes manual Garmin workouts too) |
| `!admin verify-garmin [<uid>]` | Compare DB plan against live Garmin — reports missing or unscheduled workouts |
| `!admin morning-checkin [<uid>]` | Manually trigger morning check-in now; add `--force` to skip the "already sent today" check |
| `!admin reset-onboarding <uid>` | Clear onboarding state and conversation history so an athlete can re-onboard from scratch |
| `!admin test-start` | Become a fresh new runner in the current DM channel (creates isolated test athlete) |
| `!admin test-stop` | Return to the normal admin account |

**Resync vs. clean:**
- `resync-garmin` — deletes only app-created workouts (preserves manually added Garmin workouts), then re-uploads from DB. Use this first.
- `clean-garmin` — wipes the entire Garmin workout library including manually created workouts, then re-uploads. Use only when resync doesn't fix the problem.

## Coach Personas

Athletes can switch coaches at any time by asking the bot.

| Key | Name | Style |
|-----|------|-------|
| `classic` | Coach Alex | Elite endurance veteran — polarized training, HRV-obsessed, data-precise and proactive |
| `maya` | Coach Maya | Consistency architect — practical, schedule-aware, optimizes adherence and momentum |
| `jordan` | Coach Jordan | Resilience coach — recovery-first, injury-prevention focused, conservative progression |

## Scheduled Jobs

| Job | Schedule | Action |
|-----|----------|--------|
| Morning check-in | Every 30 min from 06:00 (athlete local time) | Fetch health data + weather → adapt plan → DM athlete |
| Activity poll | Every 10 min | Poll new Garmin activities → telemetry analysis → feedback DM |
| Weekly review | Sunday 20:00 (system time) | Aggregate week → adapt next week → sync next 2 weeks to Garmin → DM summary |
| Garmin reconciliation | Daily 08:30 (system time) | Find future workouts missing Garmin IDs and re-sync them (self-healing) |

## Key Constraints

- **Garmin MFA must be disabled** — TOTP/push MFA cannot be automated
- **Encryption key** — losing `ENCRYPTION_KEY` makes all stored Garmin passwords unreadable; back it up
- **Garmin session files** — stored at `GARMIN_SESSION_DIR/{athlete_id}/`; loss forces re-authentication
- **Access control** — only Slack user IDs listed in `ALLOWED_SLACK_USER_IDS` can interact with the bot; leave it empty to enable open enrollment (any user can self-onboard)

## Development

```bash
# Linting
ruff check .
ruff check . --fix

# Tests
pytest tests/unit/
pytest tests/integration/      # requires test DB + mock clients

# Create a new database migration
alembic revision --autogenerate -m "description"
```
