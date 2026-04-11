# Implementation Plan: AI Running Coach

**Branch**: `001-ai-running-coach` | **Date**: 2026-03-31 | **Spec**: [spec.md](spec.md)

## Summary

A self-hosted AI running coach service that ingests Garmin health and workout data, maintains adaptive training plans for multiple independent athletes, and communicates with athletes via a Slack bot. The coach is powered by Claude and responds to natural-language conversation about any training topic — not a command-driven bot. Automated jobs handle daily morning check-ins, post-run feedback (including biomechanical telemetry analysis), and weekly plan reviews. All athlete data is fully isolated. Hosted in Docker on a Synology NAS.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**:

- `anthropic` — Claude API (claude-sonnet-4-6) for coach intelligence
- `python-garminconnect` — Garmin Connect data fetch and workout upload
- `slack-bolt` + `slack-sdk` — Slack bot, socket mode, proactive messaging
- `apscheduler` — scheduled jobs (morning check-in, activity polling, weekly review)
- `sqlalchemy` — ORM for all DB entities
- `pydantic-settings` — typed configuration from `.env`
- `cryptography` — Fernet symmetric encryption for Garmin credentials at rest
- `fit-tool` — building `.fit` files for structured workout upload (research required)
- `httpx` — async HTTP for Open-Meteo weather API
- `alembic` — database migrations

**Storage**: SQLite (file-based, volume-mounted at `/data/coach.db`)
**Testing**: pytest + pytest-asyncio
**Target Platform**: Linux container (Docker), ARM64 or x86_64 (Synology NAS)
**Project Type**: Long-running service / daemon
**Performance Goals**:

- Real-time conversation reply: <10 seconds end-to-end
- Post-run feedback delivery: <10 minutes from activity sync
- Morning check-in message: within 5 minutes of scheduled time

**Constraints**:

- No public inbound ports — Slack socket mode only
- Must run within Synology NAS resource envelope (low RAM, no GPU)
- Garmin credentials encrypted at rest (Fernet)
- Full data isolation between athletes — no cross-contamination at any layer
- Graceful degradation on external API failure (no crashes)

**Scale/Scope**: Small — handful of athletes, home/personal use, single instance

## Constitution Check

_GATE: Must pass before Phase 0 research. Re-check after Phase 1 design._

Project constitution is defined in `.specify/memory/constitution.md` (v1.0.0). This plan remains aligned with MUST-level constraints, especially athlete data isolation (Principle I), graceful degradation (Principle V), and test-first development (Principle VII).

| Gate                                      | Status | Notes                                                                        |
| ----------------------------------------- | ------ | ---------------------------------------------------------------------------- |
| Single responsibility per module          | PASS   | Architecture separates garmin/, coach/, slack/, scheduler/, weather/ cleanly |
| No global mutable state                   | PASS   | All state flows through DB and per-athlete sessions                          |
| Secrets never in source                   | PASS   | `.env` + Pydantic settings; credentials encrypted in DB                      |
| Graceful failure of external dependencies | PASS   | Spec FR-027 mandates retry + skip-on-stale                                   |
| Data isolation enforced at query level    | PASS   | Every query scoped by `athlete_id`                                           |
| No unnecessary complexity                 | PASS   | SQLite over Postgres, single process, no microservices                       |

No violations. Proceeding.

## Reconciliation Addendum (2026-04-04)

### Scope

This addendum closes implementation drift identified after initial delivery for SC-002, FR-027, FR-006, FR-004, FR-025/FR-026, SC-001/SC-011, FR-001, FR-003, and FR-023.

### Contract and Behavior Updates

- Activity polling contract: `scheduler/jobs.py` activity polling interval is reduced from 30 minutes to 10 minutes to meet SC-002.
- Garmin retry contract: `retry_garmin` must be applied to Garmin network-facing functions in `garmin/client.py` (`get_garmin_client`, `get_health_snapshot`, `poll_new_activities`, `upload_workout`, `schedule_workout`, `delete_workout`, `get_activity_details`, `fetch_historical_activities`).
- Stale-window contract: after retry exhaustion for time-bound scheduled operations, each scheduler job (`morning_checkin`, `activity_poll`, `weekly_review`) must perform a stale-window check and explicitly log skipped stale operations before returning.
- Multi-goal planning contract: a deterministic conflict-resolution pass is required in `coach/planner.py` or `coach/adapter.py` before persisting/syncing new plan sessions for overlapping goals.
- Onboarding state contract: onboarding resume must be backed by persisted `athlete.onboarding_step`; conversation history is advisory context only.
- Onboarding profile contract: experience level must be collected and normalized to `beginner|intermediate|advanced` before `Goal` creation.
- Biomechanical profile contract: trend direction fields are required for heart-rate drift, heart-rate/pace decoupling, and easy-zone compliance in addition to cadence trend.
- Conversation latency contract: Claude calls must enforce an explicit timeout <10s and include an immediate fallback acknowledgement path in Slack.
- Data isolation contract: athlete-scoped ORM reads should use `scoped_query()` as the default path for enforcement.
- Admin interaction contract: accepted command surface is DM `!admin ...` commands in socket mode; slash commands are not part of this architecture.
- Injury-risk guardrail contract: introduce deterministic pre-check thresholds that inject mandatory referral guidance into prompts when risk conditions are met.
- PlannedWorkout timestamp contract: `created_at` is set on insert, `updated_at` is maintained on each workout mutation, and `last_garmin_synced_at` is set only after successful Garmin upload/resync. Required write points include onboarding sync, conversation-driven resync, and weekly review resync paths.

### Testing Strategy Additions

- Add unit coverage for poll interval configuration and stale-window skip logging paths in `scheduler/jobs.py`.
- Add unit coverage that verifies `@retry_garmin()` wrappers are applied to required Garmin functions.
- Add unit/integration coverage for deterministic onboarding resume via `onboarding_step`.
- Add tests validating multi-goal conflict-resolution behavior across overlapping `PlannedWorkout` ranges.
- Add feedback prompt tests asserting trend labels for all key biomechanical metrics.
- Add conversation timeout and fallback acknowledgement tests for SC-011.
- Add data-isolation tests to prevent new non-scoped athlete queries.
- Add model/migration and behavior tests for PlannedWorkout timestamp semantics across create, mutate, and successful Garmin sync operations.

### Revision: Implementation Sync 2026-04-04

- Reason: Reconciled reliability, latency, contract, and safety gaps discovered between implementation behavior and feature requirements.

## Project Structure

### Documentation (this feature)

```text
specs/001-ai-running-coach/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── slack-events.md
│   ├── garmin-data.md
│   └── coach-output.md
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
running_coach_ai/
├── main.py                    # Entry point: starts scheduler + Slack bot
├── config.py                  # Pydantic settings from .env
├── database/
│   ├── models.py              # SQLAlchemy ORM models
│   ├── session.py             # DB engine + session factory
│   └── migrations/            # Alembic migrations
├── garmin/
│   ├── client.py              # Auth, session caching, data fetch
│   ├── parser.py              # Normalise raw Garmin API responses
│   ├── telemetry.py           # Extract + store time-series streams + laps
│   └── workout_builder.py     # Build and upload workouts to Garmin calendar
├── coach/
│   ├── persona.py             # System prompt + coach personality definition
│   ├── planner.py             # Initial plan generation via Claude
│   ├── adapter.py             # Daily plan adaptation logic
│   ├── feedback.py            # Post-run analysis prompt assembly + response
│   └── biomechanics.py        # Telemetry analytics → RunningProfile updates
├── weather/
│   └── client.py              # Open-Meteo fetch + weather adjustment rules
├── slack/
│   ├── bot.py                 # Slack Bolt App + SocketModeHandler
│   ├── conversation.py        # Context assembly, rolling window, memory extraction
│   ├── onboarding.py          # Stateful onboarding flow with resume support
│   └── admin.py               # Admin commands: add/remove athletes
├── scheduler/
│   └── jobs.py                # APScheduler job definitions (morning, poll, weekly)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── docker-compose.yml
├── Dockerfile
├── .env.example
└── alembic.ini
```

**Structure Decision**: Single-project daemon service. No frontend. All external interfaces are inbound Slack events (socket mode) and outbound API calls. Clean domain separation into `garmin/`, `coach/`, `slack/`, `scheduler/`, and `weather/` packages.

## Complexity Tracking

No constitution violations to justify. N/A.

## Reconciliation Addendum (2026-04-10)

### Scope

Schema additions and behavioural changes landed on the `001-ai-running-coach` branch after the 2026-04-04 reconciliation. This addendum closes the remaining drift.

### Contract and Behaviour Updates

- **workout_name contract**: `PlannedWorkout` now carries a `workout_name TEXT NULLABLE` column. When non-null it is emitted as `workoutName` in the Garmin workout upload JSON (`garmin/workout_builder.py:build_workout_json`). The `<plan>` JSON schema in all persona blocks now includes an optional `workout_name` field — Claude may include it to label sessions; omitting it falls back to a default name derived from `workout_type`.
- **activity_type contract**: `CompletedWorkout` now carries `activity_type TEXT NULLABLE` (Garmin `typeKey`). `garmin/parser.py` populates it from `activityType.typeKey` on both the activity-list and activity-detail responses. `slack/conversation.py` and `scheduler/jobs.py` use a `RUNNING_TYPES` constant to filter non-running activities out of running-specific weekly load aggregations.
- **lthr_bpm contract**: `Athlete` gains `lthr_bpm INTEGER NULLABLE` for Lactate Threshold Heart Rate. `scheduler/jobs.py` attempts to derive it from the Garmin profile on each activity-poll cycle (if not yet set) and stores it; it is cleared to `None` by `!admin clean-garmin`.
- **training_readiness contract**: `HealthSnapshot` gains `training_readiness INTEGER NULLABLE`. `garmin/client.py:get_health_snapshot` calls `get_morning_training_readiness(date_str)` and attaches the raw result; `garmin/parser.py` extracts the score integer and persists it. `coach/adapter.py` and `slack/conversation.py` include it in health context sections when non-null.
- **!admin morning-checkin contract**: `slack/admin.py:handle_admin_command` now accepts a `slack_client` keyword argument (None-safe). The new `!admin morning-checkin [<uid>] [--force]` branch calls `scheduler/jobs.py:_run_morning_checkin_for_athlete(athlete_id, slack_client)`. `--force` clears `athlete.last_morning_checkin_date` before the call. `slack/bot.py` passes `slack_client=client` to every `handle_admin_command` call.
- **LOG_FILE contract**: `config.py:configure_logging` now checks `settings.LOG_FILE`. When set, a `logging.handlers.RotatingFileHandler` (10 MB, 5 backups, UTF-8) is attached to the root logger in addition to the existing stdout `basicConfig` handler.

### Testing Strategy Additions

- Add unit test for `!admin morning-checkin` (with and without `--force`) in `tests/unit/test_admin.py`.
- Add unit test for `LOG_FILE` rotating handler setup in `tests/unit/test_config_logging.py`.
- Add unit tests verifying `workout_name` round-trip (plan tag to DB to Garmin upload) in `tests/unit/test_workout_builder.py`.
- Add unit tests for `activity_type` filtering logic in `tests/unit/test_activity_type_filter.py`.
- Add unit tests for `lthr_bpm` derivation and persistence in `tests/unit/test_lthr_derivation.py`.
- Add unit tests for `training_readiness` parsing and context inclusion in `tests/unit/test_training_readiness.py`.

### Revision: Implementation Sync 2026-04-10

- Reason: Reconciled schema drift (workout_name, activity_type, lthr_bpm, training_readiness), new admin morning-checkin command with slack_client plumbing, and LOG_FILE rotating log handler.
