# Tasks: Web Analytics Dashboard

**Input**: Design documents from `/specs/005-web-analytics-dashboard/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/web-api.md ✓

**Tests**: Per Constitution Principle VII, all features MUST have corresponding unit tests. Mock external services (Garmin, Claude, Slack). Integration tests required for auth flow and chat round-trip.

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US7)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project structure, dependencies, and entry point

- [X] T001 Create `running_coach_ai/web/` package with `__init__.py`, `api/__init__.py` in `running_coach_ai/web/`
- [X] T002 [P] Add `flask>=3.0.0` to `requirements.txt` (no flask-cors needed — dashboard is served by Flask itself, so all API calls are same-origin)
- [X] T003 [P] Add `WEB_SECRET_KEY: str` and `WEB_PORT: int = 8080` to `running_coach_ai/config.py` Settings class
- [X] T004 Create `web.py` at repo root: imports Flask app factory, calls `app.run(host="0.0.0.0", port=settings.WEB_PORT)`
- [X] T005 [P] Create `running_coach_ai/web/static/` and `running_coach_ai/web/templates/` directories with `.gitkeep`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Database migrations, Flask app core, auth, and event logging — MUST complete before any user story

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T006 Add `RunFeedback`, `WeeklyReviewSummary`, `WebEvent` models; add `web_username`, `web_password_hash`, `is_admin` columns to `Athlete`; add `coach_analysis` (Text nullable) column to `CompletedWorkout` in `running_coach_ai/database/models.py` per `data-model.md` (all fields, indexes, constraints, relationships)
- [X] T007 Create Alembic migration `add_web_credentials_to_athletes`: adds `web_username` (Text nullable), `web_password_hash` (Text nullable), `is_admin` (Boolean not null default False) to `athletes` table in `running_coach_ai/database/migrations/versions/`
- [X] T008 [P] Create Alembic migration `add_run_feedback`: creates `run_feedback` table with all columns and unique constraint; also adds `coach_analysis` (Text nullable) to `completed_workouts` table per `data-model.md` in `running_coach_ai/database/migrations/versions/`
- [X] T009 [P] Create Alembic migration `add_weekly_review_summary_and_web_event`: creates `weekly_review_summaries` and `web_events` tables with all columns, indexes, and constraints per `data-model.md` in `running_coach_ai/database/migrations/versions/`
- [X] T010 Create `running_coach_ai/web/app.py`: Flask app factory `create_app()` that sets `SECRET_KEY` from `settings.WEB_SECRET_KEY`, enables SQLite WAL mode via `@event.listens_for(engine, "connect")`, registers all blueprints using **deferred imports inside `create_app()`** (standard Flask pattern — `from running_coach_ai.web.api.dashboard import bp as dashboard_bp` etc. inside the function body, not at module level, so blueprint modules that don't exist yet during Phase 2 development don't cause ImportError; each blueprint registration line should include a `# added by T019 / T024 / T028 / T035 / T039 / T048` comment), and on startup idempotently sets `is_admin=True` for the `Athlete` row whose `slack_user_id` matches `settings.ADMIN_SLACK_USER_ID` (skip gracefully if no matching athlete row is found — e.g., fresh DB with no athletes yet)
- [X] T011 Create `running_coach_ai/web/auth.py`: `POST /auth/login` (check `web_username`/`web_password_hash` with werkzeug, set `session["athlete_id"]`), `POST /auth/logout` (clear session), `login_required` decorator (returns 401 JSON if no session), `admin_required` decorator (returns 403 JSON if not `is_admin`), `GET /` redirect, `GET /app` serve `app.html`, `GET /login` serve `login.html`
- [X] T012 Create `running_coach_ai/web/templates/login.html`: plain HTML form (no React) with username/password fields, `POST /auth/login` action, error message display, minimal warm-palette CSS matching the design
- [X] T013 Create `running_coach_ai/web/events.py`: `WebEventHandler` (subclass of `logging.Handler`) that writes log records to `WebEvent` table via a new DB session; `web_event(severity, category, message, athlete_id=None, details=None)` convenience helper; `cleanup_old_events(db_session)` deletes rows older than 30 days; attach handler to `running_coach_ai.web` logger namespace
- [X] T014 Create `scripts/set_web_credentials.py`: CLI script accepting `--slack-id`, `--username`, `--password`, `--admin` flags; hashes password with werkzeug; updates `Athlete` row; prints confirmation — used to seed the first admin account
- [X] T015 [P] Write unit tests for `Athlete` web credential fields, `RunFeedback` model validation (feel_score 0–4, rpe 1–10), `WeeklyReviewSummary` unique constraint, and `WebEvent` model in `tests/unit/web/test_models.py`
- [X] T016 [P] Write unit tests for `auth.py` — login success, login failure (wrong password), login failure (unknown user), logout, `login_required` blocks unauthenticated, `admin_required` blocks non-admin in `tests/unit/web/test_auth.py`
- [X] T017 Write integration test for full auth flow: seed athlete with web credentials → POST /auth/login → assert session cookie set → GET /api/dashboard (protected) → POST /auth/logout → GET /api/dashboard returns 401 in `tests/integration/web/test_auth_flow.py`

**Checkpoint**: Migrations applied, Flask app starts, login/logout work, WebEvent handler active — user story implementation can begin

---

## Phase 3: User Story 1 — View Daily Readiness & Today's Workout (Priority: P1) 🎯 MVP

**Goal**: Athlete loads Dashboard to see morning readiness metrics (HRV, Body Battery, Sleep, RHR with trends), today's workout (expandable), coach morning message, weekly plan strip, and 8-week charts.

**Independent Test**: `GET /api/dashboard` returns correct metrics for a seeded athlete; the React Dashboard screen renders all metric cards, the workout card expands on click, and the week strip shows all 7 days.

### Tests for User Story 1

- [X] T018 [P] [US1] Write unit tests for `GET /api/dashboard` covering: authenticated response shape, health trend calculation (current vs 7-day avg), today's workout selection and imperial conversion, coach message content per `coach_key`, week strip completeness, 404 when no health data for today in `tests/unit/web/test_dashboard_api.py`

### Implementation for User Story 1

- [X] T019 [US1] **Pre-step**: add `greeting: str` field to each `CoachPersona` dataclass in `running_coach_ai/coach/personas.py` — a short, natural-language morning greeting sentence for each coach (e.g., Alex: "Morning. Let's see how the data looks today.", Maya: "Good morning! Consistency is everything — let's check in.", Jordan: "Hey! Let's see how your body is holding up."). Then create `running_coach_ai/web/api/dashboard.py` Blueprint: `GET /api/dashboard` — query `HealthSnapshot` for today + 7-day window for trends; query `PlannedWorkout` for today; compute days-to-race from active `Goal.race_date`; use `PERSONAS[athlete.coach_key].greeting` for the coach message; build week strip from Mon–Sun `PlannedWorkout` rows; build 8-week volume and HRV arrays from `HealthSnapshot`; convert all distances/paces to imperial using `format_miles()`/`format_pace_mi()`; return JSON per `contracts/web-api.md`
- [X] T020 [US1] Create `running_coach_ai/web/static/app.html`: React 18 CDN + Babel standalone SPA shell; `fetch("/api/dashboard")` on load; `ThemeContext` with light/dark palette (`THEMES`) and three accent colors (`ACCENTS`); `Sidebar` component (nav links, coach switcher, athlete footer, dark/light toggle); `App` root with screen router covering all 6 screens — include a placeholder `Onboarding` route entry that renders `null` (to be replaced by T052) so navigating to it before Phase 10 does not crash the SPA; persist `{coach, mode, accent}` to `localStorage`; redirect to `/login` on 401
- [X] T021 [US1] Add `Dashboard` React component to `app.html`: header with athlete name + days-to-race countdown; coach message card (persona avatar + greeting text); 4-column metric row (`MetricCard` with trend arrows); expandable today-workout card (click to reveal target stats grid + coach notes); 2-column charts section (`BarChart` weekly volume, `LineChart` HRV trend); 7-day week strip (Mon–Sun, today highlighted, done checkmarks)
- [X] T022 [US1] Add `LineChart`, `BarChart`, `MetricCard` React primitives to `app.html`: animated SVG charts using `strokeDasharray` animation for line, `scaleY` animation for bars; warm natural color palette; responsive to card width

**Checkpoint**: `python web.py` → visit `http://localhost:8080` → login → see live Dashboard with real DB data

---

## Phase 4: User Story 2 — Browse Training Calendar (Priority: P2)

**Goal**: Athlete views monthly calendar with Monday-first weeks, rich day cells (distance, duration, pace, TSS, intensity bar), completed-day actual pace + zone bar, month navigation, day detail panel, and Garmin sync button.

**Independent Test**: `GET /api/plan?month=2026-04` returns correct week structure; `POST /api/plan/sync` triggers resync and returns success/error; the React TrainingPlan screen renders the calendar grid, month arrows work, clicking a day opens the detail panel.

### Tests for User Story 2

- [X] T023 [P] [US2] Write unit tests for `GET /api/plan` covering: week grouping (Monday-first), `active_week_index` and `today_col` calculation, completed vs upcoming cell differentiation, imperial conversion of pace/distance, month boundary handling (first week spans two months), `POST /api/plan/sync` success path and Garmin error path in `tests/unit/web/test_plan_api.py`

### Implementation for User Story 2

- [X] T024 [US2] Create `running_coach_ai/web/api/plan.py` Blueprint: `GET /api/plan?month=YYYY-MM` — query `PlannedWorkout` rows for the month scoped by `athlete_id` from `session["athlete_id"]`; group into Monday-first week rows (null cells for empty days/month boundaries); annotate `done`, `actual_pace`, `actual_zones` from linked `CompletedWorkout`; derive `active_week_index` and `today_col` from today's date; return JSON per contract. `POST /api/plan/sync` — extract `athlete_id` from `session["athlete_id"]`; pass it explicitly to the Garmin sync function (`sync_week_to_garmin(athlete, db_session)` — verify the sync path uses `get_garmin_client(athlete_id)` so the correct athlete's session files are used); catch `GarminConnectAuthenticationError` and other exceptions; return `{"ok": true, "synced_count": N}` or `{"ok": false, "error": "..."}` per contract; log sync attempt to `WebEvent` via `web_event(athlete_id=athlete_id, ...)`
- [X] T025 [US2] Add `TrainingPlan` React component to `app.html`: month navigation arrows (`‹` / `›`) updating `month` query param; day-header row (Mon–Sun labels); week rows with `56px` week-label column; rich day cells (type emoji, distance, name, duration, pace, TSS, intensity bar for upcoming, actual pace + micro-zone bar for done); selected day highlighted; Garmin sync button with loading spinner, success state (green "✓ Synced"), and error state (red reason text + retry)
- [X] T026 [US2] Add `IntensityBar` and `MicroZoneBar` React primitives to `app.html` used by calendar cells; add workout detail side panel (290px right panel) showing selected day stats grid and coach notes lookup by workout type

**Checkpoint**: Training Plan screen renders real DB data; month navigation works; Garmin sync button works

---

## Phase 5: User Story 3 — Activity Feed with Biomechanics (Priority: P2)

**Goal**: Athlete sees recent completed runs with expandable biomechanics, HR zones, coach analysis, flagged observations, and can submit/persist feel/RPE/notes feedback.

**Independent Test**: `GET /api/activities` returns activity list with correct biomechanics data and any saved feedback; `POST /api/activities/{id}/feedback` persists and returns 200; re-fetching shows pre-populated feedback; the React ActivityFeed screen expands cards, shows warning vs ok flags, and the Save button shows "✓ Saved".

### Tests for User Story 3

- [X] T027 [P] [US3] Write unit tests for `GET /api/activities` covering: pagination (`limit`/`offset`), imperial conversion, biomechanics null handling (missing power meter), `coach_analysis` present when `CompletedWorkout.coach_analysis` is populated and omitted when null, `feedback` block present when `RunFeedback` row exists, `feel_score`/`rpe` validation on `POST /api/activities/{id}/feedback`, 404 on unknown activity ID, 403 on cross-athlete activity access in `tests/unit/web/test_activities_api.py`

### Implementation for User Story 3

- [X] T028 [US3] Create `running_coach_ai/web/api/activities.py` Blueprint: `GET /api/activities?limit=20&offset=0` — query `CompletedWorkout` with `athlete_id` scope, join `WorkoutTelemetry` for HR zone data, join `RunFeedback` (left outer join), compute 7-day avg pace and weekly/YTD totals from `CompletedWorkout`, derive `flags` list from `CompletedWorkout` biomechanics fields vs `RunningProfile` baseline (flag heel-strike proxy via GCT deviation, cadence drop), include `coach_analysis` from `CompletedWorkout.coach_analysis` (omit key if null), convert all values to imperial, return JSON per contract. `POST /api/activities/{id}/feedback` — validate `feel_score` 0–4, `rpe` 1–10, `notes` ≤1000 chars; upsert `RunFeedback` row (insert or update if exists); return `{"ok": true}`
- [X] T027b [P] [US3] Write unit test for activity poll `coach_analysis` persistence in `tests/unit/scheduler/test_jobs.py`: seed a `CompletedWorkout` row with `coach_analysis=None`; mock the Claude API call to return a known analysis string; run the activity poll processing logic; assert `CompletedWorkout.coach_analysis` is updated to the mocked string in the DB session.
- [X] T028b [US3] Update `running_coach_ai/scheduler/jobs.py` activity poll job: after Claude generates the post-run analysis text for the Slack feedback DM, also persist that text to `CompletedWorkout.coach_analysis` for the relevant `CompletedWorkout` row (update the row in the same DB session used by the poll job). Historical activities already in the DB retain `null`; only new activities processed after this migration receive a stored `coach_analysis`.
- [X] T029 [US3] Add `ActivityFeed` React component to `app.html`: summary metric row (4 cards); activity list with collapsed cards (distance, time, pace, avg HR, compact zone bar); expand/collapse on click; expanded section: 2-column biomechanics grid + HR zone bar with percentages + post-run HRV; coach analysis text block; flags list with `⚠` (warning) vs `✓` (ok) icons
- [X] T030 [US3] Add `RunnerFeedback` React component to `app.html` (inside expanded activity card): 5-emoji feel selector (Dead→Strong); 1–10 RPE color-coded button row (green→red); free-text notes textarea; Save button POSTing to `/api/activities/{id}/feedback`; "✓ Saved" badge on success; pre-populate values from `activity.feedback` if present

**Checkpoint**: Activity Feed shows real runs, biomechanics, coach analysis; feedback saves and persists across page reloads

---

## Phase 6: User Story 4 — Chat with Coach Persona (Priority: P3)

**Goal**: Athlete sends messages via web Coach Chat; responses use the selected coach persona and athlete training context; history is shared with the Slack conversation thread; typing indicator shows while response is in-flight.

**Independent Test**: `POST /api/chat/message` returns a Claude response within 15s; `GET /api/chat/history` returns unified Slack + web messages; switching coach persona changes the response voice; the React CoachChat screen shows the typing indicator and appends responses in real time.

### Tests for User Story 4

- [X] T031 [P] [US4] Write unit tests for `GET /api/chat/history` (returns sorted `ConversationMessage` rows with `source` field); `POST /api/chat/message` (mocked Claude call returns response, message appended to `ConversationMessage`, XML side effects processed); Claude error → 503 response; message appended with `source="web"` in `tests/unit/web/test_chat_api.py`
- [X] T032 [P] [US4] Write integration test: seed athlete and conversation history → POST /api/chat/message with mocked Claude → assert `ConversationMessage` rows created for user and assistant → GET /api/chat/history returns both in `tests/integration/web/test_chat_round_trip.py`

### Implementation for User Story 4

- [X] T033 [US4] Refactor `running_coach_ai/slack/conversation.py`: extract `process_message(athlete: Athlete, user_text: str, db_session: Session, source: str = "slack", coach_key: str | None = None) -> str` pure function that loads system prompt using `coach_key or athlete.coach_key`, appends user `ConversationMessage` with the given `source`, calls Claude, processes XML side effects (`<plan>`, `<remember>`, `<garmin_sync/>`), appends assistant `ConversationMessage`, returns cleaned response text. Add `source` (Text nullable) column to `ConversationMessage` model (T034 handles the migration). Update `handle_message()` to be a thin wrapper calling `process_message(source="slack")` then posting to Slack.
- [X] T034 [US4] Create Alembic migration `add_source_to_conversation_messages`: adds `source` (Text nullable) to `conversation_messages` in `running_coach_ai/database/migrations/versions/`
- [X] T035 [US4] Create `running_coach_ai/web/api/chat.py` Blueprint: `GET /api/chat/history` — query all `ConversationMessage` rows for athlete ordered by `created_at`, map `role` (user/assistant) and `source` (slack/web/null→"slack"), return JSON. `POST /api/chat/message` — validate message ≤2000 chars; extract optional `coach_key` from request body (validate against `{"alex","maya","jordan"}`; fall back to `athlete.coach_key` if absent or invalid); call `process_message(source="web", coach_key=coach_key)`; catch `anthropic.APIError` → return 503; log Claude call latency to `WebEvent`; return `{"response": "...", "timestamp": "..."}`
- [X] T036 [US4] Add `CoachChat` React component to `app.html`: header with coach avatar, name, online pulse indicator, style tag; scrollable message list (user messages right-aligned, coach messages left-aligned with avatar, each with timestamp); typing indicator (3 animated dots) while request in-flight; 3 quick-reply chips with text: "How should I pace today's run?", "Am I recovered enough to train hard?", "What should I focus on this week?"; input bar with send button (enabled only when non-empty and not loading); `fetch("/api/chat/message")` on send; `fetch("/api/chat/history")` on mount; auto-scroll to bottom on new message

**Checkpoint**: Coach Chat sends/receives messages; history loads with Slack messages visible; coach switch changes response voice

---

## Phase 7: User Story 5 — Weekly Review Summary (Priority: P3)

**Goal**: Athlete views auto-generated weekly summary with aggregate metrics, daily volume chart, body battery trend, AI narrative, and next-week plan preview. Summary is written by the Sunday scheduler job.

**Independent Test**: After running the weekly review job (or seeding a `WeeklyReviewSummary` row), `GET /api/review` returns the correct data; 404 is returned when no review exists; the React WeeklyReview screen renders all metric cards, charts, narrative, and next-week strip.

### Tests for User Story 5

- [X] T037 [P] [US5] Write unit tests for `GET /api/review`: seeded `WeeklyReviewSummary` row returns correct shape; 404 when no row; `weekly_review_summaries` upsert logic (same `week_start_date` updates existing row) in `tests/unit/web/test_review_api.py`

### Implementation for User Story 5

- [X] T038 [US5] Update `running_coach_ai/scheduler/jobs.py` weekly review job: after generating the Slack narrative and sending the DM, upsert a `WeeklyReviewSummary` row (`week_start_date` = Monday of reviewed week; `narrative`, `total_miles`, `elevation_gain_ft`, `avg_hrv`, `total_tss`, `daily_volume_json`, `body_battery_json`, `next_week_json` populated from the same aggregates already computed); use `from sqlalchemy.dialects.sqlite import insert` (not the generic `sqlalchemy.insert`) with `.on_conflict_do_update(index_elements=["athlete_id", "week_start_date"], set_={"narrative": ..., "total_miles": ..., ...})` — the generic insert does not support `on_conflict_do_update` on SQLite
- [X] T039 [US5] Create `running_coach_ai/web/api/review.py` Blueprint: `GET /api/review` — query most recent `WeeklyReviewSummary` row for athlete; return 404 with next-generation message if none; return JSON per contract including all metric fields, `daily_volume`, `body_battery_8w`, `next_week` array
- [X] T040 [US5] Add `WeeklyReview` React component to `app.html`: header with week label and "Auto-synced ✓" badge; 4-column metric row; 2-column charts section (`BarChart` daily volume, `LineChart` body battery); coach weekly summary card (4 status indicators: Aerobic Load / Intensity / Recovery / Race Readiness + narrative text with highlighted key phrases); next-week preview strip (7-day row with workout type dots and labels)

**Checkpoint**: Weekly Review screen renders real `WeeklyReviewSummary` data; Sunday job persists the review to DB

---

## Phase 8: User Story 6 — Coach & Theme Switcher (Priority: P4)

**Goal**: Sidebar coach switcher (Alex / Maya / Jordan) updates greeting, chat context, and accent color globally. Light/dark mode toggle. Three accent colors (Sage, Terracotta, Stone). All preferences persist to localStorage.

**Independent Test**: Switching coach updates Dashboard greeting and CoachChat header within 100ms (no API call). Toggling dark mode switches background/text colors within 200ms. Preferences survive page refresh.

### Tests for User Story 6

- [X] T040b [P] [US6] Validate theme and coach constants in `tests/unit/web/test_theme_constants.py`: read `running_coach_ai/web/static/app.html` as a string and assert `THEMES` object contains both `"light"` and `"dark"` keys with required color properties (`bg`, `surface`, `text`, `border`); assert `ACCENTS` object contains `"sage"`, `"terracotta"`, `"stone"` keys each with a `"main"` color value; assert the `localStorage` keys `"coach"`, `"mode"`, `"accent"` are all referenced in the JS (string search). Note: full JS unit tests require a browser runtime; this Python-level assertion provides constitution-compliant structural coverage for a CDN-only React app where no JS test runner is available (see research.md decision #2).

### Implementation for User Story 6

- [X] T041 [US6] Wire coach switcher in `Sidebar` component in `app.html`: three coach cards with avatar, name, style tag; selected state highlighted with coach color; `onClick` updates `coach` state in `App`; propagated via React context to `Dashboard` (greeting) and `CoachChat` (header); no API call on switch — but next `POST /api/chat/message` includes `{ "message": "...", "coach_key": coach }` in the request body so the backend uses the currently selected persona for that message (ephemeral; see contracts/web-api.md)
- [X] T042 [US6] Wire light/dark mode toggle in `Sidebar` in `app.html`: moon/sun icon button in sidebar header; toggles `mode` state in `App` (persisted to `localStorage`); `ThemeContext` provides `{t, ac, mode}` to all components; all background, text, border, and surface colors driven by `THEMES[mode]` constants (no hardcoded colors in components)
- [X] T043 [US6] Add accent color switcher to `TweaksPanel` (hidden panel, toggled by toolbar button) in `app.html`: three color swatches (Sage #5a8a62, Terracotta #b8673e, Stone #5e7e96); `onClick` updates `accent` state; `ACCENTS[accent].main` used for all primary-colored elements; `TweaksPanel` also exposes mode toggle as radio buttons
- [X] T044 [US6] Verify `localStorage` round-trip in `App`: on mount, read `{coach, mode, accent}` from `localStorage`; write on every state change; confirm that all defaults are Sage / light / alex if no saved value exists

**Checkpoint**: All theme/coach preferences survive page refresh; accent color updates propagate to all 6 screens

---

## Phase 9: User Story 7 — Admin Observability Screen (Priority: P4)

**Goal**: Admin users see a filterable, paginated event feed (auth/garmin/claude/scheduler/http categories) in the web UI. All significant server events are logged to both `WebEvent` DB table and stdout.

**Independent Test**: Triggering a Garmin sync failure creates a `WebEvent` row with `category="garmin"` and `severity="error"`; `GET /api/admin/events` returns it; non-admin user gets 403; the React Admin screen renders the event feed, category filter works, pagination works.

### Tests for User Story 7

- [X] T045 [P] [US7] Write unit tests for `GET /api/admin/events`: admin user sees events; non-admin gets 403; `category` filter returns only matching rows; `severity` filter works; pagination (`limit`/`offset`) correct; events older than 30 days excluded after cleanup in `tests/unit/web/test_admin_api.py`
- [X] T046 [P] [US7] Write unit tests for `WebEventHandler` and `web_event()` helper: handler writes to DB on `emit()`; `cleanup_old_events()` deletes rows older than 30 days; auth events include username and masked IP; `details_json` serializes correctly in `tests/unit/web/test_web_events.py`

### Implementation for User Story 7

- [X] T047 [US7] Attach `WebEventHandler` to key loggers in `running_coach_ai/web/app.py`: `running_coach_ai.web` (HTTP), `running_coach_ai.garmin` (Garmin events), and the Claude call logger — **verify the exact logger name** by checking `getLogger(__name__)` in `running_coach_ai/coach/personas.py` before implementation; the name will be `running_coach_ai.coach.personas` (plural, matching the filename) not `running_coach_ai.coach.persona`; add `web_event()` calls in `auth.py` for login success/failure (include username, no password, remote IP) and in `plan.py` for Garmin sync outcomes
- [X] T048 [US7] Create `running_coach_ai/web/api/admin.py` Blueprint: `GET /api/admin/events?category=all&severity=all&limit=50&offset=0` — guarded by `admin_required` decorator; call `cleanup_old_events()` before query (lazy cleanup); query `WebEvent` with optional `category` and `severity` filters; join athlete name via `athlete_id`; return paginated JSON per contract
- [X] T049 [US7] Add `Admin` screen to `app.html` nav (visible only when `athlete.is_admin === true`): event feed table with columns (timestamp, severity badge, category pill, message, athlete name); color-coded severity (info=muted, warn=terracotta, error=red); category filter tab bar (All / Auth / Garmin / Claude / Scheduler / HTTP); "Load more" pagination button; refresh button; empty state when no events match filter
- [X] T050 [US7] Add Flask `@app.after_request` hook in `running_coach_ai/web/app.py` that logs HTTP 5xx responses to `WebEvent` with `category="http"`, `severity="error"`, route path, status code, and exception type in `details_json`

**Checkpoint**: Admin screen shows real events; filter works; non-admin users cannot access `/api/admin/events`

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Docker deployment, Onboarding screen, CLAUDE.md update, end-to-end validation

- [X] T051 Add `web` service to `docker-compose.yml`: first verify whether the existing `coach` service uses `build: .` (local `Dockerfile`) or a registry `image:` — if local, reuse it with `build: .` and `command: python web.py`; if registry image, use the same `image:` with the overridden command; set `env_file: .env`; environment overrides for `DB_PATH=/data/coach.db`, `GARMIN_SESSION_DIR=/data/garmin_sessions/`, `LOG_FILE=/data/web.log`; shared `./data:/data` volume; `ports: ["8080:8080"]`; `depends_on: [coach]`
- [X] T052 [P] Add `Onboarding` React screen to `app.html` (visual demo, no real data writes): 7-step flow matching the design (welcome → name → goal → experience → mileage → coach select → Garmin email → done); progress bar; coach selection cards; "Restart demo" button; clearly labeled as a demo in the UI (does not replace Slack onboarding)
- [X] T053 [P] Add empty-state components to all 6 main screens in `app.html`: Dashboard (no health data today), Activity Feed (no activities yet), Training Plan (no plan), Chat (first message prompt), Weekly Review (no review yet), Admin (no events)
- [X] T054 [P] Add error boundary to `App` component in `app.html`: catches React render errors; displays a "Something went wrong — reload page" card without crashing the whole UI
- [X] T055 Update `CLAUDE.md` Architecture section: add Web Dashboard subsection documenting Flask entry point (`web.py`), WAL mode requirement, `process_message()` refactor, `WebEvent` dual-sink logging, and new env vars (`WEB_SECRET_KEY`, `WEB_PORT`)
- [X] T056 Run `alembic upgrade head` and `python web.py`, validate against `quickstart.md` checklist: login works, all 6 screens load, Garmin sync button responds, chat round-trip completes, admin event feed populates; open browser DevTools Network tab and verify SC-001 (Dashboard first-load under 2s), SC-003 (Training Plan calendar load under 3s), SC-008 (layout intact at 1280px viewport); verify US6: coach switcher updates greeting within 100ms, dark/light toggle applies in under 200ms, all three accent colors propagate, preferences survive a hard reload (Ctrl+Shift+R)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion — **BLOCKS all user stories**
- **US1 Dashboard (Phase 3)**: Depends on Phase 2 — no other story dependency
- **US2 Training Calendar (Phase 4)**: Depends on Phase 2 — independent of US1
- **US3 Activity Feed (Phase 5)**: Depends on Phase 2 — independent of US1/US2
- **US4 Coach Chat (Phase 6)**: Depends on Phase 2 — requires `process_message()` refactor (T033)
- **US5 Weekly Review (Phase 7)**: Depends on Phase 2 — independent of US1–US4
- **US6 Theme Switcher (Phase 8)**: Depends on Phase 3 (needs `App` shell from T020) — purely frontend wiring
- **US7 Admin (Phase 9)**: Depends on Phase 2 (WebEvent handler from T013)
- **Polish (Phase 10)**: Depends on all desired user stories complete

### User Story Dependencies

- **US1 (P1)**: Can start immediately after Foundational — no other story dependency
- **US2 (P2)**: Can start after Foundational — independent of US1 (separate Blueprint + React component)
- **US3 (P2)**: Can start after Foundational — independent of US1/US2
- **US4 (P3)**: Can start after Foundational — requires T033 (conversation.py refactor) before T035
- **US5 (P3)**: Can start after Foundational — independent of US1–US4
- **US6 (P4)**: Can start after T020 (`app.html` React shell exists) — no new API endpoints
- **US7 (P4)**: Can start after T013 (`WebEventHandler` exists) — no dependency on US1–US6

### Within Each User Story

- Unit tests FIRST (write and verify they fail)
- Models/migrations before services
- API Blueprint before React component
- React component after API endpoint exists (to test against real data)

### Parallel Opportunities

Within Phase 2: T007, T008, T009 can run in parallel (separate migration files). T015, T016 can run in parallel (separate test files).

Within US1: T018 (tests) can run parallel to T019 (API). T021 and T022 can run parallel to T019 (separate React work).

US2, US3, US5 can run in parallel after Phase 2 completes (separate Blueprints and React components, no shared state).

US6 (T041–T044) and US7 (T045–T050) can run in parallel after their respective prerequisites.

---

## Parallel Example: User Story 3

```bash
# After Phase 2 completes, launch simultaneously:
Task T027:  "Write unit tests for /api/activities in tests/unit/web/test_activities_api.py"
Task T027b: "Write unit test for coach_analysis persistence in tests/unit/scheduler/test_jobs.py"
# Then:
Task T028:  "Create running_coach_ai/web/api/activities.py Blueprint"
Task T028b: "Update scheduler/jobs.py activity poll to persist coach_analysis"  # parallel with T028
Task T029:  "Add ActivityFeed React component to app.html"   # parallel with T028
Task T030:  "Add RunnerFeedback React component to app.html"  # parallel with T028
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: US1 Dashboard (T018–T022)
4. **STOP and VALIDATE**: Login → Dashboard shows live health metrics, today's workout, coach message, week strip
5. Deploy with `docker-compose up -d web`

### Incremental Delivery

1. Setup + Foundational → Flask app starts, auth works
2. US1 Dashboard → Athletes can check daily readiness (MVP)
3. US2 Training Calendar → Athletes can browse their full plan
4. US3 Activity Feed → Athletes can review runs and submit feedback
5. US4 Coach Chat → Athletes can chat with coach on the web
6. US5 Weekly Review → Athletes can review the auto-generated weekly summary
7. US6 Theme Switcher → Polish: coach switching, dark mode, accent colors
8. US7 Admin → Admin can monitor system events and diagnose issues

---

## Notes

- All API endpoints enforce `athlete_id` from session — never from query parameters
- `process_message()` refactor (T033) must be tested with existing Slack conversation tests passing before and after
- SQLite WAL mode set in `app.py` on every connection — idempotent, harmless if already set by bot process
- React SPA in `app.html` uses CDN React 18 + Babel standalone — no build step, no Node.js required
- Commit after each checkpoint (end of each phase) at minimum
