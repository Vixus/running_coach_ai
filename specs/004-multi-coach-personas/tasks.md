# Tasks: Multi-Coach Personas

**Input**: Design documents from `/specs/004-multi-coach-personas/`
**Prerequisites**: plan.md ?, spec.md ?, research.md ?, data-model.md ?, quickstart.md ?

**Tests**: Required per Constitution Principle VII. All new unit tests use pytest and mock external services. 3 new test files planned (test_coach_personas.py, test_coach_switch_tag.py, test_onboarding_coach.py).

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no unresolved dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths included in all descriptions

---

## Phase 1: Setup

**Purpose**: Confirm baseline before making changes

- [ ] T001 Run `pytest tests/unit/` to confirm baseline passes before any changes

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**?? CRITICAL**: No user story work can begin until this phase is complete

- [ ] T002 Create `running_coach_ai/coach/personas.py` — define `CoachPersona` frozen dataclass (fields: `key`, `name`, `description`, `persona_block`); implement `PERSONAS` dict with 3 entries: `"classic"` (Coach Alex, verbatim existing `COACH_PERSONA` text from `persona.py` plus new `<coach_switch>` tag instructions), `"sofia"` (Coach Sofia — evidence-based sports scientist persona with identical structural sections but scientific/methodical voice), `"miles"` (Coach Miles — high-energy motivational persona with punchy voice); set `DEFAULT_COACH_KEY = "classic"`; implement `get_persona(coach_key: str | None) -> CoachPersona` (returns default on None/unknown key); implement `is_valid_coach_key(key: str) -> bool`
- [ ] T003 [P] Add `coach_key = Column(Text, nullable=True)` to `Athlete` class in `running_coach_ai/database/models.py` (after `last_morning_checkin_date` column; nullable=True so existing rows survive until migration runs)
- [ ] T004 [P] Update `running_coach_ai/coach/persona.py` — remove the `COACH_PERSONA` module-level string constant; add backward-compatible re-export at bottom of file: `from running_coach_ai.coach.personas import get_persona as _get_persona; COACH_PERSONA = _get_persona(None).persona_block` so existing callers that import `COACH_PERSONA` directly still work without modification until each is individually migrated in Phase 3
- [ ] T005 [P] Create Alembic migration `running_coach_ai/database/migrations/versions/XXXX_add_coach_key_to_athletes.py` — `upgrade()`: `op.add_column("athletes", sa.Column("coach_key", sa.Text(), nullable=True))` then `op.execute("UPDATE athletes SET coach_key = 'classic' WHERE coach_key IS NULL")`; `downgrade()`: `op.drop_column("athletes", "coach_key")` (generate revision ID via `alembic revision --autogenerate -m "add coach_key to athletes"` or write by hand following existing version file conventions)

**Checkpoint**: `personas.py` importable, `Athlete.coach_key` column defined in model, migration file ready, `COACH_PERSONA` still importable from `persona.py` for backward compat

---

## Phase 3: User Story 1 — Select a Coach During Onboarding (Priority: P1) ?? MVP

**Goal**: New athletes are presented with coach choices during onboarding, their selection is persisted, and all AI-generated messages (morning check-in, feedback, plan updates) use the selected coach's voice.

**Independent Test**: Run a new athlete through onboarding, select "sofia", complete onboarding, trigger `run_morning_checkin()`, verify the resulting Slack DM uses Coach Sofia's evidence-based scientific tone.

### Tests for User Story 1

- [ ] T006 [P] [US1] Write `tests/unit/test_coach_personas.py` — test cases: (1) `PERSONAS` has exactly 3 keys: `"classic"`, `"sofia"`, `"miles"`; (2) `get_persona("classic")` returns persona with `name == "Coach Alex"`; (3) `get_persona(None)` returns `PERSONAS["classic"]`; (4) `get_persona("unknown_key")` returns `PERSONAS["classic"]`; (5) `is_valid_coach_key("sofia")` returns `True`; (6) `is_valid_coach_key("bogus")` returns `False`; (7) all 3 persona blocks contain the string `"<coach_switch>"` (ensures Claude always knows the tag)

- [ ] T007 [P] [US1] Write `tests/unit/test_onboarding_coach.py` — mock `db_session`, `generate_plan`, `sync_week_to_garmin`; test cases: (1) `onboarding_complete` JSON with `"coach_key": "sofia"` ? `athlete.coach_key == "sofia"` after `_complete_onboarding()` runs; (2) JSON without `coach_key` field ? `athlete.coach_key == "classic"` (default applied); (3) JSON with `"coach_key": "invalid_key"` ? `athlete.coach_key == "classic"` (fallback); (4) JSON with `"coach_key": "miles"` ? `athlete.coach_key == "miles"`

### Implementation for User Story 1

- [ ] T008 [US1] Convert `_SYSTEM_PROMPT` constant to `_build_onboarding_system_prompt()` function in `running_coach_ai/slack/onboarding.py` — at call time, import `PERSONAS` from `running_coach_ai.coach.personas` and build a coach roster block listing each coach's `name` and `description` in a readable format; insert the roster into the prompt text; extend the instructions to tell Claude: present coaches as one of the onboarding intake questions; include the athlete's chosen `coach_key` (exact registry key, e.g. `"sofia"`) in the `<onboarding_complete>` JSON; if athlete doesn't choose or is unclear, default to `"classic"`; update calls to `generate_welcome()` and `handle()` in the same file to call `_build_onboarding_system_prompt()` instead of referencing `_SYSTEM_PROMPT`

- [ ] T009 [US1] Update `_complete_onboarding()` in `running_coach_ai/slack/onboarding.py` — extract `coach_key = data.get("coach_key", DEFAULT_COACH_KEY)` from the parsed JSON; validate with `is_valid_coach_key(coach_key)` — fall back to `DEFAULT_COACH_KEY` if invalid; set `athlete.coach_key = coach_key` before `db_session.flush()`; import `is_valid_coach_key` and `DEFAULT_COACH_KEY` from `running_coach_ai.coach.personas`

- [ ] T010 [US1] Update `build_system_prompt()` in `running_coach_ai/slack/conversation.py` — replace `sections = [COACH_PERSONA]` with `from running_coach_ai.coach.personas import get_persona` (add to file imports); `sections = [get_persona(athlete.coach_key).persona_block]`; remove the `COACH_PERSONA` import from the top of the file

- [ ] T011 [P] [US1] Update `run_morning_checkin()` in `running_coach_ai/coach/adapter.py` — replace `call_claude(COACH_PERSONA, [{"role": "user", "content": prompt}])` with `call_claude(get_persona(athlete.coach_key).persona_block, [{"role": "user", "content": prompt}])`; add `from running_coach_ai.coach.personas import get_persona` to imports; remove `COACH_PERSONA` from the `from running_coach_ai.coach.persona import ...` import line

- [ ] T012 [P] [US1] Update `adapt_next_week()` in `running_coach_ai/coach/adapter.py` — replace both `call_claude(COACH_PERSONA, ...)` calls (lines ~196 and ~252) with `call_claude(get_persona(athlete.coach_key).persona_block, ...)`; `get_persona` already imported from T011; remove remaining `COACH_PERSONA` references in this file

- [ ] T013 [P] [US1] Update `running_coach_ai/coach/feedback.py` — replace both `call_claude(COACH_PERSONA, ...)` calls (lines ~312 and ~359) with `call_claude(get_persona(athlete.coach_key).persona_block, ...)`; add `from running_coach_ai.coach.personas import get_persona` to imports; remove `COACH_PERSONA` from the `from running_coach_ai.coach.persona import ...` import line; note `athlete` is already in scope at both call sites

**Checkpoint**: New athlete can complete onboarding selecting "sofia" or "miles"; post-onboarding `athlete.coach_key` persisted; morning check-in DM uses selected coach voice; all 3 new test files pass

---

## Phase 4: User Story 2 — Switch Coach After Onboarding (Priority: P2)

**Goal**: An existing onboarded athlete can say "I want to change coaches", Claude presents the roster, athlete picks one, Claude emits `<coach_switch>key</coach_switch>`, `athlete.coach_key` is updated immediately, and all subsequent messages use the new coach's voice.

**Independent Test**: As an existing athlete with `coach_key = "classic"`, send "I'd like a different coach". After selecting Miles, trigger `run_morning_checkin()` and verify the DM uses Coach Miles's high-energy tone. Also verify `athlete.coach_key == "miles"` in the DB.

### Tests for User Story 2

- [ ] T014 [US2] Write `tests/unit/test_coach_switch_tag.py` — mock `db_session` with a mock `Athlete` object (initial `coach_key = "classic"`); test cases: (1) response containing `<coach_switch>sofia</coach_switch>` ? `athlete.coach_key == "sofia"`, `db_session.commit()` called, tag stripped from returned string; (2) response containing `<coach_switch>bogus_key</coach_switch>` ? `athlete.coach_key` unchanged (still `"classic"`), tag stripped, warning logged; (3) response with no `<coach_switch>` tag ? returned string unchanged, `db_session.commit()` not called; (4) response where athlete selects their current coach ? `athlete.coach_key` unchanged, no error

### Implementation for User Story 2

- [ ] T015 [US2] Implement `extract_coach_switch(athlete: Athlete, response: str, db_session: Session) -> str` in `running_coach_ai/slack/conversation.py` — use `re.search(r"<coach_switch>(.*?)</coach_switch>", response, re.DOTALL)` to find the tag; if found: strip whitespace from captured key; if `is_valid_coach_key(new_key)`: set `athlete.coach_key = new_key`, call `db_session.commit()`, log at INFO level with `athlete.id` and `new_key`; else: log warning with invalid key and `athlete.id` (no DB write); in all cases strip the `<coach_switch>...</coach_switch>` tag from response via `re.sub` and return cleaned string; add `is_valid_coach_key` to imports from `running_coach_ai.coach.personas`

- [ ] T016 [US2] Wire `extract_coach_switch()` into `handle_message()` in `running_coach_ai/slack/conversation.py` — add call after `extract_and_save_memories()` and before the conversation messages are persisted to DB: `response = extract_coach_switch(athlete, response, db_session)`; verify the `athlete` object (not just `athlete_id`) is available in `handle_message()` scope at this point (it is — check the function signature and add `athlete` parameter if needed, or look up from DB if only `athlete_id` is passed)

**Checkpoint**: Existing athlete can trigger a coach switch via natural message; `<coach_switch>` tag processed and stripped; `athlete.coach_key` updated in DB; next scheduler-triggered message uses new coach's persona; training plan and Garmin state intact

---

## Phase 5: User Story 3 — Browse Coach Profiles (Priority: P3)

**Goal**: Any athlete can ask to see all available coaches and descriptions on demand without being forced to switch. Claude lists all coaches and waits for the athlete to initiate a switch if desired.

**Independent Test**: Send "Can you show me all my coach options?" as an onboarded athlete. Verify Claude's response lists all 3 coaches (Alex, Sofia, Miles) with their descriptions, without emitting a `<coach_switch>` tag.

### Implementation for User Story 3

- [ ] T017 [US3] Add an `## Available Coaches` section to `build_system_prompt()` in `running_coach_ai/slack/conversation.py` — after the existing sections list, append a section that iterates `PERSONAS.values()` and formats each as `"- **{name}** ({key}): {description}"`; add a one-line instruction: "When the athlete asks to see, browse, or list available coaches, present this section. Do not emit `<coach_switch>` unless they explicitly confirm a choice."; add `PERSONAS` to imports from `running_coach_ai.coach.personas`

**Checkpoint**: All 3 user stories independently functional; asking to browse coaches returns a formatted list without switching; switching still works via explicit confirmation flow

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Code quality, full test validation, migration applied

- [ ] T018 [P] Run `ruff check . --fix` on all modified files (`running_coach_ai/coach/personas.py`, `running_coach_ai/coach/persona.py`, `running_coach_ai/coach/adapter.py`, `running_coach_ai/coach/feedback.py`, `running_coach_ai/slack/conversation.py`, `running_coach_ai/slack/onboarding.py`, `running_coach_ai/database/models.py`) and resolve any reported issues
- [ ] T019 Run `pytest tests/unit/test_coach_personas.py tests/unit/test_coach_switch_tag.py tests/unit/test_onboarding_coach.py -v` and confirm all pass; then run `pytest tests/unit/` to confirm no regressions in existing tests
- [ ] T020 Apply migration and verify backfill: run `alembic upgrade head`, then run DB check from quickstart.md to confirm existing athletes have `coach_key = 'classic'` and new athletes can be assigned non-default keys

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — run immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion; **BLOCKS all user stories**
  - T002 (`personas.py`) must complete first — T003, T004, T005 can run in parallel after
- **User Story 1 (Phase 3)**: Depends on Phase 2 complete; T006/T007 (tests) can be written in parallel with T008–T013
  - T008 (onboarding prompt) must precede T009 (onboarding complete handler)
  - T010, T011, T012, T013 are independent of each other — all [P]
- **User Story 2 (Phase 4)**: Depends on Phase 2 complete; T014 (tests) before T015 (impl); T015 before T016 (wiring)
- **User Story 3 (Phase 5)**: Depends on Phase 2 (`PERSONAS` importable) and T010 (`build_system_prompt` already updated)
- **Polish (Phase 6)**: Depends on all desired stories complete

### User Story Dependencies

- **US1 (P1)**: After Phase 2 — no story dependencies
- **US2 (P2)**: After Phase 2 — no story dependencies on US1 (independently testable)
- **US3 (P3)**: After Phase 2 and T010 — reads `PERSONAS` in `build_system_prompt()` which US1 also modifies (coordinate on same function, or do US3's T017 after T010)

### Parallel Opportunities

Within Phase 2 (after T002):
- T003, T004, T005 all touch different files — fully parallel

Within Phase 3 (after Phase 2):
- T006, T007 (tests) — parallel with each other and can be written before impl tasks
- T011, T012, T013 — parallel (different functions/files)
- T008 ? T009 (sequential, same file, dependent)

---

## Parallel Example: Phase 2

```
# After T002 completes — launch all remaining foundational tasks together:
Task T003: models.py — add coach_key column
Task T004: persona.py — backward-compat re-export
Task T005: create Alembic migration file
```

## Parallel Example: User Story 1 Implementation

```
# After T008 + T009 complete (onboarding), launch these in parallel:
Task T010: conversation.py — build_system_prompt()
Task T011: adapter.py — run_morning_checkin()
Task T012: adapter.py — adapt_next_week()
Task T013: feedback.py — generate_weekly_review() + generate_activity_feedback()
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T005) — **CRITICAL**
3. Complete Phase 3: User Story 1 (T006–T013)
4. **STOP and VALIDATE**: Trigger onboarding with a test athlete, select a non-default coach, verify morning check-in DM uses correct voice
5. Deploy if validated — athletes can now select coaches during onboarding

### Incremental Delivery

1. Setup + Foundational ? Foundation ready (existing athletes unaffected)
2. User Story 1 ? New athletes get coach selection in onboarding (MVP!)
3. User Story 2 ? Existing athletes can switch coaches mid-cycle
4. User Story 3 ? Browsing without switching supported
5. Each story adds value; none breaks the others

### Task Count Summary

| Phase | Tasks | Parallelizable |
|-------|-------|---------------|
| Setup | 1 | 0 |
| Foundational | 4 | 3 |
| US1 | 8 | 5 |
| US2 | 3 | 0 |
| US3 | 1 | 0 |
| Polish | 3 | 1 |
| **Total** | **20** | **9** |
