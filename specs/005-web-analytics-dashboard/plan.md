# Implementation Plan: Web Analytics Dashboard

**Branch**: `005-web-analytics-dashboard` | **Date**: 2026-04-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/005-web-analytics-dashboard/spec.md`

## Summary

Add a desktop web dashboard that surfaces athlete health metrics, training calendar, activity feed with biomechanics, coach chat, weekly review, and an admin event log. Implemented as a standalone Flask server (`web.py`) running as a second Docker Compose service alongside the existing Slack bot, sharing the same SQLite database via WAL mode. The frontend is a React SPA served from a single `app.html` file using CDN React (no build step). Three new database models are introduced: `RunFeedback`, `WeeklyReviewSummary`, and `WebEvent`.

## Technical Context

**Language/Version**: Python 3.11+ (matching existing codebase)
**Primary Dependencies**: Flask 3.x, werkzeug (password hashing), existing SQLAlchemy/Alembic stack
**Storage**: SQLite (existing) with WAL mode enabled in the web process
**Testing**: pytest (existing); new unit tests in `tests/unit/web/`, integration tests in `tests/integration/web/`
**Target Platform**: Docker Compose on Synology NAS (Linux), desktop browser (1280px+)
**Project Type**: Web service (Flask API) + React SPA frontend
**Performance Goals**: Dashboard load <2s; chat response <15s; admin event feed load <1s
**Constraints**: No Node.js build toolchain; SQLite WAL concurrent access; single shared DB file
**Scale/Scope**: 1–10 athletes; ~50 events/day in WebEvent log; 6 screens

## Constitution Check

- [x] **I. Athlete Data Isolation**: All `/api/*` endpoints extract `athlete_id` from the authenticated session and pass it to `scoped_query()`. Admin routes additionally check `is_admin`. No endpoint accepts `athlete_id` as a client-supplied query parameter.
- [x] **II. AI Coach Persona Integrity**: Web chat reuses `conversation.process_message()` — the same system prompt assembly and XML side-effect processing as the Slack bot. Coach persona is loaded from `athlete.coach_key` via `get_persona()`.
- [x] **III. Metric-First Architecture**: All API responses convert stored km/min-per-km to miles/min-per-mile in the Flask response layer using existing `format_miles()` / `format_pace_mi()` helpers. Internal storage is unchanged.
- [x] **IV. Encrypted Secrets Management**: Web credentials use PBKDF2-SHA256 password hashing (werkzeug). Garmin credentials are untouched. `WEB_SECRET_KEY` stored in environment only.
- [x] **V. Graceful Degradation**: Garmin sync failures return `{"ok": false, "error": "..."}` — no crash. Claude API failures in chat return a user-facing error string. WebEvent captures both outcomes.
- [x] **VI. Structured Observability**: WebEvent dual-sink logging is the core admin feature. Existing `logging.getLogger()` call sites are augmented with a custom handler; no existing log calls are changed.
- [x] **VII. Test-First Development**: Unit tests for all new Flask routes, all new models, and the `process_message` refactor. Integration tests for the full auth flow and chat round-trip.
- [x] **VIII. Configuration as Code**: `WEB_SECRET_KEY` and `WEB_PORT` added to Pydantic `Settings` class. No hardcoded values.

## Project Structure

### Documentation (this feature)

```text
specs/005-web-analytics-dashboard/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── web-api.md       # API endpoint contracts
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
running_coach_ai/
├── web/                         # NEW — Flask web server package
│   ├── __init__.py
│   ├── app.py                   # Flask app factory, WAL mode, blueprint registration
│   ├── auth.py                  # /auth/login, /auth/logout, login_required decorator
│   ├── events.py                # WebEventHandler (logging.Handler), web_event() helper, cleanup
│   ├── api/
│   │   ├── __init__.py
│   │   ├── dashboard.py         # GET /api/dashboard
│   │   ├── activities.py        # GET /api/activities, POST /api/activities/<id>/feedback
│   │   ├── plan.py              # GET /api/plan, POST /api/plan/sync
│   │   ├── chat.py              # GET /api/chat/history, POST /api/chat/message
│   │   ├── review.py            # GET /api/review
│   │   └── admin.py             # GET /api/admin/events (admin-only)
│   ├── static/
│   │   └── app.html             # React SPA (CDN React + Babel standalone)
│   └── templates/
│       └── login.html           # Plain HTML login form
├── coach/
│   └── conversation.py          # MODIFIED — extract process_message() pure function
├── database/
│   └── models.py                # MODIFIED — add RunFeedback, WeeklyReviewSummary, WebEvent;
│                                #             add web_username, web_password_hash, is_admin to Athlete
├── scheduler/
│   └── jobs.py                  # MODIFIED — persist WeeklyReviewSummary after Slack DM
└── config.py                    # MODIFIED — add WEB_SECRET_KEY, WEB_PORT settings

web.py                           # NEW — standalone entry point: python web.py
docker-compose.yml               # MODIFIED — add `web` service
requirements.txt                 # MODIFIED — add flask>=3.0.0

scripts/
└── set_web_credentials.py       # NEW — CLI helper to set web_username/password/is_admin

tests/
├── unit/
│   └── web/
│       ├── test_auth.py
│       ├── test_dashboard_api.py
│       ├── test_activities_api.py
│       ├── test_plan_api.py
│       ├── test_chat_api.py
│       ├── test_review_api.py
│       ├── test_admin_api.py
│       └── test_web_events.py
└── integration/
    └── web/
        ├── test_auth_flow.py
        └── test_chat_round_trip.py

database/migrations/versions/
├── xxx_add_web_credentials_to_athletes.py
├── xxx_add_run_feedback.py           # also adds coach_analysis to completed_workouts
├── xxx_add_weekly_review_summary_and_web_event.py
└── xxx_add_source_to_conversation_messages.py
```

**Structure Decision**: Option 2 (web application variant). The `running_coach_ai/web/` package is the new web server; the existing `running_coach_ai/` package is the shared domain layer used by both the Slack bot and the web server. `web.py` at the repo root is the standalone entry point for the web service (mirroring `main.py` for the bot).

## Complexity Tracking

No constitution violations. No complexity justification required.
