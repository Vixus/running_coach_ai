# Research: Garmin Credentials via Slack Modal

**Branch**: `003-garmin-creds-modal` | **Date**: 2026-04-05

All unknowns resolved via direct codebase inspection. No external research required.

---

## Decision 1: Password Masking in Slack Block Kit Modals

**Decision**: Accept that the password field is not visually masked; include a disclaimer in the modal.

**Rationale**: Slack's `plain_text_input` element has no `type: password` attribute. The Block Kit Surface does not expose any alternative input type that obscures characters. The Slack API reference confirms this is an inherent platform limitation with no workaround within the modal surface.

**Alternatives considered**:

- _(Web form)_ A small FastAPI endpoint behind Synology reverse proxy + HTTPS — rejected by the user (more infrastructure, requires domain configuration).
- _(Admin CLI)_ Admin injects credentials manually — rejected by the user (doesn't scale, admin learns athletes' passwords).
- _(Encrypted deep-link)_ URL-encoded one-time token in the button that opens a web form — rejected (same infrastructure requirement as web form option).

**Mitigation**: FR-004 requires a disclaimer section in the modal advising the athlete to type their password in private. The core security gain (credentials not appearing in the Slack message log or conversation history) is still achieved.

---

## Decision 2: Slack Bolt view_submission Threading Model

**Decision**: Call `ack()` at the top of the `@app.view` handler, then run all post-processing (acknowledgement DM + `_complete_onboarding()`) in the same thread continuation.

**Rationale**: Slack's `view_submission` payload requires an acknowledgement within 3 seconds. Slack Bolt for Python (both sync and async flavours) runs each event handler in a worker thread from the framework's internal thread pool. Calling `ack()` immediately sends the acknowledgement packet back to Slack; the handler function continues executing in its thread without any additional `threading.Thread` wrapper. This is the documented Bolt pattern for long-running post-submission actions and is already in use for the existing `handle_message` flow (which calls Claude synchronously).

**Alternatives considered**:

- Explicit `threading.Thread` around `_complete_onboarding()` — unnecessary; Bolt already provides the thread. Adding another level of threading would complicate error handling.
- `asyncio`-based Bolt — the project uses sync Bolt; switching the whole bot to async would be out of scope.

---

## Decision 3: TTL Storage — Separate DateTime Column vs. Embedded Timestamp

**Decision**: Two new columns on `Athlete`: `pending_onboarding_data` (JSON) and `pending_onboarding_data_created_at` (DateTime).

**Rationale**: A separate DateTime column keeps the TTL check as a pure Python datetime comparison with no JSON parsing. It is also DB-queryable if a future admin script needs to list stale pending onboardings. The alternative (embedding `{"_created_at": "...", "name": ...}` inside the JSON blob) works but adds a parsing step and makes the TTL semantics implicit.

**Alternatives considered**:

- Single JSON column with embedded `_created_at` key — simpler schema change (one column instead of two) but requires JSON parsing on every TTL check and mixes metadata with profile data.

---

## Decision 4: Action Handler — `views_open` Call Location

**Decision**: `@app.action("open_garmin_creds_modal")` calls `ack()` then immediately calls `client.views_open(trigger_id=body["trigger_id"], view={...})`.

**Rationale**: `views_open` is a fast synchronous Slack API call (single HTTP request, ~100ms). The `trigger_id` from the `block_actions` payload expires after 3 seconds, so it must be used within the ack window. `ack()` + `views_open` fits comfortably within 3 seconds with no DB access needed in this handler.

**Note**: `trigger_id` is only available in `block_actions` events in SocketMode; it is not present in `view_submission` events.

---

## Decision 5: DM Channel Identity in @app.view Handler

**Decision**: Look up `athlete.slack_dm_channel_id` from the DB; use `client.chat_postMessage(channel=athlete.slack_dm_channel_id, ...)` for all post-ack messages.

**Rationale**: `@app.view` does not provide a `say()` function or a channel context — the event is triggered by a modal submission, not a channel message. The `body["user"]["id"]` gives the Slack user ID, which is used to look up the `Athlete` row and retrieve the cached `slack_dm_channel_id`. If `slack_dm_channel_id` is null (edge case: athlete somehow reaches the modal without a prior DM interaction), fall back to `client.conversations_open(users=[slack_user_id])["channel"]["id"]`.

---

## Decision 6: `_scrub_credentials` Disposal

**Decision**: Delete `_scrub_credentials()` entirely.

**Rationale**: The function exists solely to scrub a Garmin password that was typed into the chat and stored in `ConversationMessage` rows. With this feature, credentials never enter the chat — they go directly from the modal payload to `encrypt_password()`. The function has no callers after this change. Dead code with a misleading name is worse than no code.

---

## Decision 7: `_complete_onboarding()` Signature Preservation

**Decision**: Keep `_complete_onboarding(athlete, data, db_session, say_fn)` signature identical. The `@app.view` handler merges `garmin_email` and `garmin_password` (from the modal state) into the stored `data` dict before calling it.

**Rationale**: Preserving the signature means all existing unit tests (`test_onboarding_garmin_sync.py`, `test_onboarding_timezone.py`) continue to pass with zero modifications. Those tests pass a complete `data` dict directly to `_complete_onboarding()`. The merging of credentials from a different source (modal vs. completion JSON) is the view handler's responsibility, not `_complete_onboarding()`'s.

---

## Decision 8: Admin Reset Command — Full Conversation Wipe vs. State Flag Reset

**Decision**: `!admin reset-onboarding <uid>` deletes all `ConversationMessage` rows for the athlete (equivalent to a fresh start), clears `pending_onboarding_data` and `pending_onboarding_data_created_at`, and sets `onboarding_complete = False` and `onboarding_step = 0`.

**Rationale**: Keeping stale conversation history would cause Claude to see a half-finished onboarding transcript when the athlete restarts, leading to confused or repetitive responses. A clean wipe is the correct reset semantics. The existing `ConversationMessage` relationship on `Athlete` makes the bulk delete straightforward. Goal, TrainingPlan, and PlannedWorkout rows are _not_ deleted — the admin can use `!admin clean-garmin` separately if needed.

---

## Decision 9: Block Kit Constants — Module-Level vs. Inline

**Decision**: Define `_ACTION_ID = "open_garmin_creds_modal"` and `_MODAL_CALLBACK_ID = "garmin_creds_modal"` as module-level string constants in `bot.py`. Reference these constants in the handler decorators and the modal view dict.

**Rationale**: Module-level constants make it easy to update the IDs (e.g., in tests or if Slack changes requirements) without hunting for inline strings. They also make the `@app.action(...)` and `@app.view(...)` decorators self-documenting. Constitution Principle VIII — Configuration as Code — does not require action IDs to be environment variables since they are not deployment-specific.
