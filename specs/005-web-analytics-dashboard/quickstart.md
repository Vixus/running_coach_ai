# Quickstart: Web Analytics Dashboard

## Prerequisites

- Existing running coach bot running (or SQLite DB populated)
- Python 3.11+ with `.venv` active
- `.env` file with existing bot config

## New environment variables

Add to `.env`:

```bash
WEB_SECRET_KEY=<random 32-byte hex>   # python -c "import secrets; print(secrets.token_hex(32))"
WEB_PORT=8080                          # port for the web server (default 8080)
```

## Install new dependencies

```bash
pip install flask
# update requirements.txt: flask>=3.0.0
```

## Run database migrations

```bash
alembic upgrade head
```

This applies three new migrations:
1. Adds `web_username`, `web_password_hash`, `is_admin` to `athletes`
2. Creates `run_feedback` table
3. Creates `weekly_review_summaries` and `web_events` tables

## Set web credentials for an athlete

```bash
python scripts/set_web_credentials.py --slack-id U12345678 --username sarah --password hunter2
# Use --admin to also set is_admin=True
python scripts/set_web_credentials.py --slack-id U12345678 --username admin --password secret --admin
```

## Run the web server (standalone)

```bash
python web.py
# → Running on http://0.0.0.0:8080
```

## Docker Compose (production)

The `docker-compose.yml` has a new `web` service. Start both services:

```bash
docker-compose up -d
docker-compose logs -f web    # web server logs
docker-compose logs -f coach  # bot + scheduler logs
```

## Access the dashboard

Open `http://<host>:8080` in a browser. Log in with the credentials set above.

## Admin screen

Users with `is_admin=True` see an "Admin" link in the sidebar. The Admin screen shows the last 50 events by default, filterable by category and severity.

## Troubleshooting

**SQLite locked errors**: Ensure WAL mode is active. The web server sets it on every connection. If the bot is the only process and errors persist, check that only one `web.py` instance is running.

**Garmin sync from web fails**: The web sync uses the same Garmin session files as the bot. If the session has expired, re-authenticate via the Slack onboarding credential modal.

**Coach chat not loading history**: The web chat shares `ConversationMessage` rows with the Slack bot. If history is missing, verify `DB_PATH` in both services points to the same file (via the shared Docker volume).
