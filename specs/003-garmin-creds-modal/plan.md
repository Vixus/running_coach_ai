# Implementation Plan: Garmin Credentials via Slack Modal

**Branch**: `003-garmin-creds-modal` | **Date**: 2026-04-05 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `specs/003-garmin-creds-modal/spec.md`

## Summary

Replace Garmin Connect credential collection (currently done via chat messages that appear in the Slack message log) with a Slack Block Kit modal. The onboarding conversation no longer asks for credentials. After the athlete confirms their training profile, the bot sends a button message. Clicking the button opens an in-Slack modal form; submission goes directly to the Bolt event handler without appearing in any chat log. The handler encrypts and stores the credentials, then runs the existing `_complete_onboarding()` call unchanged.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Slack Bolt for Python (SocketMode), SQLAlchemy 2.x, Anthropic Claude API, Alembic (migrations)  
**Storage**: SQLite via SQLAlchemy — two new nullable columns on `Athlete`  
**Testing**: pytest with `unittest.mock`  
**Target Platform**: Linux Docker container on Synology NAS  
**Project Type**: Slack bot / background service (single-process, multi-threaded)  
**Performance Goals**: `@app.action` must call `ack()` within 3 seconds; `@app.view` must call `ack()` within 3 seconds — both met trivially since the DB lookup and API call happen before the heavy `_complete_onboarding()` work  
**Constraints**: Slack `plain_text_input` has no password-masking capability (inherent platform limitation); SocketMode means no inbound HTTP ports are opened  
**Scale/Scope**: 5–10 athletes; single-process SQLite; no concurrency concerns beyond normal per-event threading already present

## Constitution Check

_GATE: Must pass before Phase 0 research. Re-check after Phase 1 design._

- [x] **I. Athlete Data Isolation**: All new handlers look up athlete by `body["user"]["id"]` → `Athlete.slack_user_id`; all DB operations scoped to that `athlete_id`. `!admin reset-onboarding <uid>` is scoped to the specified athlete only.
- [x] **II. AI Coach Persona Integrity**: `_SYSTEM_PROMPT` keeps the full coach persona; only the Garmin credential question and example JSON fields are removed. `<onboarding_complete>` XML tag processing is unchanged.
- [x] **III. Metric-First Architecture**: N/A — no new distance or pace data introduced.
- [x] **IV. Encrypted Secrets Management**: Credentials are still encrypted with `encrypt_password()` (Fernet) before `athlete.garmin_password_encrypted` is written. Collection moves from chat to modal; storage is identical.
- [x] **V. Graceful Degradation**: `@app.view` handler wraps all post-ack work in try/except; Garmin auth failures send a DM and re-present the button; bot continues operating.
- [x] **VI. Structured Observability**: All new functions use `logger = logging.getLogger(__name__)` with structured log messages.
- [x] **VII. Test-First Development**: New unit test files for modal handlers and admin reset command; existing tests are unmodified and still pass.
- [x] **VIII. Configuration as Code**: No new environment variables. Modal `action_id` and `callback_id` are module-level string constants, not hardcoded inline.

**Result: All gates PASS. No violations to justify.**

## Project Structure

### Documentation (this feature)

```text
specs/003-garmin-creds-modal/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   └── slack-interactive.md   ← Phase 1 output
└── tasks.md             ← Phase 2 output (from /speckit.tasks)
```

### Source Code (repository root)

```text
running_coach_ai/
├── database/
│   ├── models.py                          ← MODIFIED: +2 columns on Athlete
│   └── migrations/versions/
│       └── <hash>_add_pending_onboarding_data.py  ← NEW: Alembic migration
├── slack/
│   ├── onboarding.py                      ← MODIFIED: system prompt, handle(), +_send_garmin_button()
│   │                                         DELETED: _scrub_credentials()
│   ├── bot.py                             ← MODIFIED: +@app.action, +@app.view handlers
│   └── admin.py                           ← MODIFIED: +reset-onboarding command

tests/unit/
├── test_garmin_creds_modal.py             ← NEW: action + view handler tests
└── test_admin_reset_onboarding.py         ← NEW: admin reset command tests
```

**Structure Decision**: Existing single-project layout. No new modules, packages, or services — all changes are additive modifications to existing files plus new test files.
