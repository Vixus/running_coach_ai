# Tasks: Multi-Coach Personas

**Input**: Design documents from `/specs/004-multi-coach-personas/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, quickstart.md ✓

**Tests**: Required per Constitution Principle VII. All new unit tests use pytest and mock external services. 3 new test files: `test_coach_personas.py`, `test_coach_switch_tag.py`, `test_onboarding_coach.py`.

**Organization**: Tasks grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no unresolved dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths included in all descriptions

---

## Phase 1: Setup

**Purpose**: Confirm baseline before making any changes

- [x] T001 Run `pytest tests/unit/` to confirm all existing tests pass before any changes

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Create `running_coach_ai/coach/personas.py` — define `CoachPersona` frozen dataclass (fields: `key`, `name`, `description`, `persona_block`); implement `PERSONAS` dict with 3 entries: `"classic"` (Coach Alex — verbatim existing `COACH_PERSONA` text from `coach/persona.py` plus new `<coach_switch>` tag instructions added to the XML tags section), `"sofia"` (Coach Sofia — evidence-based sports scientist persona), `"miles"` (Coach Miles — high-energy motivational persona); each of "sofia" and "miles" MUST include all 5 structural sections per data-model.md: (1) Identity, (2) Coaching Philosophy, (3) Communication Style, (4) XML Side-Effect Tag Instructions (all 4 tags: `<plan>`, `<remember>`, `<garmin_sync/>`, `<coach_switch>`), (5) Unit/Format Rules; set `DEFAULT_COACH_KEY = "classic"`; implement `get_persona(coach_key: str | None) -> CoachPersona` (returns default on None/unknown key); implement `is_valid_coach_key(key: str) -> bool`

- [x] T003 [P] Add `coach_key = Column(Text, nullable=True)` to `Athlete` class in `running_coach_ai/database/models.py` — place after the `last_morning_checkin_date` column; nullable=True so existing rows survive until migration runs

- [x] T004 [P] Update `running_coach_ai/coach/persona.py` — remove the `COACH_PERSONA` module-level string constant; add backward-compatible re-export at bottom of file: `from running_coach_ai.coach.personas import get_persona as _get_persona; COACH_PERSONA = _get_persona(None).persona_block` so existing callers that import `COACH_PERSONA` directly still work without modification until each is individually migrated in Phase 3

- [x] T005 [P] Create Alembic migration `running_coach_ai/database/migrations/versions/XXXX_add_coach_key_to_athletes.py` — `upgrade()`: `op.add_column("athletes", sa.Column("coach_key", sa.Text(), nullable=True))` then `op.execute("UPDATE athletes SET coach_key = 'classic' WHERE coach_key IS NULL")`; `downgrade()`: `op.drop_column("athletes", "coach_key")`; generate the revision ID by running `alembic revision --autogenerate -m "add coach_key to athletes"` or write by hand following existing version file conventions in `running_coach_ai/database/migrations/versions/`

**Checkpoint**: `personas.py` importable, `Athlete.coach_key` column defined in model, migration file ready, `COACH_PERSONA` still importable from `persona.py` for backward compat

---

## Phase 3: User Story 1 — Select a Coach During Onboarding (Priority: P1) 🎯 MVP

**Goal**: New athletes are presented with coach choices during onboarding, their selection is persisted, and all AI-generated messages (morning check-in, feedback, plan updates) use the selected coach's voice.

**Independent Test**: Run a new athlete through onboarding, select "sofia", complete onboarding, trigger `run_morning_checkin()`, verify the resulting Slack DM uses Coach Sofia's evidence-based scientific tone.

### Tests for User Story 1

- [x] T006 [P] [US1] Write `tests/unit/test_coach_personas.py` — test cases: (1) `PERSONAS` has exactly 3 keys: `"classic"`, `"sofia"`, `"miles"`; (2) `get_persona("classic")` returns persona with `name == "Coach Alex"`; (3) `get_persona(None)` returns `PERSONAS["classic"]`; (4) `get_persona("unknown_key")` returns `PERSONAS["classic"]`; (5) `is_valid_coach_key("sofia")` returns `True`; (6) `is_valid_coach_key("bogus")` returns `False`; (7) all 3 persona blocks contain the string `"<coach_switch>"` (ensures Claude always knows the tag); (8) `from running_coach_ai.coach.persona import COACH_PERSONA; assert isinstance(COACH_PERSONA, str)` (verifies backward-compat re-export from T004 is intact); (9) mock `call_claude` in `running_coach_ai.coach.adapter`; call `run_morning_checkin()` with an athlete whose `coach_key = "sofia"`; assert `call_claude` was called with first argument equal to `get_persona("sofia").persona_block` (verifies T011 migration); (10) same pattern for `adapt_next_week()` with `coach_key = "miles"` (verifies T012); (11) mock `call_claude` in `running_coach_ai.coach.feedback`; call the activity feedback function with `coach_key = "sofia"`; assert `call_claude` first argument matches `get_persona("sofia").persona_block` (verifies T013)

- [x] T007 [P] [US1] Write `tests/unit/test_onboarding_coach.py` — mock `db_session`, `generate_plan`, `sync_week_to_garmin`; test cases: (1) `onboarding_complete` JSON with `"coach_key": "sofia"` → `athlete.coach_key == "sofia"` after `_complete_onboarding()` runs; (2) JSON without `coach_key` field → `athlete.coach_key == "classic"` (default applied); (3) JSON with `"coach_key": "invalid_key"` → `athlete.coach_key == "classic"` (fallback); (4) JSON with `"coach_key": "miles"` → `athlete.coach_key == "miles"`; (5) call `_build_onboarding_system_prompt()` with no arguments; assert return value contains `"Coach Alex"`, `"Coach Sofia"`, `"Coach Miles"`, and `"classic"` (verifies coach roster injection into onboarding prompt)

### Implementation for User Story 1

- [x] T008 [US1] Convert `_SYSTEM_PROMPT` constant to `_build_onboarding_system_prompt()` function in `running_coach_ai/slack/onboarding.py` — at call time, import `PERSONAS` from `running_coach_ai.coach.personas` and build a coach roster block listing each coach's `name` and `description`; insert the roster into the prompt text; extend the instructions to tell Claude: present coaches as one of the onboarding intake questions, include the athlete's chosen `coach_key` (exact registry key, e.g. `"sofia"`) in the `<onboarding_complete>` JSON, if athlete doesn't choose or is unclear default to `"classic"`; update all calls to `generate_welcome()` and `handle()` in the same file to call `_build_onboarding_system_prompt()` instead of referencing `_SYSTEM_PROMPT`

- [x] T009 [US1] Update `_complete_onboarding()` in `running_coach_ai/slack/onboarding.py` — extract `coach_key = data.get("coach_key", DEFAULT_COACH_KEY)` from the parsed JSON; validate with `is_valid_coach_key(coach_key)` — fall back to `DEFAULT_COACH_KEY` if invalid; set `athlete.coach_key = coach_key` before `db_session.flush()`; import `is_valid_coach_key` and `DEFAULT_COACH_KEY` from `running_coach_ai.coach.personas`

- [x] T010 [US1] Update `build_system_prompt()` in `running_coach_ai/slack/conversation.py` — replace `sections = [COACH_PERSONA]` with `sections = [get_persona(athlete.coach_key).persona_block]`; add `from running_coach_ai.coach.personas import get_persona, PERSONAS` to file-level imports; remove the `COACH_PERSONA` import from the top of the file

- [x] T011 [P] [US1] Update all `call_claude(COACH_PERSONA, ...)` calls in `running_coach_ai/coach/adapter.py` � replace the call in `run_morning_checkin()` (line ~170) and both calls in `adapt_next_week()` (lines ~196 and ~252) with `call_claude(get_persona(athlete.coach_key).persona_block, ...)`; add `from running_coach_ai.coach.personas import get_persona` to imports; remove `COACH_PERSONA` from the `from running_coach_ai.coach.persona import ...` import line; do all three replacements in one edit to avoid import-line conflicts _(T012 merged into this task � both touch the same file and import line)_

- [x] T013 [P] [US1] Update `running_coach_ai/coach/feedback.py` — replace both `call_claude(COACH_PERSONA, ...)` calls (lines ~312 and ~359) with `call_claude(get_persona(athlete.coach_key).persona_block, ...)`; add `from running_coach_ai.coach.personas import get_persona` to imports; remove `COACH_PERSONA` from the `from running_coach_ai.coach.persona import ...` import line; `athlete` is already in scope at both call sites

**Checkpoint**: New athlete can complete onboarding selecting "sofia" or "miles"; `athlete.coach_key` persisted; morning check-in DM uses selected coach's voice; all test files pass

---

## Phase 4: User Story 2 — Switch Coach After Onboarding (Priority: P2)

**Goal**: An existing onboarded athlete can say "I want to change coaches", Claude presents the roster, athlete picks one, Claude emits `<coach_switch>key</coach_switch>`, `athlete.coach_key` is updated immediately, and all subsequent messages use the new coach's voice.

**Independent Test**: As an existing athlete with `coach_key = "classic"`, send "I'd like a different coach". After selecting Miles, trigger `run_morning_checkin()` and verify the DM uses Coach Miles's high-energy tone. Verify `athlete.coach_key == "miles"` in the DB.

### Tests for User Story 2

- [x] T014 [US2] Write `tests/unit/test_coach_switch_tag.py` � mock `db_session` with a mock `Athlete` object (initial `coach_key = "classic"`, also set a `name` and `id` attribute); test cases: (1) response containing `<coach_switch>sofia</coach_switch>` ? `athlete.coach_key == "sofia"`, `db_session.commit()` called, tag stripped from returned string; (2) response containing `<coach_switch>bogus_key</coach_switch>` ? `athlete.coach_key` unchanged (still `"classic"`), tag stripped, warning logged; (3) response with no `<coach_switch>` tag ? returned string unchanged, `db_session.commit()` not called; (4) after a valid switch, assert `athlete.name` and other non-`coach_key` attributes are unchanged (verifies FR-008: scope of mutation is `coach_key` only); (5) response where athlete selects their current coach (key already equals `"classic"`) ? `athlete.coach_key` still `"classic"`, `db_session.commit()` not called, no error raised (verifies US2 Acceptance Scenario 4)

### Implementation for User Story 2

- [x] T015 [US2] Implement `extract_coach_switch(athlete: Athlete, response: str, db_session: Session) -> str` in `running_coach_ai/slack/conversation.py` — use `re.search(r"<coach_switch>(.*?)</coach_switch>", response, re.DOTALL)` to find the tag; if found: strip whitespace from captured key; if `is_valid_coach_key(new_key)`: set `athlete.coach_key = new_key`, call `db_session.commit()`, log at INFO level with `athlete.id` and `new_key`; else: log warning with invalid key and `athlete.id` (no DB write); in all cases strip the `<coach_switch>...</coach_switch>` tag via `re.sub` and return cleaned string; add `is_valid_coach_key` to imports from `running_coach_ai.coach.personas`

- [x] T016 [US2] Wire `extract_coach_switch()` into `handle_message()` in `running_coach_ai/slack/conversation.py` � add call after `extract_and_save_memories()` and before the conversation messages are persisted to DB: `response = extract_coach_switch(athlete, response, db_session)`; if only `athlete_id` is in scope at this insertion point (not the full `athlete` object), add a lookup before the call: `athlete = scoped_query(db_session, Athlete, athlete_id)` � using the existing `scoped_query` helper per Constitution Principle I (data isolation)

**Checkpoint**: Existing athlete can trigger a coach switch via natural message; `<coach_switch>` tag processed and stripped; `athlete.coach_key` updated in DB; next scheduler-triggered message uses new coach's persona; training plan and Garmin state intact

---

## Phase 5: User Story 3 — Browse Coach Profiles (Priority: P3)

**Goal**: Any athlete can ask to see all available coaches and descriptions on demand without being forced to switch.

**Independent Test**: Send "Can you show me all my coach options?" as an onboarded athlete. Verify Claude's response lists all 3 coaches (Alex, Sofia, Miles) with their descriptions, without emitting a `<coach_switch>` tag.

### Implementation for User Story 3

- [x] T017 [US3] Add an `## Available Coaches` section to `build_system_prompt()` in `running_coach_ai/slack/conversation.py` — after the existing sections list, append a section that iterates `PERSONAS.values()` and formats each as `"- **{name}** ({key}): {description}"`; add a one-line instruction: `"When the athlete asks to see, browse, or list available coaches, present this section. Do not emit <coach_switch> unless they explicitly confirm a choice."`; `PERSONAS` is already imported from T010

**Checkpoint**: All 3 user stories independently functional; asking to browse coaches returns a formatted list without switching; switching still works via explicit confirmation flow

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Code quality, full test suite validation, migration applied

- [x] T018 [P] Run `ruff check . --fix` on all modified files (`running_coach_ai/coach/personas.py`, `running_coach_ai/coach/persona.py`, `running_coach_ai/coach/adapter.py`, `running_coach_ai/coach/feedback.py`, `running_coach_ai/slack/conversation.py`, `running_coach_ai/slack/onboarding.py`, `running_coach_ai/database/models.py`) and resolve any remaining issues

- [x] T019 Run `pytest tests/unit/test_coach_personas.py tests/unit/test_coach_switch_tag.py tests/unit/test_onboarding_coach.py -v` and confirm all pass; then run `pytest tests/unit/` to confirm no regressions in existing tests; manually verify SC-001 (coach selection adds ≤1 conversational exchange) and SC-002 (switch in single back-and-forth) against quickstart.md checklist

- [x] T020 Apply migration and verify backfill: run `alembic upgrade head`, then open the DB and confirm existing athletes have `coach_key = 'classic'`; create a test athlete record with no `coach_key` and confirm `get_persona(None)` returns `PERSONAS["classic"]` without error

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — run immediately
- **Foundational (Phase 2)**: Depends on Phase 1; **BLOCKS all user stories**
  - T002 must complete first; T003, T004, T005 can run in parallel after T002
- **User Story 1 (Phase 3)**: Depends on Phase 2; T006/T007 (tests) can be written in parallel with T008–T013
  - T008 must precede T009 (same file, dependent flow)
  - T010, T011, T013 are independent of each other � all [P] (T012 merged into T011)
- **User Story 2 (Phase 4)**: Depends on Phase 2; T014 before T015 (impl); T015 before T016 (wiring)
- **User Story 3 (Phase 5)**: Depends on Phase 2 and T010 (both modify `build_system_prompt` — do T017 after T010)
- **Polish (Phase 6)**: Depends on all desired stories complete

### User Story Dependency Graph

```
Phase 2 (T002–T005)
  ├── US1 (T006–T013)  ← MVP
  ├── US2 (T014–T016)  ← independent of US1
  └── US3 (T017)       ← requires T010 from US1
```

### Parallel Opportunities

Within Phase 2 (after T002 completes):

```
T003: models.py — add coach_key column        [parallel]
T004: persona.py — backward-compat re-export  [parallel]
T005: create Alembic migration file           [parallel]
```

Within Phase 3 (after Phase 2 completes):

```
T006: test_coach_personas.py      [parallel, can write before impl]
T007: test_onboarding_coach.py    [parallel, can write before impl]
T010: conversation.py � build_system_prompt()            [parallel]
T011: adapter.py � all 3 call sites (merged T012)        [parallel]
T013: feedback.py � both call sites                      [parallel]
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T005) — **CRITICAL**
3. Complete Phase 3: User Story 1 (T006–T013)
4. **STOP and VALIDATE**: Trigger onboarding with a test athlete, select a non-default coach, verify morning check-in DM uses correct voice
5. Deploy if validated — new athletes can now select coaches during onboarding

### Incremental Delivery

1. Setup + Foundational → Foundation ready (existing athletes fully unaffected)
2. User Story 1 → New athletes get coach selection in onboarding (**MVP**)
3. User Story 2 → Existing athletes can switch coaches mid-cycle
4. User Story 3 → Browsing without switching supported
5. Each story adds value; none breaks the others

### Task Count Summary

| Phase        | Tasks  | Parallelizable |
| ------------ | ------ | -------------- |
| Setup        | 1      | 0              |
| Foundational | 4      | 3              |
| US1          | 8      | 5              |
| US2          | 3      | 0              |
| US3          | 1      | 0              |
| Polish       | 3      | 1              |
| **Total**    | **20** | **9**          |
