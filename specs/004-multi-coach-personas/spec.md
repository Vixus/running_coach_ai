# Feature Specification: Multi-Coach Personas

**Feature Branch**: `004-multi-coach-personas`  
**Created**: 2026-04-05  
**Status**: Draft  
**Input**: User description: "add a new feature that allows user to select from multiple coaches. They will have their own names and personas."

## User Scenarios & Testing _(mandatory)_

### User Story 1 - Select a Coach During Onboarding (Priority: P1)

During the onboarding flow, Claude presents the available coaches as one of its conversational intake questions, describing each by name, coaching style, and personality. The athlete picks a coach by name within the conversation. Claude includes the chosen `coach_key` in the `<onboarding_complete>{...}</onboarding_complete>` JSON tag, which is parsed alongside the rest of the athlete profile. All subsequent AI-generated messages reflect that coach's voice and tone.

**Why this priority**: Coach selection during onboarding ensures every athlete starts their journey with a personalized experience. It is the most natural entry point for the feature and delivers immediate, visible value with zero disruption to existing athletes.

**Independent Test**: Can be fully tested by running a new athlete through onboarding and verifying that the final morning check-in and training feedback messages are written in the selected coach's voice.

**Acceptance Scenarios**:

1. **Given** a new athlete has just started onboarding, **When** the system introduces available coaches, **Then** each coach's name, personality, and coaching style is described clearly enough to make an informed choice.
2. **Given** an athlete views the coach roster, **When** they select a coach by name, **Then** the system confirms the selection and continues onboarding with that coach's persona active.
3. **Given** an athlete selects a coach during onboarding, **When** subsequent messages are generated (morning check-in, feedback, plan updates), **Then** those messages reflect the selected coach's unique tone and communication style.
4. **Given** an athlete is mid-onboarding and has not yet selected a coach, **When** they complete onboarding without selecting one, **Then** a default coach is assigned and the athlete is notified which coach they received.

---

### User Story 2 - Switch Coach After Onboarding (Priority: P2)

An existing athlete, already through onboarding, decides they want a different coaching style. They send a message indicating they want to change coaches. The system presents the available coaches, the athlete picks one, and all future messages reflect the new coach's persona. Their training plan and history remain untouched.

**Why this priority**: Athletes' preferences evolve. Allowing mid-cycle coach switching improves long-term retention and satisfaction without any disruption to training continuity.

**Independent Test**: Can be fully tested by messaging the bot as an existing athlete to switch coaches, then triggering a morning check-in and verifying the new coach's voice is present.

**Acceptance Scenarios**:

1. **Given** an athlete who completed onboarding with Coach A, **When** they ask to change or switch coaches, **Then** the system lists all available coaches with descriptions.
2. **Given** the coach list is presented, **When** the athlete selects a different coach by name, **Then** the system confirms the switch and the new coach's persona is immediately active.
3. **Given** a coach switch is confirmed, **When** the athlete interacts with the bot next (any message or scheduled job), **Then** all responses use the new coach's voice and the athlete's training data is unchanged.
4. **Given** an athlete asks to switch but selects their current coach, **Then** the system acknowledges no change is needed and confirms the current coach remains active.

---

### User Story 3 - Browse Coach Profiles (Priority: P3)

An athlete (onboarded or not) can ask to see all available coaches and their descriptions without immediately committing to a selection. This lets them explore options and make a deliberate choice.

**Why this priority**: Browsing without forced selection reduces pressure and improves the quality of athlete-coach matching.

**Independent Test**: Can be fully tested by asking the bot to list coaches and verifying all coaches are presented with distinguishable names and personality descriptions.

**Acceptance Scenarios**:

1. **Given** any athlete asks to see available coaches, **When** the system responds, **Then** all available coaches are listed with their name and a concise personality description.
2. **Given** the coach roster is displayed, **When** the athlete does not select a coach, **Then** the current coach (or default if onboarding) remains active with no change.

---

### Edge Cases

- What happens if only one coach is defined? The system assigns it automatically without presenting a choice.
- What if an athlete's saved coach persona is removed from the roster? The system falls back to the default coach and notifies the athlete at next interaction.
- What happens when the athlete does not understand the coach selection prompt during onboarding? The system presents a simplified description and re-prompts once before assigning the default.
- What if the athlete tries to select a coach by a misspelled or unrecognized name? The system asks for clarification and shows the valid options.

## Requirements _(mandatory)_

### Functional Requirements

- **FR-001**: The system MUST define at least 3 distinct coach personas at launch, each with a unique name, personality description, coaching philosophy summary, and communication style.
- **FR-002**: Each coach persona MUST produce noticeably different tone and language across all AI-generated messages (morning check-ins, training feedback, weekly reviews, and conversational replies). Each persona contributes a persona block — containing the coach's name, voice traits, and style instructions — that is injected at the top of the existing `build_system_prompt()` output. All other context sections (health data, Garmin calendar, weather, memories) are shared across all coaches and remain unchanged.
- **FR-003**: Coach selection MUST be woven into Claude's onboarding conversation as one of the intake questions. Claude presents each available coach by name and personality description, and the athlete's choice is captured as `coach_key` inside the `<onboarding_complete>` JSON tag, parsed by the onboarding handler alongside all other profile fields.
- **FR-004**: Athletes MUST be able to switch coaches at any time after onboarding by expressing intent in a natural conversational message (e.g., "I want a different coach", "change my coach"). Claude detects this intent, presents available coaches, and manages the confirmation exchange entirely within the conversation.
- **FR-005**: Claude MUST confirm the athlete's coach selection before emitting the `<coach_switch>key</coach_switch>` side-effect tag. The Python conversation handler intercepts this tag to persist the new `coach_key` to the database, consistent with the existing `<plan>`, `<remember>`, and `<garmin_sync/>` tag pattern.
- **FR-006**: The system MUST persist each athlete's current coach selection so it survives bot restarts and is consistent across all scheduling jobs.
- **FR-007**: A coach switch MUST take effect immediately after the `<coach_switch>` tag is processed and `coach_key` is persisted — all subsequent interactions, including scheduler-triggered messages, use the new coach's persona block.
- **FR-008**: Switching coaches MUST NOT modify, reset, or delete the athlete's training plan, workout history, goals, or Garmin sync state.
- **FR-009**: If an athlete completes onboarding without explicitly selecting a coach, the system MUST assign a clearly defined default coach.
- **FR-010**: The system MUST allow an athlete to view all available coaches and their descriptions on demand, without being forced to switch.
- **FR-011**: When the feature is deployed, existing already-onboarded athletes MUST be silently assigned the default coach via a DB migration with no interruption to their normal interactions.

### Key Entities

- **CoachPersona**: A predefined coaching identity with a unique name, personality description, communication style traits, and system prompt instructions that shape AI output. Defined as a dataclass/dict in `coach/personas.py` — not user-created and not stored in the database.
- **Athlete** (updated): Gains a `coach_key` string column referencing a coach by its key in the `coach/personas.py` registry (e.g., `"miles"`, `"sofia"`). No foreign key — the registry is the source of truth.
- **`<coach_switch>` tag**: A new Claude side-effect tag processed by `conversation.py` alongside `<plan>`, `<remember>`, and `<garmin_sync/>`. Format: `<coach_switch>coach_key</coach_switch>`. When detected, the handler validates the key exists in the persona registry and persists it to `Athlete.coach_key`.

## Success Criteria _(mandatory)_

### Measurable Outcomes

- **SC-001**: An athlete can browse coaches, make a selection, and complete onboarding without any additional steps compared to the baseline flow � coach selection adds at most one conversational exchange to onboarding.
- **SC-002**: An athlete can switch coaches in a single conversational back-and-forth (one message expressing intent, one confirmation exchange).
- **SC-003**: All three AI-generated message categories (morning check-in, activity feedback, weekly review) demonstrably reflect the selected coach's persona, verifiable by manual review of sample outputs.
- **SC-004**: At least 3 coach personas are available at launch, each distinct enough that a non-technical reviewer can correctly identify the coach from a message without seeing a label.
- **SC-005**: Coach switching leaves training plans, goals, and Garmin sync state fully intact � zero data loss or corruption across a switch event.

## Clarifications

### Session 2026-04-05

- Q: Where are coach persona definitions stored/loaded from? → A: Python module (`coach/personas.py`) — a dict or dataclass registry imported directly at runtime; no DB table for coach definitions.
- Q: How are existing already-onboarded athletes handled when the feature ships? → A: Silent default — the migration sets the default coach key on all existing athletes with no notification; they can switch later via natural conversation.
- Q: How does a coach persona integrate with the existing prompt pipeline? → A: Persona block injection — each coach defines a persona section (name, voice, style instructions) inserted at the top of the existing `build_system_prompt()` output; all other context sections (health, Garmin, weather, etc.) remain unchanged.
- Q: Where in the onboarding sequence does coach selection happen? → A: Woven into Claude's onboarding conversation — Claude presents available coaches as one of its intake questions; `coach_key` is included in the `<onboarding_complete>` JSON tag and parsed alongside the rest of the profile.
- Q: How is a post-onboarding coach switch confirmed and committed? → A: Claude emits `<coach_switch>key</coach_switch>` — Claude manages the full conversational flow (presenting coaches, confirming selection) and emits this side-effect tag; the Python conversation handler intercepts it to persist the new `coach_key` to the DB, consistent with the existing `<plan>`, `<remember>`, and `<garmin_sync/>` tag pattern.

## Assumptions

- Coach personas are defined and maintained in a Python module (`coach/personas.py`) by the operator — athletes cannot create or customize coaches.
- Each athlete has exactly one active coach at any given time; multi-coach support is out of scope.
- Coach persona differences are limited to the persona block (name, voice, style instructions) injected at the top of `build_system_prompt()` — the underlying workout scheduling logic, training plan structure, health data analysis, and all other prompt context sections are identical across all coaches.
- Switching coaches mid-training-cycle is supported without requiring plan regeneration.
- Existing athletes at deploy time receive the default coach silently via DB migration; no opt-in prompt is shown.
- The Slack interface remains the sole interaction channel; no separate coach selection UI is required.
- Coach personas will be designed such that they are inclusive, supportive, and appropriate for all fitness levels; offensive or demotivating personas are excluded by design.
- The onboarding flow can accommodate one additional conversational step without exceeding user patience or session timeout constraints.

---

## Implementation Status (as of 2026-04-23)

**Shipped — 100%.** All 19 tasks in `tasks.md` marked complete. Coach registry lives in `running_coach_ai/coach/personas.py` with three personas: `classic` (Coach Alex), `maya` (Coach Maya), `jordan` (Coach Jordan). The `coach_key` column on `Athlete` (migration `b3f2a1c9d0e5_add_coach_key_to_athletes`) stores the selection; Claude applies the persona at system-prompt assembly time in `slack/conversation.py:build_system_prompt()`.

A subsequent extension (spec 005, web dashboard) adds an additional per-athlete "prescription style" column (`j6f7g8h9i0j1_add_prescription_style_to_athletes`) that composes with the coach persona — not covered by this spec.

Mid-cycle coach switching is implemented via a Claude-emitted `<coach_switch>` tag handled in `conversation.py`; the switch persists to `athletes.coach_key` and takes effect immediately on the next turn and for all scheduled jobs.
