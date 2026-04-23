# Feature Specification: AI Running Coach

**Feature Branch**: `001-ai-running-coach`
**Created**: 2026-03-31
**Status**: Draft

## Overview

An AI-powered personal running coach that monitors athlete health and performance data, builds and maintains adaptive training plans, and communicates with athletes via conversational messaging. Supports multiple independent athletes — each with their own data, training plan, conversation history, and coach memory — all served by a single self-hosted instance.

The coach persona is a veteran endurance sport expert with deep knowledge of modern training methodologies. Its primary directive is to help each athlete achieve their goal without excessive stress or injury risk.

---

## Clarifications

### Session 2026-03-31

- Q: What is the maximum acceptable response latency for real-time coaching conversation replies? → A: Under 10 seconds from message received to reply sent.
- Q: When an athlete is removed by an admin, what happens to their stored data? → A: Retain all data but revoke access — athlete can be re-added without re-onboarding.
- Q: Can an athlete have multiple concurrent active goals (e.g., a tune-up 10K alongside a marathon plan)? → A: Yes — multiple concurrent goals are supported.
- Q: If onboarding is interrupted mid-conversation, should the system resume or restart when the athlete returns? → A: Resume from the last successfully answered question — no repeated questions.
- Q: How should the system behave when the fitness API returns a rate-limit response? → A: Back off with a delay and retry up to 3 times; skip the operation if its time window has passed.

### Session 2026-04-04 (Implementation Sync)

- Q: What is the accepted admin command surface in a socket-mode-only architecture? → A: Admin commands are sent as DM text with the `!admin` prefix; slash commands are out of scope for this version.
- Q: How is onboarding resume made deterministic? → A: Persist progress in `athlete.onboarding_step` after each confirmed answer and use it as the resume gate on re-entry.

### Session 2026-04-04 (Morning Check-In Polling)

- Q: When the 30-min polling window closes (10:00am) with no health data, what happens? → A: Skip — no morning message sent that day.

## Iterations

### Iteration 2026-04-04: PlannedWorkout Sync and Audit Timestamps

**Change**: Add PlannedWorkout timestamp requirements for Garmin sync tracking and row lifecycle auditing.
**Scope**: medium
**Artifacts updated**: spec.md, data-model.md, plan.md, tasks.md
**Tasks added**: T070, T071, T072, T073, T074
**Tasks removed**: None
**Tasks marked complete**: None

### Iteration 2026-04-04: Morning Check-In Health-Gated Polling

**Change**: Replace the one-shot 7:00am cron with a 30-minute polling loop that waits until Garmin health data is present before sending the DM; silently skips for the day if no sleep, HRV, or body battery data arrives by 10:00am athlete local time.
**Scope**: Phase-level — User Story 3 (Morning Check-In)
**Artifacts updated**: spec.md, data-model.md, tasks.md
**Tasks added**: T076, T077, T078, T079, T080
**Tasks removed**: None
**Tasks marked complete**: None

---

## User Scenarios & Testing _(mandatory)_

### User Story 1 - Athlete Onboarding (Priority: P1)

A new athlete sends a message to the coaching bot for the first time. The coach welcomes them and conducts a structured intake conversation to collect their profile — name, age, target race, goal time, current training volume, available training days, injury history, location, and credentials to access their fitness device data. After confirming the collected information, the coach generates a personalised multi-week training plan and delivers a first-week summary. The athlete's training schedule is immediately visible on their fitness device.

**Why this priority**: Onboarding is the prerequisite for all other features. Without a completed athlete profile, no coaching, plan generation, or daily monitoring can occur. It establishes the coach-athlete relationship and captures all data needed to make the system useful.

**Independent Test**: Can be fully tested by registering a new athlete through the messaging interface, completing the intake conversation, and confirming that a training plan is generated and pushed to the athlete's fitness device — delivering the core value of a personalised plan with zero manual effort.

**Acceptance Scenarios**:

1. **Given** a new user sends any message to the coach bot, **When** they are on the allowed-users list, **Then** the coach greets them as a veteran coach and initiates the intake questionnaire in a natural, conversational tone.
2. **Given** the intake conversation is in progress, **When** the athlete provides an answer to each question, **Then** the coach acknowledges it and moves to the next question without repeating already-answered items.
3. **Given** the onboarding flow asks for athlete profile details, **When** experience level is collected, **Then** it is captured as one of `beginner`, `intermediate`, or `advanced` and used during plan generation.
4. **Given** an onboarding conversation was interrupted, **When** the athlete sends a new message at any later time, **Then** the coach resumes from the first unanswered question, briefly acknowledging where they left off.
5. **Given** all intake questions have been answered, **When** the coach summarises the athlete profile, **Then** the athlete can confirm or correct details before the plan is generated.
6. **Given** the athlete confirms their profile, **When** plan generation is triggered, **Then** the coach produces a 16–20 week week-by-week training plan appropriate for the athlete's goal, current fitness, and available training days.
7. **Given** a plan is generated, **When** the first week begins, **Then** the training sessions are visible on the athlete's fitness device calendar on the correct dates.
8. **Given** a user messages the bot who is NOT on the allowed-users list, **When** any message is received, **Then** the bot politely declines and explains that access is invite-only.

---

### User Story 2 - Free-Form Coaching Conversation (Priority: P1)

An onboarded athlete can message the coach at any time, in natural language, about any running or training topic. The coach responds as a knowledgeable, warm, experienced coach who knows the athlete's full history — their recent runs, health signals, training plan, personal preferences, and injury background. No menus, commands, or special phrasing are required. The conversation feels like texting a real coach.

**Why this priority**: This is the core differentiating feature. The value proposition is a coach you can actually talk to — not a scheduling tool or a dashboard. All other automated features enhance this, but the conversation is the product.

**Independent Test**: Can be fully tested by an onboarded athlete sending a varied set of natural language messages (logistics change, injury concern, motivation question, training curiosity) and verifying that the coach responds appropriately and accurately, referencing the athlete's real data.

**Acceptance Scenarios**:

1. **Given** an onboarded athlete sends a logistics request ("Can we move my long run to Saturday?"), **When** the coach processes the message, **Then** it updates the training plan and confirms the change naturally without requiring the athlete to use any special commands.
2. **Given** an athlete describes a health concern ("My left knee has been niggly"), **When** the coach responds, **Then** it asks clarifying follow-up questions before giving advice, and recommends rest or physio referral if warranted.
3. **Given** an athlete asks a training knowledge question ("Why are we doing so many easy runs?"), **When** the coach responds, **Then** it gives a clear, personalised answer grounded in the athlete's current plan phase without jargon or condescension.
4. **Given** a conversation is ongoing, **When** the coach references a recent run, **Then** it cites specific data from that run (e.g., average pace, HR behaviour) rather than speaking generically.
5. **Given** the coach decides to modify the training plan based on conversation, **When** the change is made, **Then** the athlete's fitness device is updated automatically in the background, and the coach mentions the change naturally in the response.
6. **Given** the coach observes a notable biomechanical pattern (e.g., cadence consistently dropping under fatigue), **When** it responds, **Then** it saves this observation to long-term memory so it persists across future conversations.

---

### User Story 3 - Daily Morning Check-In (Priority: P2)

Each morning, the coach automatically reviews overnight health data (sleep quality, heart rate variability, resting heart rate, body battery) and the day's weather forecast, then decides whether the planned training session is appropriate. If adjustment is needed, it modifies the plan. It then sends the athlete a personalised morning message in coach voice: today's session, any adjustments made, a weather note, and an encouraging cue.

**Why this priority**: This is the primary touchpoint of the coaching relationship — the morning message is when athletes feel they have a coach watching over their training. It also prevents overtraining by using real health signals to gate session intensity daily.

**Independent Test**: Can be fully tested by simulating the polling sequence: (1) fire a poller tick before 10:00am with no sleep, HRV, or body battery data — verify no DM is sent and `last_morning_checkin_date` is NOT set; (2) fire a second tick after health data becomes available — verify the coach downgrades the planned high-intensity session (low HRV signal), sends an appropriate morning DM in coach voice, and `last_morning_checkin_date` is set to today; (3) fire a third tick same day — verify it is an immediate no-op with no Garmin call.

**Acceptance Scenarios**:

1. **Given** it is morning and an athlete has poor health signals (low HRV, poor sleep), **When** the daily check-in runs, **Then** the coach downgrades or replaces the planned session with something easier or a rest day.
2. **Given** it is morning and the forecast shows extreme weather, **When** the daily check-in runs, **Then** the coach adjusts session type or timing to account for unsafe or uncomfortable conditions.
3. **Given** health signals and weather are both good, **When** the daily check-in runs, **Then** the coach sends an encouraging message confirming the planned session without modification.
4. **Given** the morning check-in sends a Slack message, **When** a plan change was made, **Then** the message explains the change in coach voice (e.g., "Given your HRV is down today, I've swapped the intervals for an easy 40 minutes — your body needs the recovery more than the quality right now").
5. **Given** multiple athletes are registered, **When** the morning check-in runs, **Then** each athlete receives a fully independent, personalised message based solely on their own data.
6. **Given** no sleep, HRV, or body battery data has arrived on Garmin by 10:00am athlete local time, **When** the morning poller fires after that cutoff, **Then** the system MUST NOT send a morning check-in message for that day and MUST log the skip at INFO level.

---

### User Story 4 - Post-Run Feedback (Priority: P2)

After an athlete completes a run, the coach detects it automatically (no manual logging needed), retrieves the full activity data including detailed biomechanical telemetry, analyses performance against the planned session, and sends targeted feedback via the messaging interface within 10 minutes. The feedback goes beyond pace and heart rate — it comments on form signals, pacing strategy, and specific observations from the activity data.

**Why this priority**: Post-run feedback is when athletes learn and improve. The ability to give biomechanical insights (not just "good run!") based on real time-series data is a major differentiator over generic coaching apps.

**Independent Test**: Can be fully tested by completing a run on a registered athlete's device and verifying that a feedback message arrives within 10 minutes, contains specific data-driven observations (not generic), and accurately compares planned vs actual.

**Acceptance Scenarios**:

1. **Given** a registered athlete completes a run, **When** the activity syncs to their fitness account, **Then** the coach detects it automatically within 10 minutes during active hours (06:00–22:00).
2. **Given** a new activity is detected, **When** the coach analyses it, **Then** it compares actual performance to the planned session and notes any significant deviations.
3. **Given** activity data includes biomechanical signals, **When** the coach generates feedback, **Then** it references specific observations such as cadence patterns, heart rate behaviour over time, and effort distribution across the run — by name and with specific numbers.
4. **Given** a biomechanical trend is observed across multiple sessions (e.g., cadence consistently dropping in the final km), **When** the coach generates feedback, **Then** it notes this pattern and stores it in long-term memory for use in future advice.
5. **Given** an unplanned activity is detected (no matching session in the training plan), **When** the coach provides feedback, **Then** it still gives useful analysis while noting the session was outside the scheduled plan.

---

### User Story 5 - Weekly Review and Plan Adaptation (Priority: P3)

At the end of each training week, the coach reviews the athlete's full week: what was planned, what was completed, overall training load, fitness trends, and any notable patterns. It then generates a summary and sends it to the athlete. If the athlete is ahead of or behind the target training stimulus, the coach adjusts the following week accordingly and uploads the revised sessions to the fitness device.

**Why this priority**: Weekly adaptation is what makes the plan "live" over a multi-month training cycle. It prevents the common failure mode of a static plan becoming misaligned with reality.

**Independent Test**: Can be fully tested by simulating a week where the athlete missed two sessions, then verifying the Sunday review message accurately reflects the shortfall and the following week's plan is appropriately adjusted (not blindly following the original).

**Acceptance Scenarios**:

1. **Given** a training week ends, **When** the weekly review runs, **Then** the coach generates a summary of: km completed vs planned, quality sessions hit, load trend, and any notable fitness or health indicators.
2. **Given** an athlete completed significantly less training than planned, **When** the next week is generated, **Then** the coach moderates the upcoming week's load to avoid overcorrection.
3. **Given** an athlete completed all sessions and health signals show positive adaptation, **When** the next week is generated, **Then** the coach may incrementally increase load appropriately.
4. **Given** the next week's sessions are finalised, **When** the weekly review is complete, **Then** the upcoming sessions are uploaded to the athlete's fitness device and a summary of the coming week is included in the review message.

---

### User Story 6 - Admin User Management (Priority: P3)

An admin user can add or remove athletes from the allowed-users list at runtime by sending commands to the coaching bot. No redeployment or config file editing is needed to grant or revoke access.

**Why this priority**: Access management is a housekeeping feature. It's necessary for running a private multi-athlete service, but it doesn't deliver coaching value itself.

**Independent Test**: Can be fully tested by having the admin add a new Slack user ID via chat command, verifying that user can now complete onboarding, then removing them and verifying they are politely declined on next message.

**Acceptance Scenarios**:

1. **Given** the admin sends an add-user command with a valid user identifier, **When** the command is processed, **Then** the user is added to the allowed list and can begin onboarding immediately without any restart or deployment.
2. **Given** the admin sends a remove-user command, **When** the command is processed, **Then** that user's access is revoked and subsequent messages from them are declined politely — their data is retained and they can be re-added without re-onboarding.
3. **Given** a non-admin user attempts to use admin commands, **When** the command is received, **Then** it is silently ignored or politely declined without revealing admin functionality.
4. **Given** the admin sends `!admin morning-checkin [<uid>]`, **When** the command is processed, **Then** the morning check-in job runs for that athlete immediately, respecting the same-day dedup guard to prevent double-send.
5. **Given** the admin sends `!admin morning-checkin [<uid>] --force`, **When** the command is processed, **Then** `last_morning_checkin_date` is cleared first, so a fresh check-in message is sent even if one was already delivered today.

---

### Edge Cases

- What happens when the athlete's fitness device sync is delayed beyond the normal polling window? The coach should not send feedback for activities detected outside active hours until the next morning.
- What happens if the athlete completes a workout that bears no resemblance to the planned session (e.g., runs 20 km when a 5 km easy was planned)? The coach should comment on this mismatch honestly and consider whether to adjust the plan.
- What happens when an athlete changes their race goal mid-plan? The coach should regenerate or significantly revise the remaining plan to align with the new target.
- What happens if two athletes' morning check-ins run concurrently? Each must be fully isolated — no data from one athlete can appear in another's context or message.
- What happens when the fitness API is unavailable or returns a rate-limit response? The system backs off with a delay and retries up to 3 times. If the operation's time window has passed after retries are exhausted, it is skipped and logged — the system resumes normally at the next scheduled opportunity without crashing.
- What happens when an athlete's message is ambiguous (e.g., "I don't feel great")? The coach must ask a follow-up question to understand the situation before giving advice.
- What happens when an athlete is in the taper phase and asks to add more training? The coach should educate them on the purpose of taper and strongly advise against increasing load.
- What happens when an athlete adds a second goal whose plan would require sessions on days already committed by an existing plan? The coach must negotiate the schedule — either merging compatible sessions, displacing lower-priority sessions, or flagging the conflict to the athlete if no safe resolution exists.
- **No Watch to Bed**: If Garmin has not published any sleep, HRV, or body battery data by 10:00am athlete local time, the system skips the morning check-in entirely for that day and logs INFO. No degraded or partial message is sent.
- **Duplicate Send Guard**: If a morning check-in has already been sent today (as recorded in `athletes.last_morning_checkin_date`), all subsequent 30-minute poller ticks for that athlete MUST be immediate no-ops — preventing double-delivery if data arrives after the first successful send.

---

## Requirements _(mandatory)_

### Functional Requirements

**Athlete Management**

- **FR-001**: The system MUST maintain fully isolated data for each athlete — their training plan, workout history, health data, coach memory, and conversation history must never be accessible to or visible in another athlete's context.
- **FR-002**: The system MUST support an invite-only access model, declining all unapproved users with a clear, polite message.
- **FR-003**: An authorised admin MUST be able to add or remove athletes from the allowed list at runtime via chat commands, without redeployment. When an athlete is removed, their data MUST be retained in full — only access is revoked. A removed athlete can be re-added and resume without repeating onboarding.
- **FR-004**: The system MUST trigger a guided onboarding conversation for any approved user who has not yet completed intake. If onboarding is interrupted, the system MUST resume from the last successfully answered question when the athlete next messages — no previously answered questions are repeated. Resume state MUST be persisted in hard state (`onboarding_step`) rather than inferred solely from conversation history.
- **FR-005**: Athlete credentials for their fitness account MUST be stored encrypted at rest and never transmitted in plaintext.

**Training Plan**

- **FR-006**: The system MUST generate a personalised multi-week training plan for each athlete upon completing onboarding, informed by their goal, fitness level, available training days, and any existing health data from their device. An athlete MAY have multiple concurrent active goals (e.g., a tune-up race alongside a primary marathon plan); when goals overlap, the coach MUST resolve scheduling conflicts such that sessions from multiple plans do not stack on the same day in a way that would compromise recovery or exceed safe weekly load. A deterministic post-generation conflict-resolution pass MUST run before plan persistence and Garmin sync.
- **FR-007**: The training plan MUST include distinct training phases: base building, build, peak, and taper — appropriate in length to the race date.
- **FR-008**: The system MUST support the following session types: easy run, long run, tempo, intervals, strides, rest, and cross-training.
- **FR-009**: The system MUST upload planned training sessions to the athlete's fitness device calendar on the correct scheduled dates.
- **FR-010**: The system MUST update sessions on the fitness device whenever a plan change is made — including removing superseded sessions and uploading revised ones — and MUST set an explicit per-session sync timestamp when the replacement sync succeeds.

**Data Ingestion**

- **FR-011**: The system MUST automatically read daily health data for each athlete (sleep score, heart rate variability, resting heart rate, body battery, stress level, step count) without requiring the athlete to manually export or share it.
- **FR-012**: The system MUST automatically detect completed activities on each athlete's fitness account and retrieve full performance data including detailed time-series biomechanical streams.
- **FR-013**: Activity detection MUST occur regularly during active hours without requiring the athlete to notify the coach.
- **FR-014**: The system MUST gracefully handle missing data streams — when a metric is unavailable from a device, it must be skipped without causing errors.

**Coach Intelligence**

- **FR-015**: The coach MUST respond to all athlete messages in a natural, conversational tone consistent with a veteran endurance coach — warm, direct, evidence-based, and free of jargon or condescension.
- **FR-016**: The coach MUST assemble full context on every conversation turn: athlete profile, active plan phase, recent workouts, current health signals, and upcoming weather.
- **FR-017**: The coach MUST maintain long-term memory of important athlete-specific facts (injuries, preferences, observed patterns) that persists beyond the rolling conversation window.
- **FR-018**: The coach MUST evaluate daily health signals each morning and automatically adjust that day's session if fatigue, recovery, or weather indicators suggest a different approach is safer or more beneficial.
- **FR-019**: The coach MUST generate post-run feedback that includes biomechanical observations — not just aggregate stats — with specific references to patterns observed in the activity data.
- **FR-020**: The coach MUST silently apply plan mutations triggered by conversation (e.g., rescheduling a session) without requiring the athlete to use any special commands or confirm a structured workflow.
- **FR-021**: The coach MUST generate a weekly summary for each athlete and adapt the following week's plan based on actual training load vs planned load.
- **FR-022**: The coach MUST factor weather forecasts into session recommendations, applying defined adjustment rules for extreme temperatures, precipitation, wind, and optimal conditions.
- **FR-023**: The coach MUST never prescribe training loads that pose a clear injury risk, and MUST flag injury warning signs with advice to seek professional assessment.

**Biomechanical Analysis**

- **FR-024**: After each completed workout, the system MUST analyse available time-series telemetry streams to derive biomechanical insights: cadence consistency, heart rate drift, aerobic decoupling, ground contact trends, vertical oscillation, and effort distribution across the run.
- **FR-025**: The system MUST maintain a running biomechanical profile for each athlete, updated after each workout, tracking 30-day rolling averages and trend direction for key metrics. At minimum this includes cadence, heart-rate drift, heart-rate/pace decoupling, and easy-zone compliance.
- **FR-026**: The coach MUST reference the athlete's biomechanical profile when generating feedback and training advice, including trend direction (improving, stable, declining) for each tracked key metric.

**System Reliability**

- **FR-027**: The system MUST run continuously without requiring manual intervention, recovering automatically from transient errors in external services. When the fitness API returns a rate-limit response, the system MUST apply exponential backoff and retry up to 3 times before giving up. If the operation's time window has passed (e.g., the morning check-in window), the system MUST skip that operation rather than execute it with stale data, and log the skipped event.
- **FR-028**: The system MUST produce structured logs for all significant events to support diagnosis and monitoring.
- **FR-029**: The system MUST persist PlannedWorkout lifecycle audit timestamps: `created_at` at row creation, `updated_at` on each mutation, and `last_garmin_synced_at` only after a successful Garmin upload or resync for that workout.

**Morning Check-In Polling**

- **FR-030**: The system MUST register a per-athlete morning check-in job using `IntervalTrigger(minutes=30)` starting at 07:00 athlete local time, replacing the previous one-shot `CronTrigger`.
- **FR-031**: The system MUST consider health data present when at least one of `sleep_score`, `hrv_score`, or `body_battery_start` is non-null in the parsed `HealthSnapshot` for today.
- **FR-032**: The system MUST skip sending the morning DM if athlete local time is past 10:00am and health data is still unavailable; it MUST log this skip at INFO level and MUST NOT send any message for that calendar day.
- **FR-033**: The system MUST NOT send more than one morning check-in DM per athlete per calendar day. On successful send, the system MUST record the date in `athletes.last_morning_checkin_date` and treat all subsequent same-day poller ticks as no-ops.

**Data Model Additions**

- **FR-034**: The system MUST store a `workout_name` (nullable Text) on each `PlannedWorkout`. When present, this string is used as the `workoutName` field uploaded to Garmin Connect and may be included in `<plan>` JSON tags.
- **FR-035**: The system MUST store an `activity_type` (nullable Text, Garmin `typeKey`, e.g. `"running"`, `"hiking"`) on each `CompletedWorkout` so that non-running activities can be separated from running workouts in context assembly and weekly load calculations.
- **FR-036**: The system MUST store `lthr_bpm` (nullable Integer) on each `Athlete` to record their Lactate Threshold Heart Rate derived from Garmin device profiles. This value is used for zone-based training intensity calculations.
- **FR-037**: The system MUST store `training_readiness` (nullable Integer, 0–100) in each daily `HealthSnapshot` when Garmin publishes it. The morning check-in and conversation context assembler MUST include it alongside HRV and sleep data.

**Admin Operations**

- **FR-038**: The admin MUST be able to trigger a manual morning check-in for any athlete via `!admin morning-checkin [<uid>]`. The command respects the same-day dedup guard. Adding `--force` clears `last_morning_checkin_date` first, allowing the check-in to run even if already sent today.

**Observability**

- **FR-039**: When `LOG_FILE` is set in the environment, the system MUST additionally write structured logs to a rotating file (10 MB max, 5 backups) at that path, in addition to stdout. This supplements FR-028 for NAS deployments where persistent log files are required.

### Key Entities

- **Athlete**: A registered individual with a unique identity, fitness account credentials, home location, and onboarding status. Each athlete is fully isolated from all others.
- **Goal**: The athlete's target race event, desired finish time, current fitness baseline, experience level, and available training days. An athlete may have multiple concurrent active Goals, each with its own associated Training Plan. Goals are independent but their plans must be coordinated to avoid unsafe combined training load.
- **Training Plan**: A structured week-by-week schedule of sessions generated for a specific goal, covering from creation date to race day. Includes training phase information.
- **Planned Session**: A single scheduled training session within a plan — typed, described, with target effort parameters and current status (planned, completed, skipped, modified).
- **Completed Activity**: A recorded workout retrieved from the athlete's fitness device, with performance summary metrics and status relative to the plan.
- **Activity Telemetry**: Full time-series data for a completed workout — biomechanical streams sampled at device rate, plus lap-by-lap breakdowns.
- **Biomechanical Profile**: A derived, continuously-updated summary of the athlete's form metrics and running trends — cadence norms, heart rate drift patterns, zone compliance, and coach-written observations.
- **Health Snapshot**: A daily record of overnight and daytime health indicators for each athlete.
- **Conversation Message**: A stored message (athlete or coach) forming the rolling conversation history used as context for each reply.
- **Coach Memory**: Long-term, categorised facts about an athlete that the coach retains across the rolling conversation window — injuries, preferences, behavioural patterns, goal notes.

---

## Success Criteria _(mandatory)_

### Measurable Outcomes

- **SC-001**: An athlete completes full onboarding — from first message to a training plan visible on their fitness device — in a single conversation session with no manual data entry on the athlete's part beyond answering the coach's questions.
- **SC-002**: Post-run feedback is delivered within 10 minutes of an activity syncing to the athlete's account during active hours (06:00–22:00).
- **SC-003**: The coach's morning check-in message is sent within 30 minutes of Garmin health data (sleep score, HRV, or body battery) becoming available after 07:00 athlete local time. If no health data arrives by 10:00am athlete local time, no message is sent that day.
- **SC-004**: The coach correctly identifies and responds appropriately to at least 4 out of 5 distinct conversation intents (logistics change, injury concern, motivation, training curiosity, performance reflection) in a test scenario without any special phrasing from the athlete.
- **SC-005**: When a plan modification is triggered by conversation, the athlete's fitness device reflects the updated sessions within 2 minutes of the coach's response.
- **SC-006**: Athlete data remains fully isolated — no test or audit scenario can retrieve one athlete's data in another athlete's conversation context or morning message.
- **SC-007**: The system recovers automatically from a fitness API outage without crashing, and resumes normal data fetching at the next scheduled window without manual intervention.
- **SC-008**: Long-term coach memory items persist correctly across conversation sessions — facts saved in one session are referenced appropriately in future sessions days later.
- **SC-009**: The weekly plan adaptation correctly reduces the following week's load after a week where the athlete completed less than 70% of planned sessions, without requiring any manual coach input.
- **SC-010**: An admin can grant or revoke athlete access via chat command, with the change taking effect on the next incoming message from the affected user — no restart required.
- **SC-011**: The coach replies to any athlete message within 10 seconds of receipt, from the moment the message is received to the moment the reply appears in the messaging interface.

---

## Assumptions

- Athletes have a Garmin Connect account and a compatible Garmin device that syncs activity data (the system is built around Garmin's unofficial consumer API; no paid developer account is required).
- The hosting environment has continuous internet access for outbound connections to external services (fitness API, AI provider, weather API, and messaging platform).
- The messaging platform used is Slack, operating in socket mode — no public inbound URL or reverse proxy is required on the hosting environment.
- Multiple athletes can be supported from day one; the system is not limited to a single athlete.
- All athletes train for running events; the initial scope is marathon-distance goals with the training plan structure (base/build/peak/taper) designed accordingly.
- The system is self-hosted by an admin on a home server or NAS device, not deployed to a cloud provider with managed scaling requirements.
- Mobile app, web UI, Strava integration, nutrition tracking, and paid Garmin developer API are explicitly out of scope for this version.
- Athletes are expected to have stable enough internet to use Slack and sync their Garmin devices; no offline-first capability is required.
- The coach persona and all communications are in English; multi-language support is out of scope.
- Garmin session authentication tokens are cached per athlete and refreshed automatically; the system does not require the admin to re-enter credentials after initial onboarding.

### Revision: Implementation Sync 2026-04-04

- Reason: Reconciled documented behavior with implementation gaps across latency targets, retry/stale handling, deterministic onboarding progression, multi-goal conflict resolution, biomechanical trend coverage, admin command surface, and athlete-scoped query enforcement.

### Revision: Implementation Sync 2026-04-10
- Reason: Reconciled schema additions (workout_name, activity_type, lthr_bpm, training_readiness), new admin morning-checkin command, LOG_FILE rotating handler, and all associated test requirements.

---

## Implementation Status (as of 2026-04-23)

**Shipped.** All 33 functional requirements and 11 success criteria implemented on `main`. See `retrospective.md` for the full 2026-04-04 post-delivery analysis (100% task completion at that point; 100% spec adherence).

Open items in `tasks.md` (T081–T087, 7 tasks tagged `[Sync: Gap Report]`): additional unit-test coverage for admin commands, workout_name round-trip, activity_type filtering, LTHR derivation, and LOG_FILE rotating handler. These are test-hardening gaps — the functional code already exists.

Subsequent features have since extended this baseline:
- Spec 002 — Athlete timezone auto-detection (`coach/timezone_utils.py`)
- Spec 003 — Garmin credentials via Slack modal (replaces chat-based credential capture referenced in US1)
- Spec 004 — Multi-coach personas (`coach/personas.py`) — supersedes the single "veteran coach" persona in this spec
- Spec 005 — Web analytics dashboard (`running_coach_ai/web/`) — adds a web UI; the original Assumption "Mobile app, web UI… are out of scope" applies to this spec only
