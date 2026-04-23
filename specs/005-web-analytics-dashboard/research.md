# Research: Web Analytics Dashboard

**Branch**: `005-web-analytics-dashboard` | **Date**: 2026-04-18

---

## Decision 1: Web Framework

**Decision**: Flask

**Rationale**: The existing codebase is entirely synchronous (SQLAlchemy sync sessions, APScheduler BlockingScheduler, no async anywhere). Flask matches this model cleanly. It provides session management, Blueprint routing, and Jinja2 templating out of the box with minimal boilerplate. FastAPI would bring async complexity and require async SQLAlchemy adapters with no meaningful benefit for a single-user self-hosted app.

**Alternatives considered**:
- FastAPI: modern, auto-docs, but async-first and would require SQLAlchemy async adapter — unnecessary complexity for this scale.
- Django: too heavy, brings ORM conflict with existing SQLAlchemy models.

---

## Decision 2: Frontend delivery — CDN React vs build pipeline

**Decision**: CDN React with Babel standalone (no build step)

**Rationale**: The design prototype already uses this exact pattern (React 18 + Babel standalone from unpkg CDN). A build pipeline would introduce Node.js tooling, `package.json`, and `node_modules` into a Python project. For a single-user self-hosted app, the CDN approach is sufficient and eliminates the build step from the Docker image. The Flask server serves one `app.html` static file; all API data is fetched via `fetch()` calls to `/api/` endpoints.

**Alternatives considered**:
- Vite + React build: production-grade bundling but requires Node.js in the Docker image and a CI build step.
- Jinja2 server-rendered: too limited for the rich interactive UI the design requires.

---

## Decision 3: Authentication — session management

**Decision**: Flask built-in signed cookie sessions (itsdangerous) + werkzeug password hashing

**Rationale**: Flask's session uses a cryptographically signed cookie backed by `SECRET_KEY`. No additional session store (Redis, DB table) is required for a single-user app. werkzeug is already a Flask transitive dependency, providing `generate_password_hash` / `check_password_hash` (PBKDF2-SHA256). Web credentials (username + bcrypt hash) stored as new columns on the existing `Athlete` row — no separate table needed.

**New env var**: `WEB_SECRET_KEY` (random 32-byte hex, generated once at deploy).
**New env var**: `WEB_PORT` (default 8080).

**Alternatives considered**:
- Flask-Login: adds dependency for marginal benefit given small user count.
- JWT tokens: stateless but more complex; overkill for a browser-only app.

---

## Decision 4: SQLite concurrent access — WAL mode

**Decision**: Enable WAL mode via SQLAlchemy `@event.listens_for(engine, "connect")` at web app startup

**Rationale**: The bot process (writer) and web process (reader) share one SQLite file. Default journal mode uses exclusive locks that block reads during writes. WAL (Write-Ahead Logging) allows concurrent readers while the writer commits. This is the standard SQLite solution for this pattern. WAL mode is per-connection and persistent once set — enabling it in both processes is harmless (idempotent).

```python
from sqlalchemy import event
@event.listens_for(engine, "connect")
def set_wal_mode(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
```

---

## Decision 5: Coach Chat — response extraction

**Decision**: Extract a `get_coach_response(athlete, user_message, db_session) -> str` pure function from `conversation.py`

**Rationale**: The existing `handle_message()` couples Claude call + Slack DM send + XML side-effect processing (plan tags, garmin_sync, remember tags). The web chat needs only: load history → call Claude → append to ConversationMessage → return response text. Plan/sync/remember side effects from the web chat should still be processed (the athlete might ask to modify workouts via web chat). A refactored helper shared by both Slack and web paths avoids duplication.

**Approach**: New function `conversation.process_message(athlete, user_text, db_session) -> str` that:
1. Loads the same system prompt (`build_system_prompt`)
2. Appends user message to `ConversationMessage`
3. Calls Claude
4. Applies XML side effects (`<plan>`, `<remember>`, `<garmin_sync/>`)
5. Appends coach response to `ConversationMessage`
6. Returns the cleaned response text

The Slack `handle_message` becomes a thin wrapper calling `process_message` then posting the result via Slack client.

---

## Decision 6: Weekly review persistence

**Decision**: Update `_run_weekly_review` job to persist to new `WeeklyReviewSummary` table after generating the Slack DM

**Rationale**: The job already computes all aggregate data and generates the narrative. Adding a DB write is a 5-line addition. The web screen reads the most recent row for the athlete. One row per week (unique constraint on `athlete_id` + `week_start_date`, upsert on conflict).

---

## Decision 7: WebEvent — dual-sink logging

**Decision**: Custom logging handler that writes to both stdout (structured text) and `WebEvent` DB table

**Rationale**: A Python `logging.Handler` subclass captures log records from designated loggers (web request logger, garmin logger, claude logger, scheduler logger) and persists them to the `WebEvent` table. This keeps the existing `logger.info/warning/error` call sites intact — no new call-site changes needed for events that already log. For auth events (which are security-sensitive and not currently logged at this granularity), explicit `web_event()` helper calls are added at the auth routes.

**Retention**: A daily cleanup job (or lazy cleanup on request) deletes `WebEvent` rows older than 30 days.

---

## Decision 8: Garmin sync from web — reuse existing admin sync logic

**Decision**: Web Garmin sync button calls the same `sync_week_to_garmin` / `resync_athlete_to_garmin` function used by `!admin resync-garmin`

**Rationale**: No duplication. The sync logic in `garmin/` is already well-tested. The web API endpoint wraps it in a try/except and returns structured JSON with `{status: "ok"}` or `{status: "error", reason: "..."}` so the frontend can display the appropriate button state.
