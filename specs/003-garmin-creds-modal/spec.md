# Feature Specification: Garmin Credentials via Slack Modal

**Feature Branch**: `003-garmin-creds-modal`  
**Created**: 2026-04-05  
**Status**: Draft  
**Input**: User description: "Replace Garmin credential collection in Slack chat with a Slack Block Kit modal so credentials never appear in the chat history or message log."

## User Scenarios & Testing _(mandatory)_

### User Story 1 - Athlete Submits Garmin Credentials via Modal (Priority: P1)

A new athlete completes the conversational onboarding with the coaching bot. At the end, instead of typing their Garmin Connect email and password into the chat (where they would appear in the message log), they receive a button in the chat. Clicking the button opens a pop-up form inside Slack where they enter their Garmin email and password. Submitting the form triggers the bot to connect their Garmin account and finalize onboarding — without any credential ever appearing in the chat thread.

**Why this priority**: This is the core security improvement. All other stories depend on this flow working end-to-end.

**Independent Test**: Can be fully tested by completing a new athlete onboarding and verifying that (a) no Garmin credentials appear in the Slack message history, and (b) Garmin workouts appear on the athlete's Garmin Connect calendar afterward, confirming credentials were accepted and onboarding completed successfully.

**Acceptance Scenarios**:

1. **Given** an athlete has just confirmed their training profile in the onboarding chat, **When** the bot sends the credential button message, **Then** the chat does not contain any prompt asking for email or password, and the button message is visible.
2. **Given** the credential button message is visible, **When** the athlete clicks the button, **Then** a modal dialog opens inside Slack with an email field and a password field.
3. **Given** the modal is open with valid Garmin email and password, **When** the athlete submits the modal, **Then** the modal closes and the bot immediately sends an acknowledgement DM (e.g., "Got it! Setting up your account, this may take a minute…"). When processing finishes, the bot sends the week 1 training summary DM.
4. **Given** the athlete submits the modal, **When** the form is processed, **Then** no message containing the Garmin email or password is posted to any Slack channel or DM.

---

### User Story 2 - Athlete Sends Chat Message While Awaiting Credential Submission (Priority: P2)

An athlete has confirmed their profile and received the button message, but sends a follow-up chat message before clicking the button (e.g., they're confused, or they close Slack and re-open it).

**Why this priority**: Without handling this state, the bot would try to resume the Claude conversation with incomplete data, potentially breaking the onboarding flow.

**Independent Test**: Can be tested by completing the onboarding conversation up to the button step, then sending a chat message instead of clicking the button, and verifying the bot replies with a reminder pointing back to the button.

**Acceptance Scenarios**:

1. **Given** an athlete is in the "awaiting Garmin credentials" state, **When** they send any chat message, **Then** the bot replies with a reminder to use the button and does not prompt Claude or advance the onboarding step.
2. **Given** an athlete is in the "awaiting Garmin credentials" state and they lost the button message, **When** they send any chat message, **Then** the bot re-sends the button message so they can still proceed.

---

### User Story 3 - Existing Athlete Onboarding is Unaffected (Priority: P3)

Athletes who have already completed onboarding before this feature is deployed continue to use the bot normally. No disruption to their scheduled check-ins, plan management conversations, or Garmin sync.

**Why this priority**: Backward compatibility is important for operational continuity, but it's lower risk than new onboarding since existing data is already in place.

**Independent Test**: Can be tested by verifying that an already-onboarded athlete can send a message and receive a normal coaching response, with no modal or button appearing.

**Acceptance Scenarios**:

1. **Given** an athlete with `onboarding_complete = true`, **When** they send a DM to the bot, **Then** the bot responds with a normal coaching reply — no modal, no button.

---

### Edge Cases

- What happens if the athlete submits the modal with an incorrect Garmin password? The bot should detect the authentication failure and send a chat message explaining the credentials were not accepted, with instructions to try again (the button message should be re-sent).
- What happens if the athlete clicks the button after their pending profile data has expired or been cleared? The bot should send a message explaining that the session has expired and prompt them to restart onboarding. Pending profile data expires after 24 hours.
- Athletes who were mid-onboarding under the old chat-based credential flow at the time of deployment will be manually reset by the admin (conversation history cleared, `onboarding_complete` reset to false) so they restart onboarding from scratch using the new modal flow. No automated migration path is required.
- What happens if the athlete clicks the credential button multiple times? Each click opens a fresh modal. Submitting any one of them completes onboarding. If a second submission arrives after `onboarding_complete` is already `true`, it is silently discarded with no message to the athlete.
- What happens if Garmin authentication succeeds but plan generation or Garmin sync fails? The bot should inform the athlete and allow the admin to trigger a re-sync (`!admin resync-garmin`) without requiring the athlete to re-submit credentials.
- The admin MUST be able to recover a stuck athlete (wrong credentials, expired session, any fatal error) using `!admin reset-onboarding <uid>`, which clears `pending_onboarding_data`, resets conversation history, and sets `onboarding_complete = false` so the athlete can restart from scratch.

## Requirements _(mandatory)_

### Functional Requirements

- **FR-001**: The onboarding conversation MUST NOT ask the athlete for their Garmin Connect email or password at any point in the chat.
- **FR-002**: The bot MUST send a button message to the athlete's chat once they confirm their training profile, prompting them to enter Garmin credentials.
- **FR-003**: Clicking the button MUST open a Slack modal containing an email input field and a password input field.
- **FR-004**: The modal MUST display a disclaimer noting that the password field does not visually mask input and advising the athlete to type in private.
- **FR-005**: Submitting the modal MUST trigger onboarding completion: Garmin authentication, historical activity import, plan generation, and Garmin calendar sync.
- **FR-005a**: The bot MUST send an immediate acknowledgement DM the moment the modal is submitted (before any background processing begins), so the athlete knows their credentials were received.
- **FR-005b**: Upon successful completion, the bot MUST send the existing week 1 training summary DM as the final confirmation.
- **FR-006**: The Garmin email and password submitted via modal MUST NOT be posted or echoed to any Slack channel, DM, or message log.
- **FR-007**: The system MUST persist the athlete's completed training profile between the end of the Claude conversation and the modal submission (i.e., profile data collected in chat must not be lost while waiting for credentials).
- **FR-007a**: Pending profile data MUST expire automatically after 24 hours. If the athlete clicks the credential button or submits the modal after expiry, the bot MUST inform them the session has expired and prompt them to restart onboarding.
- **FR-008**: If the athlete sends a chat message while in the "awaiting credentials" state, the bot MUST reply with a reminder and re-send the button message.
- **FR-009**: The system MUST handle a Garmin authentication failure (wrong credentials) by notifying the athlete in chat and re-presenting the credential button.
- **FR-010**: Athletes who have already completed onboarding MUST NOT be affected by this change; their existing bot interactions MUST remain unchanged.
- **FR-011**: The admin command `!admin reset-onboarding <uid>` MUST clear the athlete's `pending_onboarding_data`, delete their `ConversationMessage` history, and set `onboarding_complete = false`, returning them to the start of the onboarding flow.
- **FR-012**: If the Garmin credentials modal view handler receives a submission for an athlete whose `onboarding_complete` is already `true`, it MUST silently discard the submission without sending any message or re-processing.

### Key Entities

- **Athlete**: Existing entity representing an onboarding or active athlete. Gains a new "pending profile data" field that holds the training profile between profile confirmation and credential submission.
- **Pending Onboarding Data**: The structured training profile (name, age, race goal, distances, training days, city, etc.) captured by the onboarding conversation, stored temporarily until Garmin credentials are submitted and onboarding can be completed. Expires 24 hours after creation; cleared on successful completion or expiry.

## Success Criteria _(mandatory)_

### Measurable Outcomes

- **SC-001**: Zero Garmin credential strings (email addresses or passwords) appear in the Slack message history for any athlete who onboards after this feature is deployed.
- **SC-002**: An athlete can complete the full onboarding flow — from first contact to Garmin workouts appearing on their calendar — without any coach or admin intervention, in a single self-service session.
- **SC-003**: An athlete who sends a chat message while awaiting credential submission receives a bot reminder within the same response cycle (no manual recovery needed).
- **SC-004**: All existing unit tests for `_complete_onboarding`, timezone derivation, and Garmin sync pass without modification after this change is deployed.

## Clarifications

### Session 2026-04-05

- Q: What should happen to athletes mid-onboarding (old chat flow) when this feature is deployed? → A: Admin manually resets them — clear their conversation history and set onboarding_complete = false so they restart under the new modal flow.
- Q: Should there be a time limit on how long pending profile data is held while waiting for the Garmin modal submission? → A: TTL of 24 hours — pending data expires after 24 hours and is treated as invalid; the athlete must restart onboarding. The data is only physically cleared on successful completion or via `!admin reset-onboarding`; an expired row is not automatically deleted.
- Q: What should the athlete see while onboarding completion runs in the background after modal submission (30–60 seconds)? → A: An immediate acknowledgement DM is sent the moment the modal is submitted, followed by the existing week 1 summary DM when processing finishes.
- Q: Should the admin `!admin` command set gain any new capability to handle stuck athletes (wrong credentials, expired state)? → A: Add `!admin reset-onboarding <uid>` — clears pending profile data, resets conversation history, sets onboarding_complete = false, so the athlete restarts from scratch.
- Q: What should happen if a duplicate modal submission arrives while the first is still processing? → A: Check `onboarding_complete` at the start of the view handler — if already `true`, silently discard the duplicate (no message to athlete).

## Assumptions

- Athletes use the Slack mobile or desktop client and can interact with Slack modals (Block Kit interactive components).
- Athletes have Garmin Connect accounts with multi-factor authentication (MFA) disabled, as the system cannot automate MFA — this constraint is unchanged from the existing system.
- The Slack bot is connected via SocketMode (already true), which supports interactive component payloads including `block_actions` and `view_submission` events.
- The Slack `plain_text_input` field type does not support native password masking; this is an inherent Slack platform limitation and is acceptable given that the credential is not transmitted through chat messages.
- The "pending profile data" state is temporary and scoped to the onboarding flow only; it is cleared once onboarding completes or fails fatally.
- Garmin authentication errors during modal submission are communicated to the athlete via a follow-up DM, not within the modal response itself (Slack modals have limited error display capabilities during async operations).
