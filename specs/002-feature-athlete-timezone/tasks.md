# Tasks: Athlete Timezone Auto-Detection

**Input**: Design documents from `/specs/002-feature-athlete-timezone/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/gps-timezone-extraction.md ✅, quickstart.md ✅

**Tests**: Required per Constitution Principle VII. Three new unit test files specified.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no incomplete dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths included in all descriptions

---

## Phase 1: Setup

**Purpose**: Add the only new dependency needed by this feature.

- [x] T001 Add `timezonefinder>=6.0.0` to `requirements.txt`

**Checkpoint**: Dependency declared — `pip install -r requirements.txt` will install `timezonefinder`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the shared timezone utility consumed by both US1 (onboarding) and US2 (activity ingestion). Must be complete before either user story starts.

**⚠️ CRITICAL**: US1 and US2 both import `derive_timezone_from_coords` — this phase must complete first.

- [x] T002 Create `running_coach_ai/coach/timezone_utils.py` with a module-level lazy `TimezoneFinder` singleton (initialised on first call, thread-safe for reads) and a single exported function `derive_timezone_from_coords(lat: float, lon: float) -> str | None` that calls `tf.timezone_at(lat=lat, lng=lon)`, catches all exceptions, logs warnings, and returns `None` on failure

**Checkpoint**: Foundation ready — `from running_coach_ai.coach.timezone_utils import derive_timezone_from_coords` works. Both user story phases can now begin.

---

## Phase 3: User Story 1 — Automatic Timezone Setup (Priority: P1) 🎯 MVP

**Goal**: Every newly onboarded athlete receives a correct IANA timezone in `athletes.timezone` at the moment onboarding completes, so their morning check-in job fires at 07:00 local time from day one.

**Independent Test**: Register a new athlete with a city NOT in the hardcoded table (e.g., "Austin") but valid `home_lat`/`home_lon` values matching "America/Chicago". Verify `athlete.timezone == "America/Chicago"` after `_complete_onboarding()`. Also verify an athlete whose city IS in the table (e.g., "London") still gets `"Europe/London"` unchanged.

### Tests for User Story 1

> **Write these tests FIRST — they should FAIL before T004 is implemented.**

- [x] T003 [P] [US1] Write unit tests in `tests/unit/test_onboarding_timezone.py` covering: (a) city in hardcoded table → existing IANA tz used directly, `derive_timezone_from_coords` NOT called; (b) city NOT in table with valid `home_lat`/`home_lon` → `derive_timezone_from_coords` called and result stored; (c) city NOT in table and `derive_timezone_from_coords` returns `None` → `athlete.timezone == "UTC"` with warning logged; (d) no `home_lat`/`home_lon` at all → `athlete.timezone == "UTC"`. Mock `derive_timezone_from_coords` in all cases.

### Implementation for User Story 1

- [x] T004 [US1] Modify `_complete_onboarding()` in `running_coach_ai/slack/onboarding.py`: after the block that calls `_parse_city_to_coords(city)` and sets `athlete.timezone = tz`, add an `else` branch for when `lat is None` (city not found); in that branch, if `athlete.home_lat` and `athlete.home_lon` are available call `derive_timezone_from_coords(athlete.home_lat, athlete.home_lon)` and assign the result to `athlete.timezone`, falling back to `"UTC"` with a `logger.warning(...)` if the result is `None`. **Note**: In the current codebase `home_lat`/`home_lon` are only set by `_parse_city_to_coords`, so the `home_lat`/`home_lon` check inside the `else` branch is defensive forward-compatibility — today, unlisted cities always fall through to `"UTC"`. The check MUST still be coded so the path activates automatically if a geocoder is added later.

**Checkpoint**: US1 fully functional and independently testable. New athletes with or without a city in the hardcoded table always leave onboarding with a valid IANA timezone.

---

## Phase 4: User Story 2 — Travel Auto-Adjustment (Priority: P2)

**Goal**: When an athlete completes an outdoor run in a different timezone, `athletes.timezone` is updated immediately and the morning check-in cron job is re-registered for the new local time — all within the same activity-poll cycle.

**Independent Test**: Take an athlete with `timezone = "America/New_York"`, inject a fake `get_activity_details` response with `summaryDTO.startLatitude`/`startLongitude` placing them in Seoul, call `_ingest_and_feedback`. Verify `athlete.timezone == "Asia/Seoul"` in the DB and that `register_athlete_morning_job` was called with the updated athlete.

### Tests for User Story 2

> **Write these tests FIRST — they should FAIL before T006–T008 are implemented.**

- [x] T005 [P] [US2] Write unit tests in `tests/unit/test_activity_timezone_update.py` covering: (a) `extract_activity_start_coords` with `summaryDTO.startLatitude`/`startLongitude` present → returns correct `(lat, lon)` tuple; (b) `summaryDTO` absent, `lapDTOs[0]` present → returns first-lap coords; (c) both paths null or zero → returns `None`; (d) end-to-end: `_ingest_and_feedback` with a travel activity updates `athlete.timezone`, calls `register_athlete_morning_job`, logs INFO transition; (e) treadmill run (no GPS) → `athlete.timezone` unchanged, `register_athlete_morning_job` NOT called. Mock `derive_timezone_from_coords`, `register_athlete_morning_job`, and external Garmin/Slack clients.

### Implementation for User Story 2

- [x] T006 [P] [US2] Add `extract_activity_start_coords(detail: dict) -> tuple[float, float] | None` to `running_coach_ai/garmin/parser.py`: navigate `detail["activityDetail"]["activity"]["summaryDTO"]` for `startLatitude`/`startLongitude` (primary), fall back to `detail["activityDetail"]["activity"]["lapDTOs"][0]` for `startLatitude`/`startLongitude`; reject coordinates where both `abs(lat) <= 0.001` and `abs(lon) <= 0.001` (treadmill zero-coordinates); return `(float(lat), float(lon))` or `None`
- [x] T007 [US2] Update the `_run_activity_poll(slack_client)` signature in `running_coach_ai/scheduler/jobs.py` to `_run_activity_poll(slack_client, scheduler=None)` and thread `scheduler` down to every `_ingest_and_feedback(...)` call within that function; update the `register_jobs()` call site in `running_coach_ai/scheduler/jobs.py` that registers `_run_activity_poll` via `scheduler.add_job` to pass the live `scheduler` instance as an additional `args` element
- [x] T008 [US2] Modify `_ingest_and_feedback` signature in `running_coach_ai/scheduler/jobs.py` to accept `scheduler` as a new parameter; after the `generate_post_run_feedback(...)` call, add timezone-update logic: call `extract_activity_start_coords(detail)`, if coords are returned call `derive_timezone_from_coords(*coords)`, if `new_tz` differs from `athlete.timezone` update `athlete.timezone`, `db_session.commit()`, log `"Timezone updated for Athlete %d: %s -> %s"` at INFO, then call `register_athlete_morning_job(scheduler, athlete, slack_client)` in a try/except that logs errors without rolling back the DB update (per spec edge-case requirement)

**Checkpoint**: US2 fully functional. Travel activities update timezone + reschedule morning job within one poll cycle.

---

## Phase 5: User Story 3 — Global Activity Polling (Priority: P2)

**Goal**: Remove the server-time gate `if not (6 <= now.hour < 22): return` from `_run_activity_poll` so athletes in any timezone have their activities ingested promptly 24 hours a day.

**Independent Test**: Mock `datetime.now()` to return 03:00 (outside old gate). Call `_run_activity_poll` with a mocked Garmin client. Verify the function does NOT return early and proceeds to poll athletes.

### Tests for User Story 3

> **Write this test FIRST — it should FAIL before T010 is implemented.**

- [x] T009 [P] [US3] Write unit test in `tests/unit/test_activity_poll_no_gate.py`: patch `running_coach_ai.scheduler.jobs.datetime` so `datetime.now()` returns a time of 03:00; assert `_run_activity_poll` proceeds past where the gate would have been (e.g., the mocked DB query or Garmin client is reached). Also write a second test confirming behaviour at 14:00 is unchanged.

### Implementation for User Story 3

- [x] T010 [US3] In `running_coach_ai/scheduler/jobs.py`, delete the three lines comprising the server-time gate from `_run_activity_poll`: `now = datetime.now()`, `if not (6 <= now.hour < 22):`, and `logger.debug("Outside active hours (%s), skipping activity poll", now.strftime("%H:%M"))` and `return`; also remove any `now` variable that is no longer referenced after this deletion

**Checkpoint**: US3 complete. `_run_activity_poll` runs unconditionally on every 10-minute tick. The 16-hour stale-window guard in `_ingest_and_feedback` remains untouched.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify lint and test suite integrity across all changes.

- [x] T011 [P] Run `ruff check . --fix` from repository root and resolve any lint errors introduced by new files or modified functions
- [x] T012 Run `pytest tests/unit/` from repository root and confirm all existing and new tests pass with zero failures

---

## Dependencies

```
T001 (requirements.txt)
  └── T002 (timezone_utils.py)          ← foundational, unblocks US1 + US2
        ├── T003 (US1 tests)     [P]    ← can start alongside T004
        ├── T004 (US1 impl)             ← depends T002
        ├── T005 (US2 tests)     [P]    ← can start alongside T006, T007
        ├── T006 (parser.py)     [P]    ← depends T002; independent of T007
        ├── T007 (jobs.py sig)          ← depends T002; must precede T008
        │     └── T008 (jobs.py impl)   ← depends T006, T007
        ├── T009 (US3 tests)     [P]    ← independent; can run in parallel with T005–T008
        └── T010 (gate removal)         ← can run after T007 (same file, same function)
T011, T012 run after all implementation tasks are complete
```

## Parallel Execution Examples

**US1 phase** (after T002):

```
T003 (test_onboarding_timezone.py) ─┬─ both read from timezone_utils.py, no shared writes
T004 (onboarding.py modification)  ─┘
```

**US2 + US3 phases** (after T002):

```
T005 (test_activity_timezone_update.py) ─┐
T006 (parser.py)                        ─┤─ all different files
T009 (test_activity_poll_no_gate.py)    ─┘
  then: T007 → T008, T010 (sequential within jobs.py)
```

## Implementation Strategy

**MVP Scope** (deliver US1 first): T001 → T002 → T003 → T004. This alone fixes the core issue where newly onboarded athletes get `NULL` timezone and fire check-ins at wrong hours.

**Increment 2** (US2): T005 → T006 → T007 → T008. Adds travel auto-adjustment.

**Increment 3** (US3): T009 → T010. Enables global polling — fastest to implement (single deletion).

**Format validation**: All 12 tasks follow the `- [ ] [TaskID] [P?] [Story?] Description with file path` format.

---

## Success Criteria Verification Notes

| SC                                              | Verification Method                                                                                                                                                                               | Covered By                                                                      |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| SC-001                                          | Unit test assertion: `athlete.timezone` is a non-null IANA string immediately after `_complete_onboarding()`                                                                                      | T003, T004                                                                      |
| SC-002 (within 60 s of ingestion)               | Inherent to synchronous pipeline: timezone update executes in the same `_ingest_and_feedback` call that processes the activity — no async delay possible. Verified post-deploy by log timestamps. | T005, T008                                                                      |
| SC-003 (job fires within ±5 min of 07:00 local) | Operational metric. Verified post-deploy by comparing `morning_checkin_*` log timestamps against the athlete’s stored `timezone`. Not unit-testable without a live scheduler.                     | T004, T008 (job re-registration); existing `register_athlete_morning_job` tests |
| SC-004                                          | Unit test: `_run_activity_poll` proceeds at 03:00 server time.                                                                                                                                    | T009, T010                                                                      |

## Remediation: Gaps (2026-04-10)

- [ ] T013 [P] [US1] Write unit tests for the updated `_next_checkin_start()` in `tests/unit/test_morning_checkin_polling.py`: (a) called before 06:00 local -> returns today's 06:00; (b) called between 06:00 and noon -> returns approximately now (within 1 second); (c) called after noon -> returns tomorrow's 06:00; (d) existing T079 tests still pass unmodified in `running_coach_ai/scheduler/jobs.py`. [Sync: Gap Report]
