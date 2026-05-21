---
description: "Task list for spec 007 — Today Card"
---

# Tasks: Today Card

**Input**: Design documents from `/specs/007-today-card/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/today-api.md, quickstart.md

**Tests**: Per Constitution Principle VII, every user story has unit and/or integration tests. External services (Claude, Garmin) are not touched by the new endpoint at request time, so no mocking is required; tests run against in-memory SQLite (FR-035).

**Organization**: Tasks are grouped by user story so each story can be implemented and verified independently. The foundational phase (Phase 2) provides shared infrastructure that all six stories depend on.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Maps to spec.md user stories (US1 = PRE_RUN, US2 = COMPLETED, US3 = REST_DAY, US4 = RACE_DAY, US5 = NO_PLAN, US6 = no-planned-row → REST_DAY)
- Each task description includes the exact file path to modify or create.

## Path Conventions

This feature follows the existing project layout per `plan.md`:

- Backend modules: `running_coach_ai/coach/`, `running_coach_ai/web/api/`
- Frontend: `running_coach_ai/web/static/`
- Tests: `tests/unit/`, `tests/integration/web/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm working state of the branch and pre-flight checks. No new project scaffolding is required — this is an additive feature in an existing repo.

- [X] T001 Confirm branch `007-today-card` is checked out and working tree is clean; run `ruff check .` and `pytest tests/unit/ -q` to verify baseline passes before any changes.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the shared infrastructure all six user stories depend on — new files, dataclass extensions, prompt directives, the endpoint shell, the magazine.html/css/js shell, and the chat dismissal handlers. **No user story phase can begin until this phase is complete.**

### Persona dataclass + values

- [X] T002 Extend `CoachPersona` dataclass with two new fields (`accent_color: str = "#b8ff4f"`, `race_morning_greeting: str = ""`) in `running_coach_ai/coach/personas.py` per data-model.md §2.
- [X] T003 Populate `accent_color` and `race_morning_greeting` for the `classic`, `maya`, and `jordan` entries in the `PERSONAS` registry in `running_coach_ai/coach/personas.py` using the canonical values from research.md Decisions 4 and 5.
- [X] T004 [P] Update `tests/unit/test_coach_personas.py` to assert each persona has a non-empty `accent_color` matching `^#[0-9A-Fa-f]{6}$` and a non-empty `race_morning_greeting` under 280 characters.

### Rationale helpers module

- [X] T005 Create `running_coach_ai/coach/today_rationale.py` with `extract_rationale_paragraph(body: str) -> str` per FR-011 — pure function, strips whitespace, splits on double-newline, returns first paragraph (or first sentence if no paragraph break exists), fully unit-testable.
- [X] T006 Add `rule_based_morning(snap, planned, plan, goal, athlete_name) -> str` to `running_coach_ai/coach/today_rationale.py` implementing both branches per FR-008/FR-008a/FR-008b/FR-008c: with-snapshot (~6 templates keyed by HRV/sleep/body battery buckets) and no-snapshot (run-by-feel cues per workout type). Every variant references workout_type, plan.current_phase/current_week if available, goal.race_name if available, and the athlete by name.

### Athlete-centered prompt directives

- [X] T007 [P] Update `MORNING_CHECKIN_PROMPT` in `running_coach_ai/coach/adapter.py:17` to append the athlete-centered single-paragraph rationale directive from FR-009 (covers periodization arc, today's readiness, one execution cue; first-person; references continuity; 2-3 sentences; no hedging). Keep existing `{name} / {todays_session} / {health_data} / {weather}` placeholders intact.
- [X] T008 [P] Update `POST_RUN_FEEDBACK_PROMPT` in `running_coach_ai/coach/feedback.py:14` with the analogous athlete-centered directive from FR-010 (single conversational paragraph: how the run went vs plan, one lesson, what's next; first-person; references continuity).

### Endpoint scaffold

- [X] T009 Create `running_coach_ai/web/api/today.py` with a Flask blueprint, the `GET /api/today` route protected by `@login_required`, athlete-local timezone resolution per FR-002 (use `ZoneInfo(athlete.timezone or "America/New_York")`, NEVER `date.today()`), and a stub that returns `{"state": "PRE_RUN", ...}` so the endpoint is reachable.
- [X] T010 Implement the state-resolution function in `running_coach_ai/web/api/today.py` per FR-003 precedence (NO_PLAN → RACE_DAY → COMPLETED → REST_DAY → PRE_RUN). Use `scoped_query(db_session, Model, athlete_id)` for every DB query. Return only the `state` field for now; per-state payload assembly comes in user-story phases. Note: no PlannedWorkout row → REST_DAY (treated as implicit rest, not a distinct alarm state).
- [X] T011 Implement the `WebEvent` emission helper in `running_coach_ai/web/api/today.py` per FR-032a/b and data-model.md §3: query the most recent `today.state_transition` event for the athlete, compare against the resolved state, INSERT a new event only when `from_state != to_state`, emit via Flask `@after_request` so the HTTP response is not blocked.
- [X] T012 Register the today blueprint in `running_coach_ai/web/app.py` inside `create_app()` (follow the existing pattern used for the magazine and notifications blueprints).
- [X] T013 [P] Add `today.state_transition` to the admin Events view filter dropdown in `running_coach_ai/web/api/admin.py` (and the corresponding frontend dropdown options in `running_coach_ai/web/static/magazine.js` if the dropdown is rendered client-side).

### Frontend shell — markup, styles, script

- [X] T014 Replace the existing `<section id="hero">…</section>` block in `running_coach_ai/web/static/magazine.html` with a new `<section id="today">…</section>` block containing markup for all five state variants per contracts/today-api.md (use `data-state="…"` attribute selectors so CSS and JS can toggle visibility per state).
- [X] T015 Add `#today` CSS rules to `running_coach_ai/web/static/magazine.css` per FR-020/FR-021 and Section 5 of the design: mobile-first 2×2 cover-line grid at <768px flattening to 4-column at ≥768px, accent color driven by a CSS variable `--coach-accent` set inline from the response payload, magazine-cover typography (Bebas Neue / DM Serif Display / Inter), ribbon styling, rationale-paragraph italicized serif with 2px accent border-left.
- [X] T016 Remove the now-unused `#hero` CSS rules from `running_coach_ai/web/static/magazine.css` (clean deletion, no leftover decorative styles).
- [X] T017 Implement `hydrateToday()` in `running_coach_ai/web/static/magazine.js`: fetch `GET /api/today` on page load, render-to-DOM per `state` and `data-state` attribute selectors, persist payload to `localStorage` under `runcoach.today.{athlete_id}` per FR-028a, start the 60s polling loop with `visibilitychange` per FR-026.
- [X] T018 Implement the localStorage-cache-first render path and the failure-mode fallbacks in `running_coach_ai/web/static/magazine.js` per FR-028a/b/c/d: synchronous cached render on page load (background fetch within 500ms), stale-dot indicator on cover-line values when the most recent fetch failed, skeleton placeholder when no cache exists, stale dots clear on next success without animation.
- [X] T019 Add the dimmed backdrop element + tap-outside-to-dismiss + Escape keydown handler for the chat panel per FR-025 and research.md Decision 3. Modify `running_coach_ai/web/static/magazine.html` (add backdrop element), `magazine.css` (backdrop styling, dim opacity), and `magazine.js` (click handler on backdrop calls `toggleChat()`; new global `keydown` handler closes chat on Escape, mirroring the admin-panel pattern at `magazine.js:1973`). Discard any unsent draft text without confirmation on all three paths.
- [X] T020 Wire the persona-switch refetch in `running_coach_ai/web/static/magazine.js` per FR-026: after a successful `POST /api/coach`, call `hydrateToday()` and replace the localStorage cache so a subsequent first-load failure cannot render the prior coach's voice.
- [X] T021 Wire the notification-bell refetch in `running_coach_ai/web/static/magazine.js` per FR-026: when the notification poll registers a new notification of kind `morning_checkin` or `post_run_feedback`, trigger an immediate `hydrateToday()` call.

**Checkpoint**: Foundation ready. The endpoint resolves states, the card renders a shell, and dismissal handlers work. User story implementation can now begin in parallel for any P1/P2/P3 story.

---

## Phase 3: User Story 1 — Pre-Run Briefing (Priority: P1) 🎯 MVP

**Goal**: Athlete opens the dashboard in their athlete-local morning and sees today's planned workout, pace target, and a one-paragraph rationale from their coach at the top of the page. Health stats appear as cover lines with drill-in to Morning Readiness. Tap on the workout opens chat.

**Independent Test**: Seed an athlete with an active Goal, a `PlannedWorkout(workout_type="tempo", scheduled_date=today)`, a `HealthSnapshot(date=today)`, and a `Notification(kind="morning_checkin", body="...", created_at=today)`. Hit `GET /api/today` and verify response is `state="PRE_RUN"`, headline contains the workout distance and pace, `rationale.source="morning_checkin"`, and `cover_lines` has 4 entries with drill_to="morning". Load `magazine.html` and verify the card renders above the Morning Readiness section.

### Backend implementation

- [X] T022 [US1] Implement `_build_pre_run_payload()` in `running_coach_ai/web/api/today.py`: assemble headline (workout_type label as eyebrow/ribbon, distance/pace as title, description as subtitle), implement the rationale resolution ladder (morning_checkin → placeholder before 7am local → rule_based) per FR-005, populate `modifiers.on_watch` from `PlannedWorkout.garmin_workout_id`.
- [X] T023 [US1] Implement `_pre_run_cover_lines()` in `running_coach_ai/web/api/today.py`: source from today's `HealthSnapshot` (HRV, body battery, sleep hours, RHR), apply the 3-day stale fallback from `web/api/magazine.py:339-356`, set `is_stale=true` on the entries that came from a non-today snapshot, set `drill_to="morning"` on all four.
- [X] T024 [US1] Implement the `actions` block for PRE_RUN in `running_coach_ai/web/api/today.py`: `headline_chat_prompt="Tell me about today's workout."`, `rationale_chat_prompt="I have a question about today's plan."`, `cta=null`.

### Frontend implementation

- [X] T025 [US1] Implement PRE_RUN rendering in `running_coach_ai/web/static/magazine.js`: render workout headline + rationale paragraph + four cover-line stats, wire headline tap → chat with `actions.headline_chat_prompt` pre-filled, wire rationale tap → chat with `actions.rationale_chat_prompt` pre-filled, wire cover-line tap → smooth-scroll to `#morning` section + 1-second highlight pulse class on the target section.
- [X] T026 [US1] Render `Ready on watch ✓` badge in PRE_RUN markup in `running_coach_ai/web/static/magazine.html` and `magazine.css` when `modifiers.on_watch === true`, styled in the active persona's accent color.

### Tests

- [X] T027 [P] [US1] Create `tests/integration/web/test_today_api.py` with the test harness (in-memory SQLite, Flask test client with session, `Athlete` fixture per `test_magazine_morning_report.py` pattern). First test: `test_pre_run_state_with_morning_checkin` — seed Goal + PlannedWorkout(tempo) + HealthSnapshot + Notification(morning_checkin); assert response state=`PRE_RUN`, rationale.source=`morning_checkin`, headline.title contains the pace, cover_lines length is 4.
- [X] T028 [P] [US1] Add `test_pre_run_rationale_placeholder_before_7am` and `test_pre_run_rationale_rule_based_after_7am` to `tests/integration/web/test_today_api.py` covering the fallback ladder when no morning_checkin Notification exists.
- [X] T029 [P] [US1] Add `test_pre_run_on_watch_badge_when_synced` to `tests/integration/web/test_today_api.py`: PlannedWorkout with non-null `garmin_workout_id` → `modifiers.on_watch=true`; null → false.
- [X] T030 [P] [US1] Create `tests/unit/test_today_rationale.py` with tests for `rule_based_morning()` with-snapshot branch: low HRV, normal HRV, high HRV × short sleep / normal sleep / low body battery — assert athlete name + workout_type + observed metric value appear in every output.
- [X] T031 [P] [US1] Add `test_rule_based_morning_no_snapshot_per_workout_type` to `tests/unit/test_today_rationale.py`: for each workout_type (easy, long_run, tempo, intervals, strides, recovery), assert the no-snapshot output explicitly acknowledges missing overnight data and provides a workout-type-specific run-by-feel cue.
- [X] T032 [P] [US1] Add `test_rule_based_morning_no_plan_no_goal` to `tests/unit/test_today_rationale.py`: when `plan` and `goal` are None, output still references workout_type by name and is grammatically clean (no broken sentences).
- [X] T033 [P] [US1] Add `test_extract_rationale_paragraph` to `tests/unit/test_today_rationale.py` covering: multi-paragraph body returns first paragraph, single-paragraph body returns it whole, empty/None returns `""`, body with trailing sign-off returns first paragraph only.

**Checkpoint**: PRE_RUN works end-to-end. The MVP is shippable to athletes with an active plan.

---

## Phase 4: User Story 2 — Card Reflects Completed Workout (Priority: P1)

**Goal**: After an athlete completes today's run and the activity poll picks it up, the Today Card transitions to COMPLETED state showing actual distance / pace / HR / training load with a coach-voiced reflection. Bonus runs (no matching PlannedWorkout) are surfaced as "Bonus Run — not on plan" without judgement.

**Independent Test**: Seed an athlete with today's `PlannedWorkout` AND a `CompletedWorkout(date=today, planned_workout_id=<that planned id>, coach_analysis="...")`. Hit `/api/today` and verify response is `state="COMPLETED"`, `modifiers.is_bonus=false`, cover_lines reference actual stats with drill_to="last_run", rationale.source="coach_analysis". Repeat with `planned_workout_id=null` and verify `is_bonus=true` and the ribbon label changes.

### Backend implementation

- [X] T034 [US2] Implement `_build_completed_payload()` in `running_coach_ai/web/api/today.py`: headline includes actual distance and pace from `CompletedWorkout`, ribbon = `"Completed"` (or `"Bonus Run — not on plan"` when `is_bonus`), rationale source ladder = `coach_analysis` first sentence-paragraph → rule-based completed-summary fallback per FR-006.
- [X] T035 [US2] Implement `_completed_cover_lines()` in `running_coach_ai/web/api/today.py`: Distance (mi), Pace (min/mi), Avg HR, Training Load; all sourced from `CompletedWorkout`; `drill_to="last_run"`.
- [X] T036 [US2] Implement `modifiers.is_bonus` resolution: `True` when `CompletedWorkout.planned_workout_id IS NULL`, else `False`.
- [X] T037 [US2] Implement the COMPLETED rule-based fallback in `running_coach_ai/coach/today_rationale.py` per FR-006: a small templated summary referencing the completed distance, pace vs target, and one observation. Used when `coach_analysis` is empty.

### Frontend implementation

- [X] T038 [US2] Render COMPLETED state in `running_coach_ai/web/static/magazine.js`: green-completion ribbon, drill-to-last_run for cover-line taps (smooth-scroll to `#featrun`), headline chat pre-fill = `"How did today's run go?"`, ribbon-swap slide animation per FR-027 only when state transitions live during the session.

### Tests

- [X] T039 [P] [US2] Add `test_completed_state_with_coach_analysis` to `tests/integration/web/test_today_api.py`: seed PlannedWorkout + CompletedWorkout(planned) + coach_analysis; assert state=`COMPLETED`, rationale.source=`coach_analysis`, modifiers.is_bonus=false, cover_lines drill_to=`last_run`.
- [X] T040 [P] [US2] Add `test_completed_state_bonus_run` to `tests/integration/web/test_today_api.py`: CompletedWorkout with `planned_workout_id=null` → `is_bonus=true`, ribbon label changes to "Bonus Run — not on plan".
- [X] T041 [P] [US2] Add `test_completed_rationale_rule_based_when_coach_analysis_empty` to `tests/integration/web/test_today_api.py`.
- [X] T042 [P] [US2] Add `test_rule_based_completed_summary` unit test to `tests/unit/test_today_rationale.py`.

**Checkpoint**: COMPLETED works. The full P1 (US1 + US2) is shippable as MVP.

---

## Phase 5: User Story 3 — Rest Day (Priority: P2)

**Goal**: On a deliberate rest day, the card shows `"Rest Day — Recovery is the workout"` with a coach-voiced explanation of recovery context and the same four health cover lines from PRE_RUN. Reinforces periodization as a first-class state, not a "broken card."

**Independent Test**: Seed an athlete with `PlannedWorkout(workout_type="rest", scheduled_date=today)`. Hit `/api/today` and verify `state="REST_DAY"`, headline title = "Recovery is the workout", cover_lines present from HealthSnapshot.

### Backend implementation

- [X] T043 [US3] Implement `_build_rest_day_payload()` in `running_coach_ai/web/api/today.py`: headline eyebrow/ribbon = `"Rest Day"`, title = `"Recovery is the workout"`, subtitle = `null`. Rationale source ladder identical to PRE_RUN (morning_checkin → placeholder → rule_based), but `rule_based_morning` is called with the rest workout type so the periodization clause frames recovery, not running.

### Frontend implementation

- [X] T044 [US3] Render REST_DAY state in `running_coach_ai/web/static/magazine.js`: no on_watch badge, no headline chat-target (rationale tap still opens chat with `"How should I make the most of today's recovery?"`), cover-line drill-to = `morning`.

### Tests

- [X] T045 [P] [US3] Add `test_rest_day_state` to `tests/integration/web/test_today_api.py`: seed PlannedWorkout(workout_type="rest") + morning_checkin; assert state=`REST_DAY`, rationale.source=`morning_checkin`, cover_lines drill_to=`morning`.
- [X] T046 [P] [US3] Add `test_rest_day_transitions_to_completed_on_bonus_run` to `tests/integration/web/test_today_api.py`: REST_DAY + CompletedWorkout(no planned_workout_id) → state=`COMPLETED`, `is_bonus=true`. Verifies the precedence ladder in FR-003.

---

## Phase 6: User Story 4 — Race Day (Priority: P2)

**Goal**: On race day, the card displays the race name + countdown + goal pace / HR cap, with a persona-specific race-morning greeting. Cover lines are read-only (no drill).

**Independent Test**: Seed an athlete with `PlannedWorkout(workout_type="race", scheduled_date=today)` and matching `Goal(race_date=today, race_name="Berlin Marathon")`. Hit `/api/today` and verify `state="RACE_DAY"`, headline title contains "Berlin Marathon", rationale.source=`persona_static`, rationale.text matches the active persona's `race_morning_greeting`, cover_lines drill_to=null.

### Backend implementation

- [X] T047 [US4] Implement `_build_race_day_payload()` in `running_coach_ai/web/api/today.py`: pull race_name from the active Goal, compute HR cap as `athlete.lthr_bpm * 0.92` (or null if lthr_bpm is missing), populate `rationale.text` from `get_persona(athlete.coach_key).race_morning_greeting`, `rationale.source="persona_static"`.
- [X] T048 [US4] Implement `_race_day_cover_lines()` in `running_coach_ai/web/api/today.py`: Distance (from PlannedWorkout.target_distance_km or fall back to Goal-implied distance), Goal Pace (PlannedWorkout.target_pace_min_per_km converted to min/mi), HR Cap, Weather="—" placeholder per FR-015. All entries have `drill_to=null`.

### Frontend implementation

- [X] T049 [US4] Render RACE_DAY state in `running_coach_ai/web/static/magazine.js`: countdown widget in place of the on_watch badge (compute from PlannedWorkout.scheduled_start_time if present, else race-day banner without countdown), cover-line stats inert (cursor:default, no tap handlers wired), headline chat pre-fill = `"Walk me through race execution."`.

### Tests

- [X] T050 [P] [US4] Add `test_race_day_state` to `tests/integration/web/test_today_api.py`: seed PlannedWorkout(workout_type="race") + Goal; assert state=`RACE_DAY`, rationale.text matches the persona's race_morning_greeting, cover_lines drill_to=null for all entries.
- [X] T051 [P] [US4] Add `test_race_day_greeting_per_persona` to `tests/integration/web/test_today_api.py`: switch athlete.coach_key to maya and jordan in turn; assert rationale.text matches each persona's race_morning_greeting from research.md Decision 5.

---

## Phase 7: User Story 5 — No-Plan State Converts to Goal Selection (Priority: P3)

**Goal**: Athlete with no active Goal sees a "Pick a race" CTA card at the top of the dashboard. Tapping opens chat with a pre-filled goal-setting prompt.

**Independent Test**: Seed an athlete with `onboarding_complete=true` and no `Goal(active=True)`. Hit `/api/today` and verify `state="NO_PLAN"`, headline title = "Ready to train for something?", `cover_lines=null`, `actions.cta.label="Pick a race"` and `actions.cta.chat_prompt="I want to train for…"`.

### Backend implementation

- [X] T052 [US5] Implement `_build_no_plan_payload()` in `running_coach_ai/web/api/today.py`: headline title = `"Ready to train for something?"`, rationale.text = static copy from FR-007a, rationale.source = `persona_static`, cover_lines = `null`, actions.cta with label/chat_prompt per contract.

### Frontend implementation

- [X] T053 [US5] Render NO_PLAN state in `running_coach_ai/web/static/magazine.js` and CSS: hide the cover-lines region entirely, render a single primary CTA button in `--coach-accent` with the label from `actions.cta.label`, button tap → open chat with `actions.cta.chat_prompt` pre-filled.

### Tests

- [X] T054 [P] [US5] Add `test_no_plan_state` to `tests/integration/web/test_today_api.py`: athlete with no active Goal; assert state=`NO_PLAN`, cover_lines is `null`, actions.cta is populated.

---

## Phase 8: US6 — no-planned-row → REST_DAY (Priority: P3)

**Goal**: Athlete with an active Goal but no `PlannedWorkout` for today sees a REST_DAY card — "Recovery is the workout" — with the four health cover lines and the Sleep / Fuel / Move cues strip. No CTA button; no alarm state. The five-state model treats any no-planned-row day as implicit rest.

**Independent Test**: Seed an athlete with an active Goal but zero PlannedWorkout rows for today (and explicitly no `workout_type="rest"` row). Hit `/api/today` and verify `state="REST_DAY"`, headline "Recovery is the workout", `cover_lines` length 4 with drill_to="morning", `cues` length 3 with labels Sleep / Fuel / Move, and `actions.cta=null`.

### Backend implementation

- [X] T055 [US6] Confirm `_build_rest_day_payload()` in `running_coach_ai/web/api/today.py` handles the no-planned-row branch (called when no PlannedWorkout row exists for today, in addition to the explicit rest-type row branch). Ensure the `cues` field is populated with the three default entries (Sleep / Fuel / Move) on every REST_DAY response, regardless of whether the rest day is explicit or implicit.

### Frontend implementation

- [X] T056 [US6] Confirm the REST_DAY rendering in `running_coach_ai/web/static/magazine.js` renders the `cues` strip (three pill-style labels: Sleep, Fuel, Move) when `cues` is present in the payload — the REST_DAY template handles both explicit rest-type rows and implicit no-planned-row days.

### Tests

- [X] T057 [P] [US6] Add `test_no_planned_row_treated_as_rest_day` to `tests/integration/web/test_today_api.py`: athlete with active Goal + zero PlannedWorkout rows for today → asserts `state="REST_DAY"`, headline title "Recovery is the workout", `cover_lines` length 4, `cues` length 3 with labels Sleep / Fuel / Move, `actions.cta=null`. This is the FR-033 regression scenario for the five-state model.
- [X] T058 [P] [US6] Add `test_no_planned_row_transitions_to_completed_on_bonus_run` to `tests/integration/web/test_today_api.py`: active Goal + no planned row + a CompletedWorkout(planned_workout_id=null) → state transitions to `COMPLETED` with `is_bonus=true`, mirroring the explicit rest-day → bonus-run transition.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: WebEvent emission verification, manual verification, prompt-quality smoke test, lint clean, mobile QA, documentation.

### Observability tests

- [X] T059 [P] Add `test_state_transition_emits_web_event` to `tests/integration/web/test_today_api.py` per FR-032a: call /api/today twice — first as PRE_RUN, then mutate DB to COMPLETED state and call again. Assert a new `WebEvent(kind="today.state_transition", extra={from_state: "PRE_RUN", to_state: "COMPLETED"})` row exists for the athlete after the second call. Call a third time with no state change — assert NO new WebEvent row is added.
- [X] T060 [P] Add `test_first_ever_state_resolution_emits_web_event` to `tests/integration/web/test_today_api.py`: athlete with no prior `today.state_transition` events; first call to /api/today emits an event with `from_state=null` and the resolved `to_state`.

### Lint and verification

- [X] T061 [P] Run `ruff check running_coach_ai/web/api/today.py running_coach_ai/coach/today_rationale.py running_coach_ai/coach/personas.py running_coach_ai/coach/adapter.py running_coach_ai/coach/feedback.py running_coach_ai/web/app.py running_coach_ai/web/api/admin.py` and fix any reported issues.
- [X] T062 Run the full test suite — `pytest tests/unit/ tests/integration/ -q` — and confirm all new tests pass alongside the existing baseline.

### Manual QA

- [ ] T063 Walk through `specs/007-today-card/quickstart.md` Steps 1-7 against a local dev environment (web.py + scheduler running). Tick each "Done criteria" checkbox.
- [ ] T064 [P] Mobile viewport check at 375px iPhone SE in Chrome DevTools per quickstart.md Step 4: cover lines stack 2×2, no horizontal scroll, headline fits on one line, all interactive elements have at least a 44×44 px tap area.
- [ ] T065 Manual prompt-quality check per quickstart.md Step 9: trigger a real morning_checkin via the admin "force morning check-in" path for a seeded athlete with at least 2 recent CompletedWorkouts and `TrainingPlan.current_week >= 3`. Read the resulting `Notification.body` and confirm it covers periodization + readiness + execution cue in 2-3 sentences, addresses the athlete in first person, references continuity, and avoids hedging. Repeat for the post-run path after a recorded activity.

### Documentation

- [X] T066 [P] Update the `## Architecture` section of `CLAUDE.md` to mention the new `/api/today` endpoint, the `today.state_transition` WebEvent kind, and the new `coach/today_rationale.py` module. Keep the entry concise (one bullet under "Web dashboard").
- [ ] T067 Commit final polish on branch `007-today-card`; verify branch is ready to push and open a PR draft.

---

## Dependencies

**Strict phase ordering**: Phase 1 → Phase 2 → (Phase 3 + Phase 4 in parallel for the MVP) → (Phase 5 + Phase 6 in parallel) → (Phase 7 + Phase 8 in parallel) → Phase 9.

**Within Phase 2** (foundational):

- T002 → T003 → T004 (persona dataclass → values → tests, same file)
- T005 → T006 (rationale module, same file)
- T007, T008 are parallel (different files)
- T009 → T010 → T011 → T012 (today.py shell, same file, then registration)
- T013 is parallel after T011 (admin.py + magazine.js dropdown)
- T014 → T015 → T016 (HTML structure, then CSS additions, then CSS deletions)
- T017 → T018 (magazine.js cache + fallbacks, same file)
- T019 → T020 → T021 (magazine.js features, same file)

**Within each user story phase**:

- Backend implementation tasks are sequential (same file: `web/api/today.py`).
- Frontend implementation tasks are sequential (same file: `magazine.js`).
- Test tasks are all `[P]` — they live in different test files or different test functions and have no shared state once foundational is done.

**Between user story phases (P1 vs P2 vs P3)**:

- US1 and US2 are both P1 and operate on different state-resolver branches in `today.py` — they can run in parallel after Phase 2.
- US3, US4 (P2) can each parallel either P1 story (different states, different `_build_*_payload()` functions, different test functions).
- US5, US6 (P3) share frontend shell (`renderTodayCard()` state handling) — implement US5 first, then US6 reuses the shell.

**Phase 9**: T059–T060 (WebEvent tests) can run anytime after T011. T061–T067 happen after all stories are merged.

---

## Parallel execution examples

**MVP burndown (after Phase 2 checkpoint)**:

```text
US1 backend (T022, T023, T024 sequential)  +  US2 backend (T034, T035, T036, T037 sequential)
       ↓                                              ↓
US1 frontend (T025, T026 sequential)        +  US2 frontend (T038)
       ↓                                              ↓
US1 tests T027–T033 all in parallel         +  US2 tests T039–T042 all in parallel
```

**Foundational batch (after T001)**:

```text
T002 → T003 → T004 in series       ⎫
T007, T008 in parallel             ⎬  all 4 tracks can run in parallel
T005 → T006 in series              ⎪
T009 → T010 → T011 → T012 series   ⎭
T013 after T011
T014 → T015 → T016 in series
T017 → T018 → T019 → T020 → T021 in series
```

---

## Implementation strategy

**MVP scope (ship after Phase 4 checkpoint)**: User Stories 1 (PRE_RUN) and 2 (COMPLETED) — the two P1 stories together cover the daily morning briefing and the post-run reflection, which is the core daily loop. REST_DAY, RACE_DAY, NO_PLAN, and the no-planned-row REST_DAY variant (US6) can ship as fast-follow increments.

**Sequence after MVP**:

1. **Sprint 1** — Phase 1 + Phase 2 (foundational, ~21 tasks) + Phase 3 + Phase 4 (US1 + US2, ~21 tasks) → **MVP shippable**.
2. **Sprint 2** — Phase 5 + Phase 6 (US3 + US4, ~9 tasks).
3. **Sprint 3** — Phase 7 + Phase 8 (US5 + US6, ~7 tasks).
4. **Sprint 4** — Phase 9 (polish, ~9 tasks).

**Total task count**: 67 tasks across 9 phases, with 6 user-story phases and 1 polish phase. Parallel opportunities: 33 tasks marked `[P]` (~49% of total).

---

## Independent test criteria summary

| Story | Independent Test |
|---|---|
| US1 (PRE_RUN, P1) | Seed Goal + PlannedWorkout(tempo) + HealthSnapshot + Notification(morning_checkin) → GET /api/today returns `state="PRE_RUN"` with rationale.source=`morning_checkin` and 4 cover_lines. |
| US2 (COMPLETED, P1) | Add CompletedWorkout(date=today, coach_analysis="...") to US1 seed → returns `state="COMPLETED"` with rationale.source=`coach_analysis`. |
| US3 (REST_DAY, P2) | Seed PlannedWorkout(workout_type="rest") → returns `state="REST_DAY"` with "Recovery is the workout" headline. |
| US4 (RACE_DAY, P2) | Seed PlannedWorkout(workout_type="race") + Goal(race_date=today) → returns `state="RACE_DAY"` with persona's race_morning_greeting. |
| US5 (NO_PLAN, P3) | Athlete with no active Goal → returns `state="NO_PLAN"` with cover_lines=null and CTA "Pick a race". |
| US6 (no-planned-row → REST_DAY, P3) | Active Goal + zero PlannedWorkout rows for today → returns `state="REST_DAY"`, headline "Recovery is the workout", 4 cover_lines, cues strip (Sleep/Fuel/Move), no CTA. |

Each story is independently verifiable via its own integration test alone, with no dependency on the other stories' completion.
