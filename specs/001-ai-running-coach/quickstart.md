# Quickstart: AI Running Coach

**Branch**: `001-ai-running-coach` | **Date**: 2026-03-31

---

## Prerequisites

- Python 3.11+
- Docker + Docker Compose (for deployment on Synology NAS)
- A Slack workspace with a bot app created (free tier sufficient)
- A Garmin Connect account for each athlete (unofficial consumer API — no developer account needed)
- An Anthropic API key

---

## 1. Environment Setup

```bash
# Clone and enter repo
git clone <repo>
cd running_coach_ai

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
```

---

## 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
```

Generate the Fernet encryption key (run once, store the output):
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Required `.env` values:

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SLACK_BOT_TOKEN` | Bot token (`xoxb-...`) |
| `SLACK_SIGNING_SECRET` | Signing secret from Slack app settings |
| `SLACK_APP_TOKEN` | App-level token (`xapp-...`) for Socket Mode |
| `DB_PATH` | SQLite path, e.g. `/data/coach.db` |
| `GARMIN_SESSION_DIR` | Directory for per-athlete Garmin session files, e.g. `/data/garmin_sessions/` |
| `ENCRYPTION_KEY` | Fernet key (generated above) |
| `ALLOWED_SLACK_USER_IDS` | Comma-separated Slack user IDs who can use the bot |
| `ADMIN_SLACK_USER_ID` | Single admin user ID for runtime access management |
| `LOG_LEVEL` | `INFO` or `DEBUG` |

---

## 3. Slack App Configuration

In your Slack app dashboard:

1. **Socket Mode**: Enable under "Socket Mode" → generate App-Level Token with `connections:write` scope.
2. **Bot scopes** (OAuth & Permissions): `chat:write`, `im:history`, `im:write`, `app_mentions:read`.
3. **Event Subscriptions**: Subscribe to `message.im` and `app_mention` bot events.
4. **Slash Commands** (optional): `/status`, `/plan`, `/skip-today` — point to any URL (Socket Mode intercepts).
5. Install app to workspace.

---

## 4. Initialise Database

```bash
# Run Alembic migrations to create all tables
alembic upgrade head
```

---

## 5. Run Locally

```bash
python main.py
```

The service starts:
- APScheduler (morning check-in, activity poll, weekly review jobs)
- Slack SocketModeHandler (inbound DMs and mentions)

Send a DM to the bot in Slack from an `ALLOWED_SLACK_USER_ID` to begin onboarding.

---

## 6. Deploy on Synology NAS (Docker Compose)

```bash
# Build and start
docker-compose up -d

# View logs
docker-compose logs -f coach
```

Ensure the `./data` directory is created on the NAS before first run — it is volume-mounted for DB and Garmin session persistence.

```yaml
# docker-compose.yml (summary)
services:
  coach:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/data
```

No inbound ports are exposed. The bot connects outbound via Slack Socket Mode only.

---

## 7. First Athlete Onboarding

1. Admin sends a DM to the bot with the athlete's Slack user ID (`!admin add U012AB3CD`), or pre-populate `ALLOWED_SLACK_USER_IDS` in `.env`.
2. Athlete sends any message to the bot in Slack.
3. Coach initiates the intake conversation. The athlete answers 8 questions across one or more sessions (onboarding resumes if interrupted).
4. After confirmation, the coach generates a 16–20 week training plan and uploads week 1 to Garmin Connect.

---

## 8. Key Developer Notes

- **Athlete data isolation**: Every DB query MUST include `athlete_id`. No exceptions.
- **Garmin MFA**: Athletes with MFA on their Garmin account cannot be auto-authenticated. They must disable MFA before onboarding.
- **Garmin session caching**: Sessions stored at `GARMIN_SESSION_DIR/{athlete_id}/`. Back these up — losing them forces re-authentication.
- **Encryption key**: Losing `ENCRYPTION_KEY` means all stored Garmin passwords become unreadable. Back it up securely.
- **Claude rate limits**: Each conversation turn and each automated job calls the Claude API. Monitor Anthropic usage dashboard.
- **Multiple concurrent goals**: An athlete may have more than one active Goal. Plan scheduling logic must detect and resolve overlapping sessions.

---

## 9. Running Tests

```bash
pytest tests/unit/
pytest tests/integration/    # Requires a test DB and mock Slack/Garmin/Claude clients
```
