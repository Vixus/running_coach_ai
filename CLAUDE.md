# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

- Do not make any changes until you have 95% confidence in what you need to build. Ask clarifying questions until you reach that confidence — even for small tasks. It's better to ask one extra question than to build the wrong thing.
- Challenge product ideas that have poor UX, unclear value, or better alternatives. You are a collaborator, not an executor — push back with reasoning before implementing.
- Specs for features live in `specs/<NNN>-<feature-name>/` (numbered directories). Always check the relevant spec before implementing.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the scheduler (morning check-ins, activity polling, weekly reviews)
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
| `DB_PATH` | no | `/data/coach.db` | SQLite file path |
| `GARMIN_SESSION_DIR` | no | `/data/garmin_sessions/` | Per-athlete garth OAuth token cache |
| `ENCRYPTION_KEY` | yes | — | Fernet key for Garmin passwords at rest |
| `WEB_SECRET_KEY` | yes | — | Flask session secret |
| `WEB_PORT` | no | `8080` | Web dashboard port |
| `BOOTSTRAP_ADMIN_EMAIL` | no | — | First-deploy admin seed (creates account if missing) |
| `BOOTSTRAP_ADMIN_PASSWORD` | no | — | Paired with `BOOTSTRAP_ADMIN_EMAIL` |
| `LOG_LEVEL` | no | `INFO` | Python logging level |
| `LOG_FILE` | no | `""` | If set, also log to rotating file (10 MB × 5) |
| `GARMIN_TIMEOUT` | no | `30` | Garmin API call timeout (seconds) |
| `STORY_IMAGE_DIR` | no | `/data/story_images/` | Athlete story cover image uploads |

## Architecture

### Process model

Two separate entry points share the same SQLite database:

- **`main.py`** — APScheduler `BlockingScheduler` running morning check-in, activity poll, weekly review, Garmin reconciliation, and health backfill jobs.
- **`web.py`** — Flask dashboard via `create_app()` from `running_coach_ai/web/app.py`. Runs on `WEB_PORT`.

All shared state flows through SQLite. Each scheduler job and HTTP request creates its own DB session per operation. SQLite WAL mode is enabled on every connection (`web.py`) to allow concurrent readers alongside writers.

### Identity & auth

Athletes are identified by `Athlete.id` (integer PK). The login path accepts either `email` or the legacy `web_username`; both are unique. New signups are gated by `InviteToken` rows issued from the admin tab.

First-deploy admin seeding: set `BOOTSTRAP_ADMIN_EMAIL` + `BOOTSTRAP_ADMIN_PASSWORD` env vars. `create_app()` creates the athlete row on startup (or updates the password if the email already exists) and sets `is_admin=True`.

### Onboarding (`coach/onboarding.py`)

Surface-agnostic. The web `/api/chat/message` endpoint dispatches to `coach.onboarding.handle_turn()` whenever `athlete.onboarding_complete == False`. Claude drives the 8-question intake; on the closing turn it emits `<onboarding_complete>{...json...}</onboarding_complete>`, which is parsed and stored in `athlete.pending_onboarding_data` (with `pending_onboarding_data_created_at` for 24h TTL). The chat response includes `{"onboarding": {"pending_garmin": true}}` to trigger the front-end Garmin credentials modal. Submitting the modal calls `/api/onboarding/garmin-creds` which validates the creds, runs `coach.onboarding.complete_onboarding()` (creates `Goal`, generates plan, syncs week 1 to Garmin), and returns the week-1 summary.

### Coaching conversation (split across three files)

The conversation flow is split across three files in `coach/`:

| File | Responsibility |
|---|---|
| `coach/prompt.py` | `build_system_prompt()` + the 8 context-section helpers (athlete profile, plan/week, health, recent workouts, calendar, weather, memories, run-telemetry spotlights). Also owns `_validate_garmin_sync` (the inline DB↔Garmin reconciliation pass that runs while building the calendar section). |
| `coach/side_effects.py` | The five XML tag extractors and their helpers — `extract_and_apply_plan`, `extract_and_sync_garmin`, `extract_and_save_memories`, `extract_prescription_switch`, `extract_coach_switch`. |
| `coach/conversation.py` | Orchestrator only — `process_message`, the `<garmin_fetch/>` re-call loop, and DB persistence of conversation messages. |

**`process_message(athlete, user_text, db_session, source="web", coach_key=None) -> str`** is the central coaching function. It temporarily overrides `athlete.coach_key` in a `try/finally` block (ephemeral — does not persist to DB), then calls Claude and returns the response text.

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

Note: `coach/persona.py` is now a narrow utility module — `call_claude()` (the Anthropic API wrapper) plus unit-conversion helpers (`format_miles`, `format_pace_mi`, `km_to_mi`, `mi_to_km`, `round_to_5`). All persona text lives in `coach/personas.py`; callers use `get_persona(athlete.coach_key).persona_block`.

### Notification inbox (`coach/notify.py`)

Every coach-initiated message — morning check-in, post-run feedback, weekly review, system alerts — writes a `Notification` row via `notify(db, athlete, kind=..., title=..., body=..., action_path=...)`. The web app polls `/api/notifications/unread-count` every 30s and shows a bell badge; clicking the bell opens a slide-in panel listing recent notifications. Clicking a notification navigates to its `action_path` (e.g. `/app#chat`, `/app#activities/123`, `/app#review`) and marks it read. Pages re-fetch when `latest_at` advances, so new data appears without a manual refresh.

### Web dashboard (`running_coach_ai/web/`)

Flask 3.x app factory (`create_app()`) with blueprints deferred inside the factory. Auth uses Flask sessions (`athlete_id` in session) with `@login_required` / `@admin_required` decorators from `web/auth.py`.

**API routes:**

| Blueprint | Routes |
|---|---|
| `web/auth.py` | `POST /auth/login`, `POST /auth/logout`, `POST /auth/signup`, `GET /auth/me` |
| `web/api/activities.py` | `GET /api/activities`, `POST /api/activities/<id>/feedback` |
| `web/api/plan.py` | `GET /api/plan`, `POST /api/plan/sync`, `POST /api/plan/workout-preview` |
| `web/api/chat.py` | `GET /api/chat/history`, `POST /api/chat/message` |
| `web/api/onboarding.py` | `POST /api/onboarding/garmin-creds`, `POST /api/onboarding/skip-garmin` |
| `web/api/notifications.py` | `GET /api/notifications`, unread-count, mark-read, read-all |
| `web/api/admin.py` | Admin-only: athletes CRUD, Garmin resync/clean/verify, invites, events, story start-interview/generate/render/hard-delete/list-templates |
| `web/api/magazine.py` | `GET /api/magazine`, `GET/POST /api/coach` |
| `web/api/stories.py` | Athlete story API — opt-in, intro-acknowledged, list/current, swap template, regenerate, publish/unpublish, soft-delete, image upload/get/patch/delete, session respond/decline |
| `web/routes/public_story.py` | Public no-auth `GET /story/<token>` (with rate limit + noindex) and `GET /robots.txt` |

**`WebEvent` dual-sink logging** (`web/events.py`): `WebEventHandler` writes log records to the `web_events` table; `web_event()` is a convenience helper for explicit event writes.

### Athlete Story (`coach/story.py`, `coach/story_templates.py`)

The story feature is opt-in (default `Athlete.story_opt_in=False`). Once opted in and the multi-page intro modal is acknowledged (`story_intro_seen_at`), event triggers fire interview sessions. Five trigger kinds with priority order (highest first): `race_complete > race_upcoming > pr_set > pace_recalibration > difficult_week`. Detection lives inside the existing pipelines (no new APScheduler job): `_ingest_and_feedback` for `pr_set`/`race_complete`, `_run_morning_checkin_for_athlete` for `race_upcoming`/`difficult_week`, `extract_and_apply_plan` for `pace_recalibration`. Each trigger runs through `coach.story.fire_trigger_if_eligible(...)` which enforces opt-in + intro-seen + active-session + 24h priority dedup gates.

Sessions ask 5–10 adaptive questions via Claude (C-001 prompt), each with 2–4 multiple-choice options + a free-text fallback. The conversation hook in `coach/conversation.py:_handle_message_core` short-circuits regular coaching turns when a `StoryQuestion` is pending, capturing the user's reply via `coach.story.capture_chat_answer`.

When a `race_complete` milestone fires (and ≥4 questions are answered across linked sessions), `generate_story` produces a 400–600 word editorial via Claude (C-002 prompt) with auto-template-selection in the same call. Five magazine templates ship in v1: `vogue` / `runners_world` / `times_long_read` / `outside` / `gq_profile` — each declared by a directory under `web/static/story_templates/<key>/` containing `template.json` + `cover.html` + `inside.html` + `template.css` + `thumbnail.jpg`. Athletes can swap templates instantly (no Claude call); swapping locks the template against future regenerations and switches the prompt to the locked-voice variant per FR-S035.

Public sharing happens at `/story/<share_token>` — server-rendered Jinja2, no auth, `noindex` meta, `robots.txt: Disallow: /story/`, in-memory per-IP sliding-window rate limit (30 req/min). Unpublished/deleted stories return 404 except for `?preview=1` viewed by the owning athlete or an admin.

Admin tooling under `/api/admin/...` (gated by `is_admin=True`): start-interview (bypasses real-trigger detection + intro-modal gate), generate-story (synchronous; 30s target, 60s timeout returns 504 + `story_failed` notification), render (admin override of template_key without locking), hard-delete (cascade story + image files + sessions), list-templates.

### Garmin integration (`garmin/`)

- **Auth** (`client.py:get_garmin_client`): loads cached garth OAuth tokens from `GARMIN_SESSION_DIR/{athlete_id}/`; falls back to full re-auth with decrypted credentials if expired.
- **Deletion API**: `delete_workout` and `remove_workout_schedule` use `garmin.garth.request("DELETE", "connectapi", path, api=True)` — the `garminconnect` library does **not** expose a `delete_workout` method.
- **Pace encoding** (`workout_builder.py`): Garmin's `pace.zone` target values are **speed in m/s as floats** (NOT sec/km). Formula: `speed_ms = 1000.0 / (pace_min_per_km * 60)`. tv1 (faster bound) = `speed_ms * 1.05`, tv2 (slower bound) = `speed_ms * 0.95`. Example: 9:30/mi (5.905 min/km) → speed = 2.824 m/s → tv1=2.965, tv2=2.683.
- **Sync scope**: `sync_week_to_garmin` only uploads workouts with `scheduled_date >= today`. Past dates are never written to Garmin.
- **Admin operations** (`garmin/admin.py`): `verify_garmin_for_athlete()`, `resync_garmin_for_athlete()`, `clean_garmin_for_athlete()` — all return structured dicts (counts, failed dates, error string), consumed by `/api/admin/athletes/<id>/...` endpoints.

### Scheduler jobs (split per job)

`scheduler/jobs.py` is the registration entrypoint and re-exports the public symbols. Each job body lives in its own module:

| Job module | Trigger | Action |
|---|---|---|
| `scheduler/morning.py` (`morning_checkin_{id}`) | Daily 07:00 athlete local time | Fetch Garmin health → weather → Claude → adapt plan → write Notification |
| `scheduler/activity_poll.py` (`activity_poll`) | Every 30 min, 06:00–22:00 only | Poll new Garmin activities → telemetry → biomechanics → write feedback Notification |
| `scheduler/weekly_review.py` (`weekly_review`) | Sunday 20:00 system time | Aggregate week → Claude review → adapt next week → sync Garmin → write Notification |
| `scheduler/reconcile.py` (`garmin_reconciliation`) | Daily 08:30 | Verify DB plan matches Garmin library |
| `scheduler/health_backfill.py` (`health_backfill`) | Daily 14:00 | Catch missed morning health snapshots |
| `scheduler/jobs.py` (`refresh_athlete_morning_jobs`) | Every 5 min | Idempotently register morning jobs for newly-onboarded web athletes |

Morning check-in gate: skips if `athlete.last_morning_checkin_date == today` (dedup) or before 06:00 local. For athletes with Garmin, waits for `training_readiness` (or sleep_score fallback) to be populated; retries on next tick until 12:00, then skips for the day.

New athletes get their morning job registered automatically by the `refresh_athlete_morning_jobs` tick (within 5 minutes of completing onboarding) — no scheduler restart needed.

### Unit conversions

All distances and paces are stored internally in **km / min-per-km**. The display layer always converts to **miles / min-per-mile** for athlete-facing output using helpers in `coach/persona.py`: `format_miles()`, `format_pace_mi()`, `km_to_mi()`, `mi_to_km()`. Paces inside `<plan>` JSON tags must be in min/km (internal format); Claude is instructed to convert.

### Data isolation

Every DB query on behalf of an athlete **must** include `athlete_id`. Use `database/session.py:scoped_query(db_session, Model, athlete_id)` for athlete-scoped lookups. This is a hard requirement — no cross-athlete data leakage is acceptable.

### Admin (`coach/admin_ops.py`, `garmin/admin.py`)

Admin operations are exposed via `/api/admin/...` endpoints, gated by `is_admin=True` on the athlete. The admin tab in the web app provides three views:

| View | Capabilities |
|---|---|
| Athletes | List all; allow/disable; force morning check-in; reset onboarding; per-athlete Garmin verify, resync, clean (with confirm dialogs) |
| Invites | Create one-time signup tokens (30-day TTL) and copy signup URLs; list outstanding/used invites |
| Events | Filtered event log (auth, garmin, claude, scheduler, http × info/warn/error) |

### Key constraints

- **Garmin MFA**: Athletes must disable MFA on Garmin Connect — TOTP/push MFA cannot be automated.
- **Garmin session files**: Stored at `GARMIN_SESSION_DIR/{athlete_id}/`. Loss forces re-authentication.
- **Encryption key**: Losing `ENCRYPTION_KEY` makes all stored Garmin passwords unreadable.
- **DB schema**: `main.py` and `web.py` call `Base.metadata.create_all(engine)` at startup (idempotent). Alembic is used for schema migrations on existing deployments.
- **speckit workflow**: Feature development follows `specify → clarify → plan → tasks → implement` using the slash commands in `.claude/commands/`. Specs live in numbered directories under `specs/` (e.g. `specs/006-magazine-features/`).

### Magazine frontend (`web/static/magazine.html`, `web/static/magazine.js`)

The athlete-facing UI is a single static HTML page. `magazine.js` is extracted script from the page and hydrates all sections from one `GET /api/magazine` call on load. Sections scroll into view (`SECTIONS` array drives the nav). `ACTIVITIES_DATA`, `WEEK_DATA`, and `METRIC_TS` are module-level globals populated from the API response. Charts are built lazily as sections enter the viewport via `IntersectionObserver`. There is no bundler — plain ES2020 in `<script>` tags.
