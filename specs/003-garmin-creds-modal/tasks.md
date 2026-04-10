# Tasks: Garmin Credentials via Slack Modal

**Input**: Design documents from `specs/003-garmin-creds-modal/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/slack-interactive.md ✓, quickstart.md ✓

**Tests**: Required per Constitution Principle VII — unit tests included for all user stories. Mock external services (Garmin, Claude, Slack SDK). No integration tests required (existing integration test suite unchanged).

**Organization**: Tasks grouped by user story. US1 is the complete MVP — US2 and US3 are independently additive.

**Note on `_SYSTEM_PROMPT`**: The onboarding system prompt (`_SYSTEM_PROMPT_BASE`) no longer includes a Garmin credential question or `garmin_email`/`garmin_password` fields in the `<onboarding_complete>` JSON example — this was handled as part of feature 004. No prompt changes are needed for this feature.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story (US1, US2, US3)
- Exact file paths included in every implementation task

---

## Phase 1: Setup

_No project initialization needed — existing project structure. Migration tooling (Alembic) already configured._

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: DB schema change that ALL user story implementations depend on.

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete.

- [x] T001 Add `pending_onboarding_data` (JSON, nullable) and `pending_onboarding_data_created_at` (DateTime, nullable) columns to `Athlete` class in `running_coach_ai/database/models.py`
- [x] T002 Generate Alembic migration and apply with `alembic upgrade head` — migration file at `running_coach_ai/database/migrations/versions/1f11b24c1fdf_add_pending_onboarding_data.py`

**Checkpoint**: Schema updated ✅ — user story implementation can begin.

---

## Phase 3: User Story 1 - Athlete Submits Garmin Credentials via Modal (Priority: P1) 🎯 MVP

**Goal**: A new athlete completes onboarding without ever typing Garmin credentials into chat. After confirming their profile, they receive a button; clicking it opens a Slack modal; submitting the modal triggers onboarding completion.

**Independent Test**: Complete a full new athlete onboarding. Verify: (a) no Garmin credentials appear in Slack message history, (b) `data/garmin_sessions/<id>/oauth1_token.json` is created, (c) Week 1 training summary DM received.

### Tests for User Story 1

> **Write BEFORE implementation — tests must fail first.**

- [x] T003 [P] [US1] Write unit tests for `open_garmin_creds_modal` action handler in `tests/unit/test_garmin_creds_modal.py`: (1) `ack()` called; (2) `client.views_open()` called with correct `trigger_id` and modal containing both block IDs; (3) TTL-expired pending data → DM sent, `views_open` NOT called; (4) `onboarding_complete=True` → silent no-op
- [x] T004 [P] [US1] Write unit tests for `garmin_creds_modal` view handler in `tests/unit/test_garmin_creds_modal.py`: (1) `ack()` called; (2) `onboarding_complete=True` → silent discard, no DM; (3) null/expired pending data → expiry DM sent; (4) happy path → ack DM sent, `_complete_onboarding` called with merged data dict containing `garmin_email` and `garmin_password`, pending columns cleared; (5) Garmin auth failure → error DM sent, credential button re-sent; (6) SC-001 negative assertion — after `<onboarding_complete>` is parsed and button sent in T007, query all `ConversationMessage` rows for the athlete and assert that none contain the strings `"garmin_email"` or `"garmin_password"` as substring values (verifies credentials never entered chat history)

### Implementation for User Story 1

- [x] T005 [US1] Add `PENDING_DATA_TTL_HOURS = 24` module-level constant and `_is_pending_data_expired(athlete: Athlete) -> bool` helper function to `running_coach_ai/slack/onboarding.py` per the TTL logic in `specs/003-garmin-creds-modal/data-model.md`
- [x] T006 [US1] Add `_send_garmin_credential_button(channel: str, client) -> None` helper to `running_coach_ai/slack/onboarding.py` that calls `client.chat_postMessage()` with the Block Kit button message (section text + primary button with `action_id="open_garmin_creds_modal"`) per the payload definition in `specs/003-garmin-creds-modal/contracts/slack-interactive.md`
- [x] T007 [US1] Modify the `<onboarding_complete>` branch inside `handle()` in `running_coach_ai/slack/onboarding.py`: after parsing `data`, set `athlete.pending_onboarding_data = data` and `athlete.pending_onboarding_data_created_at = datetime.utcnow()`, flush, call `say_fn(clean_response)` if `clean_response` is non-empty (guard against empty string when response was only the completion tag), call `_send_garmin_credential_button(channel, client)`; remove the `_scrub_credentials()` call and the `_complete_onboarding()` call from this branch (depends on T005, T006)
- [x] T008 [US1] Delete the `_scrub_credentials()` function from `running_coach_ai/slack/onboarding.py` (dead code — no call sites remain after T007; credentials no longer pass through chat)
- [x] T009 [P] [US1] Add `_ACTION_ID = "open_garmin_creds_modal"` and `_MODAL_CALLBACK_ID = "garmin_creds_modal"` as module-level string constants at the top of `running_coach_ai/slack/bot.py`
- [x] T010 [US1] Implement `@app.action(_ACTION_ID)` handler inside `register_handlers()` in `running_coach_ai/slack/bot.py`: call `ack()`, open DB session, look up athlete by `body["user"]["id"]`; if no Athlete row found or `athlete.allowed` is False, silently return (no DM, no modal); check `athlete.onboarding_complete` (silent return if true); call `_is_pending_data_expired()` (send expiry DM if expired, return); call `client.views_open(trigger_id=body["trigger_id"], view={...})` with the full modal definition from `specs/003-garmin-creds-modal/contracts/slack-interactive.md`; log key events with `logger.info`/`logger.error` (note: guard order is `allowed → onboarding_complete → TTL` — deliberately checks `onboarding_complete` before TTL since a complete athlete should always short-circuit with no DM regardless of pending data state) (depends on T009)
- [x] T011 [US1] Implement `@app.view(_MODAL_CALLBACK_ID)` handler inside `register_handlers()` in `running_coach_ai/slack/bot.py`: call `ack()`, open DB session, look up athlete by `body["user"]["id"]`, guard `onboarding_complete=True` (silent discard), guard null/expired pending data (send expiry DM — both null and expired use the same message since the athlete must restart either way), send ack DM `"Got it! Setting up your account, this may take a minute…"`, extract `garmin_email` + `garmin_password` from `view["state"]["values"]`, merge into copy of `athlete.pending_onboarding_data`, clear `pending_onboarding_data` and `pending_onboarding_data_created_at`, call `_complete_onboarding(athlete, merged_data, db_session, say_fn)` with a `say_fn` wrapper using `client.chat_postMessage(channel=athlete.slack_dm_channel_id, ...)`; on auth failure — catch `garminconnect.GarminConnectAuthenticationError` and `garth.exc.GarthHTTPError` (both signal bad credentials) — send error DM explaining invalid credentials, import and call `_send_garmin_credential_button()` to re-present button; all other exceptions propagate normally; log key branch outcomes with `logger.info`/`logger.error` (depends on T009, imports `_send_garmin_credential_button` from `onboarding.py`)
- [x] T012 [US1] Run `pytest tests/unit/test_garmin_creds_modal.py -v` and confirm all US1 tests from T003 and T004 pass

**Checkpoint**: US1 fully functional. New athletes can complete onboarding via modal. No credentials in chat history. Existing unit tests unaffected.

---

## Phase 4: User Story 2 - Athlete Sends Chat Message While Awaiting Credentials (Priority: P2)

**Goal**: If an athlete sends a chat message after profile confirmation (while awaiting modal submission), the bot replies with a reminder and re-sends the button. Claude is never called.

**Independent Test**: Complete onboarding conversation to the button step, send a chat message, verify bot sends reminder + button and no new `ConversationMessage` rows are created for this interaction.

### Tests for User Story 2

- [x] T013 [P] [US2] Write unit tests for the pending-state chat guard in `tests/unit/test_garmin_creds_modal.py`: (1) athlete with non-expired `pending_onboarding_data` sends message → `call_claude` NOT called, reminder text sent, `_send_garmin_credential_button` called; (2) athlete with expired `pending_onboarding_data` sends message → expiry DM sent, button NOT re-sent; (3) athlete with `pending_onboarding_data = None` → guard does NOT trigger, normal `handle()` flow continues

### Implementation for User Story 2

- [x] T014 [US2] Add pending-state guard at the top of `handle()` in `running_coach_ai/slack/onboarding.py` (before any Claude call): if `athlete.pending_onboarding_data is not None`, check `_is_pending_data_expired(athlete)` — if expired send expiry DM and return; else call `say_fn("You're all set — just click the button below to enter your Garmin credentials securely.")` and call `_send_garmin_credential_button(channel, client)`; return without calling Claude or creating `ConversationMessage` rows (depends on T005, T006)

**Checkpoint**: US1 + US2 complete. Athlete stuck at button step is gracefully handled. Claude not invoked in the awaiting-credentials state.

---

## Phase 5: User Story 3 - Admin Reset + Backward Compatibility (Priority: P3)

**Goal**: Admin can recover stuck athletes with `!admin reset-onboarding <uid>`. Existing onboarded athletes are unaffected.

**Independent Test**: Send `!admin reset-onboarding <uid>` as admin; verify DB state reset; verify already-onboarded athlete sends a message and receives a normal coaching reply with no modal or button.

### Tests for User Story 3

- [x] T015 [P] [US3] Write unit tests for `!admin reset-onboarding` in `tests/unit/test_admin_reset_onboarding.py`: (1) valid uid → `ConversationMessage` rows deleted, `pending_onboarding_data = None`, `pending_onboarding_data_created_at = None`, `onboarding_complete = False`, `onboarding_step = 0`, success message returned; (2) unknown uid → error message returned; (3) uid argument absent or empty → error message returned (uid is required; do NOT reset all athletes)
- [x] T016 [P] [US3] Confirm pre-existing tests in `tests/unit/test_onboarding_garmin_sync.py` and `tests/unit/test_onboarding_timezone.py` still pass without modification (`pytest tests/unit/test_onboarding_garmin_sync.py tests/unit/test_onboarding_timezone.py -v`)

### Implementation for User Story 3

- [x] T017 [US3] Add `reset_match = re.match(r"!admin\s+reset-onboarding(?:\s+(<@)?([A-Z0-9]+)>?)?", text, re.IGNORECASE)` branch to `handle_admin_command()` dispatch in `running_coach_ai/slack/admin.py`; if the uid capture group is absent or empty, return an error message (e.g. `"Usage: !admin reset-onboarding <user_id> — uid is required"`); do NOT attempt to reset all athletes; add `reset-onboarding <user_id>` to the unknown-command hint string
- [x] T018 [US3] Implement `_reset_onboarding_athlete(slack_user_id: str, db_session: Session) -> str` in `running_coach_ai/slack/admin.py`: look up athlete by `slack_user_id`; if not found return error; delete all `ConversationMessage` rows for `athlete.id`; set `athlete.pending_onboarding_data = None`, `athlete.pending_onboarding_data_created_at = None`, `athlete.onboarding_complete = False`, `athlete.onboarding_step = 0`; commit; return confirmation message

**Checkpoint**: Full feature complete. Admin recovery path operational. All three user stories independently testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T019 Run `ruff check . --fix` on all modified files (`running_coach_ai/slack/onboarding.py`, `running_coach_ai/slack/bot.py`, `running_coach_ai/slack/admin.py`) and resolve any remaining lint warnings
- [x] T020 Run full unit test suite `pytest tests/unit/ -v` and confirm all tests pass with zero new failures (one pre-existing failure in `test_activity_poll_no_gate` is expected and acceptable)

---

## Dependency Graph

```
T001+T002 ✅ already done → [all implementation tasks unblocked]

Within US1:
  T005 ─┐
  T006 ─┤→ T007 → T008
  T009 ──→ T010 → T011
  T011 uses _send_garmin_credential_button from T006 (import)

Tests:
  T003 [P] parallel with T005-T009
  T004 [P] parallel with T005-T009
  T013 [P] parallel with T014
  T015 [P] parallel with T016, T017, T018
  T012 depends on T010, T011

Admin:
  T017 → T018

Polish:
  T019, T020 after all implementation tasks
```

## Parallel Execution Examples

**US1 — schema already done:**

```
Thread A: T005 → T006 → T007 → T008
Thread B: T003, T004 (new test file — no file conflict)
Thread C: T009 → T010 → T011 (different file: bot.py)
Sync: T012 (after T010, T011 complete)
```

**US2 — after US1:**

```
Thread A: T014 (onboarding.py guard)
Thread B: T013 (test file — no conflict)
```

**US3 — after US2:**

```
Thread A: T017 → T018 (admin.py)
Thread B: T015 (new test file — no conflict), T016 (read-only pytest run)
```

## Implementation Strategy

**MVP Scope**: Phase 2 + Phase 3 (T001–T012) delivers the complete security improvement with zero credential exposure. US2 and US3 are stable increments that can ship immediately after.

**Suggested delivery order**:

1. T001–T002 ✅ done (foundation)
2. T003–T012 (US1 core, ~2–3 hours)
3. T013–T014 (US2 guard, ~30 min)
4. T015–T018 (US3 admin reset, ~45 min)
5. T019–T020 (polish, ~15 min)

**Total task count**: 20 tasks across 3 user stories

| User Story   | Task Count     | Files Touched                                           |
| ------------ | -------------- | ------------------------------------------------------- |
| Foundational | 2 ✅ (T001–T002) | `models.py`, migration                                |
| US1 (P1)     | 10 (T003–T012) | `onboarding.py`, `bot.py`, `test_garmin_creds_modal.py` |
| US2 (P2)     | 2 (T013–T014)  | `onboarding.py`, `test_garmin_creds_modal.py`           |
| US3 (P3)     | 4 (T015–T018)  | `admin.py`, `test_admin_reset_onboarding.py`            |
| Polish       | 2 (T019–T020)  | modified files                                          |
