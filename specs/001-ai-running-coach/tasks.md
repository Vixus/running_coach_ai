# Tasks: AI Running Coach

**Feature Branch**: `001-ai-running-coach`
**Input**: `specs/001-ai-running-coach/` — plan.md, spec.md, data-model.md, contracts/, research.md
**Tests**: Per constitution Principle VII, all features MUST have corresponding unit tests. Test tasks are included in the Remediation: Gaps section (T054, T056, T058, T060, T074, T075) and within relevant phases.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story label ([US1]–[US6])
- Exact file paths included in all task descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project directory structure, dependencies, and container configuration.

- [x] T001 Create project directory structure per plan.md (`running_coach_ai/` with `garmin/`, `coach/`, `slack/`, `scheduler/`, `weather/`, `database/` subdirectories and `__init__.py` files)
- [x] T002 [P] Create `requirements.txt` with all dependencies: `anthropic`, `python-garminconnect`, `garth`, `slack-bolt`, `slack-sdk`, `apscheduler`, `sqlalchemy`, `alembic`, `cryptography`, `pydantic-settings`, `requests`
- [x] T003 [P] Create `.env.example` with all required variables per quickstart.md: `ANTHROPIC_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_APP_TOKEN`, `DB_PATH`, `GARMIN_SESSION_DIR`, `ENCRYPTION_KEY`, `ALLOWED_SLACK_USER_IDS`, `ADMIN_SLACK_USER_ID`, `LOG_LEVEL`
- [x] T004 [P] Create `Dockerfile` — Python 3.11-slim base, install requirements, set working directory, entrypoint `python main.py`
- [x] T005 [P] Create `docker-compose.yml` — single `coach` service, `env_file: .env`, `restart: unless-stopped`, volume mount `./data:/data`, no exposed ports

**Checkpoint**: Project skeleton ready for foundational implementation.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure all user stories depend on. No story work begins until this phase is complete.

**⚠️ CRITICAL**: Blocks all user story phases.

- [x] T006 Create all 9 SQLAlchemy models in `running_coach_ai/database/models.py`: `Athlete` (with `onboarding_step`, `allowed`, `slack_dm_channel_id`, `timezone`), `Goal`, `TrainingPlan`, `PlannedWorkout`, `CompletedWorkout`, `WorkoutTelemetry`, `RunningProfile`, `HealthSnapshot`, `ConversationMessage`, `CoachMemory` — include all fields, types, indexes, and foreign keys per data-model.md
- [x] T007 Create DB session factory in `running_coach_ai/database/session.py` — `sessionmaker` factory, `scoped_session` for thread safety, context manager helper; session per operation pattern
- [x] T008 Initialize Alembic in project root, configure `alembic.ini` and `env.py` to use `DB_PATH` from config, generate initial migration creating all tables
- [x] T009 [P] Create Pydantic Settings class in `running_coach_ai/config.py` — load all env vars from `.env`, expose typed settings singleton; include `GARMIN_SESSION_DIR`, `ENCRYPTION_KEY`, `ALLOWED_SLACK_USER_IDS` (parsed to list), `ADMIN_SLACK_USER_ID`
- [x] T010 [P] Configure structured logging in `running_coach_ai/config.py` — `logging.basicConfig` with level from `LOG_LEVEL` env var, format includes timestamp, level, module name
- [x] T011 [P] Implement Fernet encryption helpers in `running_coach_ai/garmin/client.py` — `encrypt_password(plaintext: str) -> bytes` and `decrypt_password(ciphertext: bytes) -> str` using `ENCRYPTION_KEY` from config
- [x] T012 Create `main.py` entry point — instantiate Slack `App(token=SLACK_BOT_TOKEN)`, `SocketModeHandler`, call `handler.connect()` (non-blocking), instantiate `BlockingScheduler`, register placeholder for jobs, call `scheduler.start()` to block main thread

**Checkpoint**: Foundation complete — database schema, config, logging, encryption, and process skeleton ready.

---

## Phase 3: User Story 1 — Athlete Onboarding (Priority: P1) 🎯 MVP

**Goal**: New athlete completes intake conversation, coach generates a 16–20 week training plan, and week 1 sessions are uploaded to Garmin Connect.

**Independent Test**: Register a new allowed Slack user ID, send a first message, complete all 8 intake questions, confirm profile, verify `TrainingPlan` row created in DB, verify `PlannedWorkout` rows for week 1 have `garmin_workout_id` set.

- [x] T013 [P] [US1] Implement Garmin per-athlete auth with garth session caching in `running_coach_ai/garmin/client.py` — `get_garmin_client(athlete_id, email, encrypted_password)`: load from `GARMIN_SESSION_DIR/{athlete_id}/` via `garmin.garth.load()`, call `garmin.login()`, on auth failure decrypt password and re-authenticate, save updated tokens via `garmin.garth.dump()`
- [x] T014 [P] [US1] Implement Garmin workout JSON upload methods in `running_coach_ai/garmin/client.py` — `upload_workout(athlete_id, workout_json)` → `schedule_workout(athlete_id, workout_id, date)` → return stored IDs; `delete_workout(athlete_id, workout_id)` per garmin-data.md Workout Upload Contract
- [x] T015 [P] [US1] Implement coach persona and static system prompt section in `running_coach_ai/coach/persona.py` — veteran endurance coach persona, training philosophy (polarized, 80/20, periodization, HRV-based load management), injury-avoidance directive
- [x] T016 [P] [US1] Implement training plan generation in `running_coach_ai/coach/planner.py` — `generate_plan(athlete, goal)`: assemble prompt with athlete profile + goal details, call Claude API (`claude-sonnet-4-6`), parse structured plan JSON from response, persist `TrainingPlan` + `PlannedWorkout` rows for all weeks
- [x] T017 [P] [US1] Implement Garmin workout builder in `running_coach_ai/garmin/workout_builder.py` — `build_workout_json(planned_workout)`: construct Garmin Connect workout JSON with `workoutName`, `sport`, `workoutSegments`; for simple sessions (easy/long_run): single step with pace target; for structured sessions (tempo/intervals) with `target_zones_json.steps`: one step per entry using `RepeatGroupDTO`; encode pace targets in seconds-per-metre per research.md
- [x] T018 [US1] Implement onboarding conversation flow in `running_coach_ai/slack/onboarding.py` — 8-question intake sequence: name/age, target race, goal time, current weekly km, training days/week, injuries, city, Garmin credentials; resume from `athlete.onboarding_step` on re-entry; encrypt Garmin password before storing; profile confirmation step before plan generation
- [x] T019 [US1] Implement Slack bot event handler scaffold in `running_coach_ai/slack/bot.py` — `@app.event("message")` handler: guard `event.get("bot_id")` (skip), guard `event.get("subtype")` (skip), guard `channel_type == "im"`, resolve `event.user` → `Athlete`, cache `event.channel` → `athlete.slack_dm_channel_id`; route to `onboarding.handle()` if not `onboarding_complete`, else to conversation handler (stub for now); proactive DM helper `send_dm(athlete, text)` using `conversations_open` + `chat_postMessage`
- [x] T020 [US1] Wire onboarding completion to plan generation and Garmin upload in `running_coach_ai/slack/onboarding.py` — after profile confirmed: call `planner.generate_plan()`, call `workout_builder.build_workout_json()` for each week-1 `PlannedWorkout`, call `garmin_client.upload_workout()` + `schedule_workout()` for each, store `garmin_workout_id` on row, send week-1 summary DM to athlete

**Checkpoint**: New athlete can complete onboarding end-to-end; training plan appears in DB and week 1 is on their Garmin calendar.

---

## Phase 4: User Story 2 — Free-Form Coaching Conversation (Priority: P1)

**Goal**: Onboarded athlete can message the coach in natural language; coach responds with full context, applies plan mutations silently, and persists long-term memories.

**Independent Test**: Send 5 varied messages (logistics change, injury concern, training question, performance reflection, motivation) and verify: coach responds in under 10 seconds; plan mutations update `PlannedWorkout` and Garmin; `<remember>` tags create `CoachMemory` rows.

- [x] T021 [P] [US2] Implement Anthropic SDK wrapper in `running_coach_ai/coach/persona.py` — `call_claude(system_prompt, messages)`: create `anthropic.Anthropic()` client, call `messages.create(model="claude-sonnet-4-6", ...)`, return raw response text; handle API errors with logging
- [x] T022 [P] [US2] Implement system prompt assembly (8 sections) in `running_coach_ai/slack/conversation.py` — `build_system_prompt(athlete, db_session)`: (1) coach persona from `persona.py`, (2) athlete profile block, (3) current plan phase + this week's sessions, (4) today's health snapshot (note if unavailable), (5) last 5 completed workouts, (6) weather today + 3 days, (7) active `CoachMemory` rows as bullet list, (8) last 30 `ConversationMessage` rows; token budget management: reduce conversation history window first (30→20→15), then workout history (5→3→2)
- [x] T023 [US2] Implement `<plan>` tag extraction and plan mutation processing in `running_coach_ai/slack/conversation.py` — `extract_and_apply_plan(athlete_id, claude_response, db_session)`: regex-extract all `<plan>...</plan>` blocks, parse JSON (skip on invalid), for each session find or create `PlannedWorkout` by `(athlete_id, date)`, update fields, if `garmin_workout_id` exists: delete old workout + upload revised, store new IDs; strip all `<plan>` blocks from text before returning
- [x] T024 [US2] Implement `<remember>` tag extraction and `CoachMemory` persistence in `running_coach_ai/slack/conversation.py` — `extract_and_save_memories(athlete_id, claude_response, db_session)`: regex-extract all `<remember>...</remember>` blocks, infer `category` from content keywords (injury/preference/performance_flag), create `CoachMemory` row, skip if identical `content` already exists for athlete; strip all `<remember>` blocks from text before returning
- [x] T025 [US2] Implement main conversation handler in `running_coach_ai/slack/conversation.py` — `handle_message(athlete, text, db_session)`: load conversation history, call `build_system_prompt()`, call `call_claude()`, call `extract_and_apply_plan()` and `extract_and_save_memories()`, persist user + assistant `ConversationMessage` rows, return cleaned reply text
- [x] T026 [US2] Wire conversation handler into `running_coach_ai/slack/bot.py` — route non-onboarding messages to `conversation.handle_message()`, post returned reply via `say()`, handle `@app.event("app_mention")` identically to DM handler

**Checkpoint**: Full coaching conversation works — context-aware responses, plan mutations applied silently, memories persisted.

---

## Phase 5: User Story 3 — Daily Morning Check-In (Priority: P2)

**Goal**: Each athlete receives a personalised morning Slack message by 07:00 local time, with health-based session adaptation and weather context.

**Independent Test**: Set athlete timezone, simulate low HRV + high-intensity planned session, trigger `morning_checkin` job manually, verify session is downgraded in DB + Garmin, and Slack DM received with explanation in coach voice.

- [x] T027 [P] [US3] Implement Garmin health data reads in `running_coach_ai/garmin/client.py` — `get_health_snapshot(athlete_id, date)`: call `get_sleep_data(date)`, `get_hrv_data(date)`, `get_rhr_day(date)`, `get_body_battery(date, date)`, `get_stress_data(date)`, `get_steps_data(date)`, `get_spo2_data(date)`; catch all exceptions per field individually (leave NULL on missing); return raw response dict
- [x] T028 [P] [US3] Implement Garmin health response parser in `running_coach_ai/garmin/parser.py` — `parse_health_snapshot(raw_health, athlete_id, date)`: map fields per garmin-data.md Daily Health Read Contract table; upsert `HealthSnapshot` on `(athlete_id, date)`
- [x] T029 [P] [US3] Implement Open-Meteo weather fetch in `running_coach_ai/weather/client.py` — `get_forecast(lat, lon)`: GET `https://api.open-meteo.com/v1/forecast` with `hourly=temperature_2m,precipitation_probability,windspeed_10m,weathercode,relativehumidity_2m&forecast_days=7&timezone=auto`; `summarise_forecast(response)`: extract today + next 3 days with WMO code → condition string mapping per research.md; return structured dict for prompt injection
- [x] T030 [US3] Implement morning check-in logic in `running_coach_ai/coach/adapter.py` — `run_morning_checkin(athlete, db_session)`: fetch health snapshot + parse, fetch weather, get today's `PlannedWorkout`, assemble Claude prompt with health + weather + today's session, call Claude to evaluate + adapt, apply any `<plan>` mutations, send Slack DM via `bot.send_dm()`; if entire health read fails note data unavailability and proceed
- [x] T031 [US3] Register `morning_checkin` APScheduler job in `running_coach_ai/scheduler/jobs.py` — one `CronTrigger(hour=7, minute=0, timezone=athlete.timezone)` per onboarded athlete; `misfire_grace_time=300`; iterate all active athletes; wrap each in `try/except` with logging; register jobs in `main.py` after scheduler creation

**Iteration 2026-04-04: Morning Check-In Health-Gated Polling** — T030 and T031 above are superseded by T076–T078 below.

- [x] T076 [US3] Add `last_morning_checkin_date` column (DATE, NULLABLE) to `Athlete` model in `running_coach_ai/database/models.py` and generate Alembic migration for the new field.
- [x] T077 [P] [US3] Modify `run_morning_checkin` in `running_coach_ai/coach/adapter.py` — add health-data gate: (1) if `athlete.last_morning_checkin_date == today`, return immediately (no-op); (2) parse health snapshot; (3) if all of `sleep_score`, `hrv_score`, `body_battery_start` are None and athlete local time is before 10:00am, return silently to allow next tick to retry; (4) if all None and athlete local time is 10:00am or later, log INFO skip and return without sending; (5) on successful DM send, set `athlete.last_morning_checkin_date = today` and commit.
- [x] T078 [P] [US3] Modify `register_jobs` and `register_athlete_morning_job` in `running_coach_ai/scheduler/jobs.py` — replace `CronTrigger(hour=7, minute=0, timezone=tz)` with `IntervalTrigger(minutes=30, start_date=<next 07:00 in athlete tz>)`; `misfire_grace_time=300`; deduplication is handled inside the adapter (T077), not at the job level.
- [x] T079 [P] [US3] Write unit tests in `tests/unit/test_morning_checkin_polling.py` covering: (a) health data present on first tick → DM sent; (b) all health fields None before 10:00am → no send, silent return; (c) all health fields None at/after 10:00am → no send, INFO log emitted; (d) `last_morning_checkin_date == today` → immediate no-op with no Garmin call.
- [x] T080 [P] [US3] Write unit tests in `tests/unit/test_morning_checkin_dedup.py` covering: athlete receives exactly one DM even when two poller ticks fire in quick succession with health data present on both ticks — second tick must be a DB-guarded no-op.

**Checkpoint**: Morning check-in runs autonomously; athletes receive personalised, data-driven morning messages only after health data is confirmed present, with a hard 10:00am cutoff and same-day deduplication guard.

---

## Phase 6: User Story 4 — Post-Run Feedback (Priority: P2)

**Goal**: New activity auto-detected within 30 min, full telemetry ingested, biomechanics analysed, targeted feedback sent to athlete within 10 minutes of detection.

**Independent Test**: Simulate new Garmin activity for registered athlete, trigger `activity_poll` job, verify `CompletedWorkout` + `WorkoutTelemetry` rows created, `RunningProfile` updated, Slack DM received with biomechanical observations and specific numbers.

- [x] T032 [P] [US4] Implement Garmin activity polling with deduplication in `running_coach_ai/garmin/client.py` — `poll_new_activities(athlete_id, db_session)`: call `get_activities_by_date(yesterday, today, "running")`, extract `activityId` from each, check `CompletedWorkout.garmin_activity_id` for athlete, return list of new activity IDs only
- [x] T033 [P] [US4] Implement Garmin activity detail parser in `running_coach_ai/garmin/parser.py` — `parse_activity_summary(detail_response, athlete_id)`: map all fields from `get_activity_details()` to `CompletedWorkout` columns per garmin-data.md Activity Detail Read Contract Step 1; convert distance metres→km, speed m/s→min/km pace; persist `CompletedWorkout` row
- [x] T034 [P] [US4] Implement telemetry stream extraction in `running_coach_ai/garmin/telemetry.py` — `extract_telemetry(detail_response, completed_workout_id, athlete_id)`: build `metricsKey → column_index` map from `metricDescriptors`; for each of the 12 recognised stream keys extract column as JSON array; skip missing keys (store NULL); record present keys in `telemetry_channels_json`; persist `WorkoutTelemetry` row
- [x] T035 [P] [US4] Implement lap splits ingestion in `running_coach_ai/garmin/telemetry.py` — `ingest_lap_splits(athlete_id, activity_id, completed_workout)`: call `get_activity_splits(activity_id)`, extract avg/max metrics per lap, store as `laps_json` array on `WorkoutTelemetry` row
- [x] T036 [US4] Implement biomechanics analysis in `running_coach_ai/coach/biomechanics.py` — `analyse_workout(telemetry, completed_workout)`: HR drift % (`avg_hr_last_quarter - avg_hr_first_quarter / avg_hr_first_quarter × 100`), aerobic decoupling (HR:pace ratio first half vs second half), zone compliance (% samples ≤ zone 2 threshold derived from athlete max HR), cadence consistency (CV + lap trend), effort distribution (even/positive/negative split classification); return `BiomechanicsResult` dict
- [x] T037 [US4] Implement `RunningProfile` upsert in `running_coach_ai/coach/biomechanics.py` — `update_running_profile(athlete_id, db_session)`: query last 30 days of `WorkoutTelemetry` grouped by workout type, compute rolling averages for all profile metrics, set trend direction (`improving`/`stable`/`declining`) where ≥3 data points exist; upsert `RunningProfile` row
- [x] T038 [US4] Implement post-run feedback generation in `running_coach_ai/coach/feedback.py` — `generate_post_run_feedback(athlete, completed_workout, biomechanics_result, db_session)`: assemble prompt with planned vs actual summary, biomechanical findings, `RunningProfile` trends; call Claude; extract `<remember>` tags for any persistent patterns; send Slack DM via `bot.send_dm()`; mark `completed_workout.feedback_given = True`
- [x] T039 [US4] Register `activity_poll` APScheduler job in `running_coach_ai/scheduler/jobs.py` — `IntervalTrigger(minutes=30)` with `start_date` at next 06:00 and `end_date` logic to suppress runs outside 06:00–22:00 window (check current local time inside job); `misfire_grace_time=60`; iterate all onboarded athletes; wrap in `try/except` with logging

**Checkpoint**: Post-run feedback pipeline fully operational — from activity detection through biomechanical analysis to Slack delivery.

---

## Phase 7: User Story 5 — Weekly Review and Plan Adaptation (Priority: P3)

**Goal**: Sunday evening, each athlete receives a week-in-review summary and next week's adapted plan is uploaded to Garmin.

**Independent Test**: Simulate a week where athlete completed 2 of 5 sessions, trigger `weekly_review` job, verify review message contains accurate planned vs actual stats, verify next week's `PlannedWorkout` rows are adjusted (lower load), and Garmin workouts for next week have new `garmin_workout_id` values.

- [x] T040 [P] [US5] Implement weekly load aggregation in `running_coach_ai/coach/planner.py` — `aggregate_week(athlete_id, week_start_date, db_session)`: query `PlannedWorkout` and `CompletedWorkout` for the week, compute: total km planned vs actual, quality sessions (tempo/intervals) planned vs completed, load trend (training load sum), VO2 max trend, notable health indicators from `HealthSnapshot` rows; return structured summary dict
- [x] T041 [US5] Implement weekly review message generation in `running_coach_ai/coach/feedback.py` — `generate_weekly_review(athlete, week_summary, db_session)`: assemble Claude prompt with week summary + athlete profile + `RunningProfile` trends; call Claude; return coach-voice narrative covering km hit, quality sessions, fatigue trend, next-week preview
- [x] T042 [US5] Implement next-week plan adaptation in `running_coach_ai/coach/adapter.py` — `adapt_next_week(athlete, week_summary, db_session)`: if athlete completed <70% of sessions, moderate upcoming week load; if all sessions completed + positive adaptation signals, allow incremental load increase; emit `<plan>` mutations via `extract_and_apply_plan()` to update `PlannedWorkout` rows for next week
- [x] T043 [US5] Implement next-week Garmin sync in `running_coach_ai/garmin/workout_builder.py` — `sync_week_to_garmin(athlete_id, week_start_date, db_session)`: for each next-week `PlannedWorkout`, if `garmin_workout_id` exists call `delete_workout()`, build updated JSON via `build_workout_json()`, call `upload_workout()` + `schedule_workout()`, store new IDs
- [x] T044 [US5] Register `weekly_review` APScheduler job in `running_coach_ai/scheduler/jobs.py` — `CronTrigger(day_of_week="sun", hour=20, minute=0)`; `misfire_grace_time=3600`; iterate all onboarded athletes; call `aggregate_week()` → `generate_weekly_review()` → `adapt_next_week()` → `sync_week_to_garmin()` → `send_dm()`; wrap in `try/except` with logging

**Checkpoint**: Weekly review runs autonomously every Sunday; next week's plan adapts to actual training load and appears on Garmin.

---

## Phase 8: User Story 6 — Admin User Management (Priority: P3)

**Goal**: Admin can grant and revoke athlete access at runtime via Slack DM commands, with changes effective immediately and no restart required.

**Independent Test**: Send `!admin add U_NEW_USER` from admin Slack ID, verify `Athlete.allowed=True` for that user, send message from new user and verify onboarding initiates. Send `!admin remove U_NEW_USER`, verify `Athlete.allowed=False`, send message from removed user and verify polite decline. Attempt admin command from non-admin ID, verify it is silently ignored.

- [x] T045 [P] [US6] Implement admin command parser in `running_coach_ai/slack/admin.py` — `handle_admin_command(admin_user_id, text, db_session)`: parse `!admin add <user_id>`, `!admin remove <user_id>`, `!admin list` patterns; verify sender is `ADMIN_SLACK_USER_ID` (return silently if not); `add`: upsert `Athlete(slack_user_id=<id>, allowed=True)`, reset `onboarding_step=0` only if no prior data; `remove`: set `athlete.allowed=False`, retain all data; `list`: return formatted list of athletes with name + Slack user ID + allowed status
- [x] T046 [US6] Implement allowed-list enforcement in `running_coach_ai/slack/bot.py` — in DM event handler: after resolving `event.user`, check `athlete.allowed`; if no `Athlete` row or `allowed=False`, reply with polite decline ("This coaching service is private — ask the admin to add you.") and return
- [x] T047 [US6] Wire admin command interceptor into DM handler in `running_coach_ai/slack/bot.py` — before routing to onboarding/conversation: if `event.user == ADMIN_SLACK_USER_ID` and message starts with `!admin`, call `admin.handle_admin_command()` and reply with result; skip all other routing

**Checkpoint**: All 6 user stories implemented. Full system functional end-to-end.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Reliability, observability, and deployment validation across all stories.

- [x] T048 [P] Implement exponential backoff retry decorator in `running_coach_ai/garmin/client.py` — `@retry_garmin(max_attempts=3, base_delay=2)`: on HTTP 429 or auth error, wait `base_delay * 2^attempt` seconds, retry; after 3 failures raise; callers check if operation time window has passed before calling (skip + log if stale per FR-027)
- [x] T049 [P] Add structured log statements to all modules — `garmin/client.py` (auth events, retries, rate limits), `scheduler/jobs.py` (job start/end, skipped operations), `slack/bot.py` (message received, routed), `coach/` modules (Claude calls, plan mutations, memory saves); all log entries include `athlete_id` where applicable
- [x] T050 [P] Add `athlete_id` isolation guard helper in `running_coach_ai/database/session.py` — `scoped_query(db_session, Model, athlete_id)`: returns base query filtered by `athlete_id`; update all existing service calls to use this helper (FR-001 enforcement)
- [x] T051 Wire all scheduler jobs into `main.py` — import `scheduler/jobs.py`, register all 3 job types (`morning_checkin` per athlete, `activity_poll`, `weekly_review`), call `handler.connect()` before `scheduler.start()`; confirm process starts cleanly with `alembic upgrade head` having been run
- [x] T052 [P] Validate full quickstart.md flow — run `alembic upgrade head`, start `python main.py`, register one athlete via `ALLOWED_SLACK_USER_IDS`, complete onboarding via Slack DM, confirm week 1 appears in Garmin; document any deviations in quickstart.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion — **BLOCKS all user story phases**
- **US1 (Phase 3)**: Depends on Phase 2 completion — MVP start
- **US2 (Phase 4)**: Depends on Phase 2; integrates with US1 DB models and Garmin client
- **US3 (Phase 5)**: Depends on Phase 2; uses Garmin client from US1, conversation from US2
- **US4 (Phase 6)**: Depends on Phase 2; uses Garmin client from US1, feedback DM from US2
- **US5 (Phase 7)**: Depends on US1 (plan structure), US4 (completed workout data)
- **US6 (Phase 8)**: Depends on Phase 2 (Athlete model); can be implemented independently of US2–US5
- **Polish (Phase 9)**: Depends on all desired stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start immediately after Phase 2 — foundational; all others depend on its DB models
- **US2 (P1)**: Can start after Phase 2 — uses `Athlete`, `ConversationMessage`, `CoachMemory`, `PlannedWorkout` from Phase 2/US1
- **US3 (P2)**: Can start after Phase 2 — health reads are independent; uses plan adaptation from US2
- **US4 (P2)**: Can start after Phase 2 — Garmin polling independent; biomechanics requires `WorkoutTelemetry` (new in US4)
- **US5 (P3)**: Requires US1 (`PlannedWorkout` structure) and US4 (`CompletedWorkout` data)
- **US6 (P3)**: Only requires `Athlete.allowed` from Phase 2 — fully independent of US1–US5

### Within Each User Story

- Tasks marked [P] within a phase can run in parallel (different files, no shared state)
- Non-[P] tasks must run after their phase's [P] tasks complete
- Final wire-up task in each story depends on all prior story tasks

---

## Parallel Example: Phase 3 (US1 Onboarding)

```
# Launch these together — all independent files:
T013: garmin/client.py — auth + garth session caching
T014: garmin/client.py — workout upload methods  ← same file as T013, run after
T015: coach/persona.py — coach persona + Claude wrapper
T016: coach/planner.py — plan generation
T017: garmin/workout_builder.py — workout JSON builder

# After T013–T017 complete:
T018: slack/onboarding.py — conversation flow
T019: slack/bot.py — event handler scaffold

# After T018–T019 complete:
T020: slack/onboarding.py — wire plan generation + Garmin upload
```

> **Note**: T013 and T014 share `garmin/client.py` — run sequentially.

---

## Implementation Strategy

### MVP First (US1 + US2 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: US1 Onboarding
4. Complete Phase 4: US2 Coaching Conversation
5. **STOP and VALIDATE**: New athlete can onboard and have a full coaching conversation
6. Deploy to Synology NAS via Docker Compose

### Incremental Delivery

1. Setup + Foundational → skeleton ready
2. US1 → athlete can onboard, plan generated, week 1 on Garmin (MVP)
3. US2 → full coaching conversation with plan mutations (core value delivery)
4. US3 + US4 → automated check-ins and post-run feedback (daily engagement loop)
5. US5 → weekly adaptation (multi-month plan stays live)
6. US6 → admin management (multi-athlete operations)
7. Polish → reliability hardening and deployment validation

---

## Summary

| Phase                          | Tasks        | Stories            |
| ------------------------------ | ------------ | ------------------ |
| Phase 1: Setup                 | T001–T005    | —                  |
| Phase 2: Foundational          | T006–T012    | —                  |
| Phase 3: US1 Onboarding        | T013–T020    | US1                |
| Phase 4: US2 Conversation      | T021–T026    | US2                |
| Phase 5: US3 Morning Check-In  | T027–T031    | US3                |
| Phase 6: US4 Post-Run Feedback | T032–T039    | US4                |
| Phase 7: US5 Weekly Review     | T040–T044    | US5                |
| Phase 8: US6 Admin Management  | T045–T047    | US6                |
| Phase 9: Polish                | T048–T052    | —                  |
| **Total**                      | **52 tasks** | **6 user stories** |

**Parallel opportunities**: 24 tasks marked [P] across all phases.
**MVP scope**: Phases 1–4 (T001–T026) — onboarding + coaching conversation.
**Suggested first task**: T001 (create project structure).

---

## Remediation: Gaps (2026-04-04)

- [x] T053 [P] [US4] Reduce activity poll schedule to `IntervalTrigger(minutes=10)` and align active-hours detection semantics in `running_coach_ai/scheduler/jobs.py` [Sync: Gap Report]
- [x] T054 [US4] Add regression tests for SC-002 detection latency and poll cadence in `tests/unit/test_activity_poll_interval.py` [Sync: Gap Report]
- [x] T055 [P] [US3] Apply `@retry_garmin()` to all required Garmin client operations in `running_coach_ai/garmin/client.py` [Sync: Gap Report]
- [x] T056 [US3] Add retry/backoff behavior tests covering 429 and transient auth failures in `tests/unit/test_retry_garmin_decorator.py` [Sync: Gap Report]
- [x] T057 [P] [US3] Implement stale-window guards and explicit "skipped stale operation" logging after retry exhaustion in `running_coach_ai/scheduler/jobs.py` [Sync: Gap Report]
- [x] T058 [US3] Add stale-window skip and log assertion tests for scheduled jobs in `tests/unit/test_scheduler_stale_window_skip.py` [Sync: Gap Report]
- [x] T059 [P] [US5] Implement deterministic multi-goal conflict resolution before plan persistence/sync in `running_coach_ai/coach/planner.py` [Sync: Gap Report]
- [x] T060 [US5] Add overlap and recovery-protection tests for multi-goal plans in `tests/unit/test_multi_goal_conflict_resolution.py` [Sync: Gap Report]
- [x] T061 [P] [US1] Persist and consume `athlete.onboarding_step` for deterministic onboarding resume in `running_coach_ai/slack/onboarding.py` [Sync: Gap Report]
- [x] T062 [US1] Extend onboarding schema and parsing to collect `experience_level` and map to `beginner|intermediate|advanced` in `running_coach_ai/slack/onboarding.py` [Sync: Gap Report]
- [x] T063 [P] [US4] Add trend direction fields for HR drift, HR-pace decoupling, and easy-zone compliance in `running_coach_ai/database/models.py` and `running_coach_ai/coach/biomechanics.py` [Sync: Gap Report]
- [x] T064 [US4] Create Alembic migration for new biomechanical trend columns in `running_coach_ai/database/migrations/versions/` [Sync: Gap Report]
- [x] T065 [US4] Include all key metric trend labels in feedback prompt assembly in `running_coach_ai/coach/feedback.py` [Sync: Gap Report]
- [x] T066 [US2] Enforce Claude timeout and immediate fallback acknowledgement path for SC-011 in `running_coach_ai/coach/persona.py` and `running_coach_ai/slack/conversation.py` [Sync: Gap Report]
- [x] T067 [P] [US2] Migrate athlete-scoped ORM reads to `scoped_query()` helper in `running_coach_ai/slack/conversation.py`, `running_coach_ai/coach/adapter.py`, `running_coach_ai/scheduler/jobs.py`, and `running_coach_ai/slack/onboarding.py` [Sync: Gap Report]
- [x] T068 [US6] Align admin command handling/docs to DM `!admin` surface in `running_coach_ai/slack/admin.py`, `running_coach_ai/slack/bot.py`, `specs/001-ai-running-coach/quickstart.md`, and `specs/001-ai-running-coach/contracts/slack-events.md` [Sync: Gap Report]
- [x] T069 [US2] Add deterministic overtraining risk pre-check thresholds and mandatory referral prompt injection in `running_coach_ai/coach/feedback.py` and `running_coach_ai/coach/adapter.py` [Sync: Gap Report]
- [x] T070 [P] [US1] Add `PlannedWorkout` timestamp columns (`created_at`, `updated_at`, `last_garmin_synced_at`) with defaults/auto-update semantics in `running_coach_ai/database/models.py` [Iteration: 2026-04-04]
- [x] T071 [US1] Create Alembic migration to add `planned_workouts.created_at`, `planned_workouts.updated_at`, and `planned_workouts.last_garmin_synced_at` with safe backfill defaults in `running_coach_ai/database/migrations/versions/` [Iteration: 2026-04-04]
- [x] T072 [US1] Update Garmin sync write paths to set `last_garmin_synced_at` only on successful upload/schedule in `running_coach_ai/slack/onboarding.py`, `running_coach_ai/slack/conversation.py`, and `running_coach_ai/garmin/workout_builder.py` [Iteration: 2026-04-04]
- [x] T073 [US1] Ensure create/mutation paths maintain `created_at`/`updated_at` semantics for `PlannedWorkout` inserts and modifications in `running_coach_ai/coach/planner.py` and `running_coach_ai/slack/conversation.py` [Iteration: 2026-04-04]
- [x] T074 [US1] Add regression tests validating `PlannedWorkout` timestamp behavior for create, update, and successful resync flows in `tests/unit/test_planned_workout_timestamps.py` [Iteration: 2026-04-04]
- [x] T075 [US6] Add unit tests verifying `!admin` prefix routing in `tests/unit/test_admin.py`: assert `!admin add/remove/list` commands route to `handle_admin_command()`, `/admin` prefix is NOT intercepted, and non-admin sender is silently ignored [Sync: Gap Report]

## Remediation: Gaps (2026-04-10)

- [ ] T081 [P] [US6] Write unit tests for `!admin morning-checkin` in `tests/unit/test_admin.py`: (a) no `--force` -> check-in runs, dedup guard respected; (b) `--force` -> `last_morning_checkin_date` cleared, check-in runs regardless; (c) unknown uid -> error message returned; (d) athlete not yet onboarded -> error message; (e) `slack_client=None` -> error message returned without crash. [Sync: Gap Report]
- [ ] T082 [P] [US1] Write unit tests for `workout_name` round-trip in `tests/unit/test_workout_builder.py`: (a) `PlannedWorkout` with `workout_name` set -> `build_workout_json` uses it as `workoutName`; (b) `workout_name = None` -> falls back to default name derived from `workout_type`; (c) `<plan>` JSON tag containing `workout_name` -> value persisted to `PlannedWorkout.workout_name` in `running_coach_ai/slack/conversation.py`. [Sync: Gap Report]
- [ ] T083 [P] [US4] Write unit tests for `activity_type` filtering in `tests/unit/test_activity_type_filter.py`: (a) `parse_activity_summary` maps Garmin `activityType.typeKey` to `CompletedWorkout.activity_type` in `running_coach_ai/garmin/parser.py`; (b) `RUNNING_TYPES` filter in `running_coach_ai/slack/conversation.py` excludes hiking/tennis from running-specific weekly load; (c) null `activity_type` defaults to running-type in filter logic. [Sync: Gap Report]
- [ ] T084 [P] [US4] Write unit tests for `lthr_bpm` derivation in `tests/unit/test_lthr_derivation.py`: (a) Garmin profile contains LTHR -> value stored on `Athlete.lthr_bpm` in `running_coach_ai/scheduler/jobs.py`; (b) LTHR already set -> no overwrite attempt; (c) `!admin clean-garmin` resets `lthr_bpm` to `None` in `running_coach_ai/slack/admin.py`. [Sync: Gap Report]
- [ ] T085 [P] [US3] Write unit tests for `training_readiness` in `tests/unit/test_training_readiness.py`: (a) `parse_health_snapshot` extracts score integer from Garmin response and persists to `HealthSnapshot.training_readiness` in `running_coach_ai/garmin/parser.py`; (b) health context assembler includes "Training readiness: N" when non-null in `running_coach_ai/slack/conversation.py` and `running_coach_ai/coach/adapter.py`; (c) missing Garmin training-readiness data -> field is `None`, no error raised. [Sync: Gap Report]
- [ ] T086 [P] Write unit tests for `LOG_FILE` rotating handler in `tests/unit/test_config_logging.py`: (a) `LOG_FILE` unset -> only stdout handler attached; (b) `LOG_FILE` set to a valid path -> `RotatingFileHandler` added to root logger with correct `maxBytes` and `backupCount` in `running_coach_ai/config.py:configure_logging`. [Sync: Gap Report]
- [ ] T087 [US6] Verify `handle_admin_command()` signature change is covered by existing tests: confirm `tests/unit/test_admin.py` passes `slack_client=None` for all existing test cases in `running_coach_ai/slack/admin.py` (backward-compat check -- `slack_client` is keyword-only with `None` default, so existing call sites without it must not break). [Sync: Gap Report]
