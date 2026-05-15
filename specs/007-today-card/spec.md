# Feature Specification: Today Card

**Feature Branch**: `007-today-card`
**Created**: 2026-05-14
**Status**: Draft
**Input**: User description: "Pin a 'Today Card' to the top of the magazine dashboard that answers the runner's single morning question — what am I running today, and why — in the coach's voice, with health metrics as cover lines and drill-in to detail sections below. Replace the current decorative hero with a magazine-cover treatment that makes the athlete the cover star."

---

## Product Principle

**The runner is the center of attention.** The coach must feel like it has only this one athlete as a client and gives the most personalized advice and motivation possible because it knows everything about them. Its only purpose is to help the athlete succeed in their running goals. Every coach-voiced surface in this spec — the Today Card rationale, the morning_checkin prompt directive, the post-run coach_analysis directive — must reflect this principle.

---

## Clarifications

### Session 2026-05-14

- Q: What is the core job of the Today Card? → A: All-day status anchor that morphs through pre-run, on-watch, completed, rest, and race-day states — the morning state *is* the pre-run briefing.
- Q: Should the card show a live "run in progress" state? → A: No. Garmin's 30-minute polling cadence would make "live" feel laggy; the card stays in `on_watch` until the workout completes.
- Q: Where does the Today Card live on the page? → A: Replace the existing hero with a magazine-cover treatment (athlete-as-cover-star, today's run as the cover article). Morning Readiness and Last Run sections stay below as drill-downs.
- Q: How is the one-line rationale generated? → A: Hybrid — morning_checkin notification body for PRE_RUN/REST_DAY (Claude voice); post-run `coach_analysis` for COMPLETED; rule-based fallback when Claude content is missing; a "Coach is checking in soon" placeholder before the morning check-in scheduler fires.
- Q: What does the Today Card replace? → A: The hero only. Morning Readiness section stays as the detailed drill-down (HRV trend, body battery, sleep, RHR explanations). Last Run section stays as the workout-detail drill-down. The card is a summary; the sections below are the full articles.
- Q: Rest day card content? → A: "Recovery is the workout" headline with a rule-based or morning_checkin-sourced rationale. Cover lines still show health stats. Rest is a first-class state.
- Q: Athlete runs an unplanned workout (bonus run, no matching PlannedWorkout) — what does the card show? → A: After 30-min poll detects the run, card flips to a COMPLETED variant labeled "Bonus Run — not on plan" with actual stats. Coach analysis runs as normal.
- Q: Athlete has no active Goal yet (just signed up, between blocks) — what does the card show? → A: A "Pick a race" CTA card. Tap opens chat with a pre-filled "I want to train for..." prompt.
- Q: Athlete opens the page before the morning_checkin job fires (e.g., 5:30am) — what's in the "why" slot? → A: A "Coach is checking in soon" placeholder. The card auto-refreshes when the scheduler fires.
- Q: Mobile vs desktop priority? → A: Mobile-first at 375px width; desktop is a wider variant of the same layout. Most morning checks happen on phone.
- Q: How should the rationale be structured? → A: A single conversational paragraph in the coach's voice, addressed directly to the athlete ("you"), covering (1) where today's workout sits in the training arc, (2) what today's data says about readiness, (3) one execution cue — woven naturally, not in labeled sections. Sounds like a personal coach, not a clinical report.
- Q: What's clickable on the card? → A: (a) Headline (workout) opens chat with a pre-filled "Tell me about today's workout" prompt. (b) Rationale opens chat with "I have a question about today's plan." (c) Any cover-line stat smooth-scrolls to the Morning Readiness section (or Last Run section in COMPLETED state) with a brief highlight pulse. Chat panel dismisses on tap-outside or Escape.
- Q: What happens when the athlete has an active Goal but no PlannedWorkout row exists for today (plan gap between blocks, plan ran out, generation bug)? → A: A sixth state `OFF_PLAN` with a "Your plan needs attention" message and a chat pre-fill "I'm between training blocks." Distinct from `REST_DAY` (deliberate rest within a plan) and `NO_PLAN` (no active Goal at all). Surfaces plan-coverage gaps to the athlete instead of silently treating them as recovery.
- Q: What does the card show when `GET /api/today` fails (5xx, timeout, network error)? → A: Render the last successfully-cached payload (held in localStorage) with a subtle stale dot on cover-line values. Retry quietly on the next 60s poll. No user-facing error UI, no manual retry button. Graceful degradation — the card never looks broken; transient failures self-heal within one polling cycle.
- Q: Should `/api/today` emit `WebEvent` rows for observability? → A: Emit a `today.state_transition` WebEvent only when the resolved state differs from the previous resolved state for the same athlete. Compact, semantically meaningful for the admin Events view; per-request logging would generate ~50k rows/athlete/year of pure noise.
- Q: When the athlete switches coach personas via `POST /api/coach`, how does the Today Card update? → A: Refetch `/api/today` immediately on a successful switch response so byline, accent color, and persona-static rationale text update within ~100ms. Matches the existing pattern in `web/api/magazine.py` where the quote cache is invalidated on switch.
- Q: What does the rule-based rationale fallback look like? → A: Two-branch structure. **Branch 1 (HealthSnapshot present):** ~6 templates keyed by HRV / sleep / body battery buckets, each one referencing the athlete's name and at least one observed metric. **Branch 2 (HealthSnapshot absent — athlete doesn't wear watch overnight):** explicitly acknowledge no morning data and offer a "run by feel" cue ("If your legs are heavy, hold the easy end; if you're springy, run the target paces"). **The why of the run MUST appear in both branches** — every fallback variant references the workout type, where it sits in the training block (current_phase, current_week from TrainingPlan), and what it builds toward (race_name from Goal). Health commentary varies with data availability; periodization context does not.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Pre-Run Briefing in Athlete-Local Morning (Priority: P1)

An athlete opens the dashboard at 6:45am in their local timezone. Today's planned workout, today's pace target, and a personalized one-paragraph rationale from their coach are the first things they see — above the magazine, above any health-stats explanations, above the calendar. The rationale explains where today's workout sits in their training arc, what today's HRV/sleep say about their readiness, and one execution cue, written in the coach's voice as if speaking only to them. Health stats appear as cover lines beneath; tapping any of them drills into the Morning Readiness section for the full explanation. Tapping the workout opens the coach chat for follow-up questions.

**Why this priority**: This is the daily reason the athlete opens the app. Without it, the dashboard is a magazine pretending to be a tool. With it, the question "what am I running today and why" is answered in under three seconds.

**Independent Test**: Seed an athlete with an active Goal, a PlannedWorkout for today, a HealthSnapshot for today, and a morning_checkin Notification dated today. Hit `GET /api/today` — verify response is in `PRE_RUN` state with the workout headline, rationale text from the notification body, on_watch flag matching the PlannedWorkout's `garmin_workout_id`, and four populated cover lines. Load `magazine.html` and verify the card renders above the Morning Readiness section.

**Acceptance Scenarios**:

1. **Given** an athlete with an active Goal, a PlannedWorkout for today, and a fresh morning_checkin Notification, **When** they load `magazine.html`, **Then** the Today Card renders as the topmost section with state `PRE_RUN`, the workout headline (distance and pace), and the morning_checkin's rationale paragraph as the lede.
2. **Given** the rationale source, **When** the morning_checkin notification body is available for today, **Then** the card displays it verbatim (the personalized paragraph the morning prompt produced) with `rationale.source = "morning_checkin"` and the coach's name in the byline.
3. **Given** today's PlannedWorkout has been synced to Garmin (non-null `garmin_workout_id`), **When** the card renders, **Then** a "Ready on watch ✓" badge appears in the accent color of the athlete's current persona.
4. **Given** the athlete taps the workout headline, **When** the chat panel opens, **Then** the input is pre-filled with "Tell me about today's workout." and the athlete can send or edit before sending.
5. **Given** the athlete taps a cover-line stat (e.g., HRV), **When** the page smooth-scrolls, **Then** the Morning Readiness section receives a 1-second highlight pulse to confirm the navigation.
6. **Given** the athlete taps anywhere outside an open chat panel, **When** the click registers on the dimmed backdrop, **Then** the chat panel closes without warning and any unsent draft is discarded.
7. **Given** the page is loaded on a 375px-wide mobile viewport, **When** the card renders, **Then** the four cover-line stats stack in a 2×2 grid and the workout headline scales to fit on a single line.

---

### User Story 2 — Card Reflects Completed Workout After Poll (Priority: P1)

An athlete completes their tempo run at 7:30am. The activity_poll scheduler picks up the new Garmin activity at 8:00am, writes a CompletedWorkout row, and the post-run coach_analysis Claude call writes a personalized reflection. The athlete refreshes the dashboard at 8:15am; the Today Card has transitioned from `PRE_RUN` to `COMPLETED` — showing actual distance and pace, a green completion ribbon, and a coach reflection paragraph in place of the morning rationale. Cover-line stats now show distance, pace, average HR, and training load instead of morning health metrics. Tapping any stat smooth-scrolls to the Last Run section for the detailed analysis.

**Why this priority**: Closing the loop is what makes the coaching feel responsive. Athletes need to see "you did it" appear on the same surface that said "go do it" — without navigating away.

**Independent Test**: Seed an athlete with today's PlannedWorkout, then add a CompletedWorkout for today with `coach_analysis` populated and `planned_workout_id` matching. Hit `GET /api/today` — verify `state = "COMPLETED"`, `modifiers.is_bonus = false`, cover_lines reference the completed stats, and `rationale.source = "coach_analysis"`. Repeat with `planned_workout_id = null` and verify `is_bonus = true`.

**Acceptance Scenarios**:

1. **Given** an athlete has a CompletedWorkout for today linked to today's PlannedWorkout, **When** they load the card, **Then** the state is `COMPLETED`, the headline shows actual distance and pace, the ribbon reads "Completed", and the rationale draws from `CompletedWorkout.coach_analysis`.
2. **Given** an athlete has a CompletedWorkout for today with `planned_workout_id = null` (bonus run), **When** they load the card, **Then** `modifiers.is_bonus = true` and the headline ribbon reads "Bonus Run — not on plan" without judgement language.
3. **Given** the COMPLETED state, **When** the cover lines render, **Then** they show Distance, Pace, Avg HR, and Training Load (sourced from the CompletedWorkout) — not the morning health stats.
4. **Given** the athlete taps a cover-line stat in COMPLETED state, **When** the page scrolls, **Then** it targets the Last Run section (with highlight pulse), not the Morning Readiness section.
5. **Given** the COMPLETED state, **When** the athlete taps the workout headline, **Then** the chat opens with pre-fill "How did today's run go?" — inviting reflection rather than instruction.

---

### User Story 3 — Rest Day Treats Recovery as a First-Class State (Priority: P2)

An athlete's plan calls for rest today. They open the dashboard at 7am. The Today Card displays "Rest Day — Recovery is the workout" with a one-paragraph rationale from the coach explaining that recovery is the work being done, what their current readiness data shows, and what tomorrow's session will demand. The cover lines still show health stats so the athlete can confirm their recovery is on track. No "Ready on watch" badge appears; no chat pre-fill suggests running.

**Why this priority**: Without a first-class rest state, athletes feel guilty on rest days or assume the app has broken. Treating rest as a deliberate state reinforces the periodization principle and reduces non-compliance with planned recovery.

**Independent Test**: Seed an athlete whose today PlannedWorkout has `workout_type = "rest"` (or no PlannedWorkout for today at all, where the plan implies rest). Hit `GET /api/today` — verify `state = "REST_DAY"`, headline reads as a rest-framed label, rationale paragraph is present, and cover_lines reference today's HealthSnapshot.

**Acceptance Scenarios**:

1. **Given** today's PlannedWorkout has `workout_type = "rest"`, **When** the card loads, **Then** state is `REST_DAY` and the headline reads "Rest Day" with the subtitle "Recovery is the workout."
2. **Given** REST_DAY state, **When** a fresh morning_checkin Notification is available, **Then** the rationale draws from that notification body; otherwise, the rule-based fallback explicitly addresses recovery context.
3. **Given** REST_DAY state, **When** the cover lines render, **Then** they show the four morning health stats (HRV, Body Battery, Sleep, RHR) and tapping any drills into Morning Readiness.
4. **Given** an athlete completes an unplanned run on a rest day, **When** the activity poll registers the CompletedWorkout, **Then** the card transitions to `COMPLETED` with `is_bonus = true` rather than staying in `REST_DAY`.

---

### User Story 4 — Race Day Surfaces Goal, Pace, Countdown (Priority: P2)

The athlete's race is today. They open the dashboard at 5am. The Today Card displays `RACE_DAY` state with the race name, a countdown to the scheduled start time, their goal pace, HR cap, and the morning weather. The rationale is a persona-specific race-morning greeting in the coach's voice ("Trust the work. Execute the plan, mile by mile.") with no green/yellow/red readiness call. Cover lines are read-only — race day is not the day to drill into Morning Readiness for HRV interpretation.

**Why this priority**: Race day is the highest-stakes single morning of the training arc. The card must give the athlete confidence and a clear plan without any noise.

**Independent Test**: Seed an athlete with today's PlannedWorkout having `workout_type = "race"` and a Goal with a race_date matching today. Hit `GET /api/today` — verify `state = "RACE_DAY"`, headline includes the race name, cover_lines reference goal pace / HR cap / distance / weather, and the rationale is the persona's `race_morning_greeting`.

**Acceptance Scenarios**:

1. **Given** today's PlannedWorkout has `workout_type = "race"`, **When** the card loads, **Then** state is `RACE_DAY` and the headline includes the race name from the Goal.
2. **Given** RACE_DAY state, **When** the rationale renders, **Then** it draws from a new `CoachPersona.race_morning_greeting` field — not from morning_checkin or rule-based templates.
3. **Given** RACE_DAY state, **When** the cover lines render, **Then** they show Distance, Goal Pace, HR Cap, and Weather (not morning health stats).
4. **Given** RACE_DAY state, **When** the athlete taps a cover-line stat, **Then** no scroll-to-section action occurs (stats are read-only); only the headline tap is interactive.

---

### User Story 5 — No-Plan State Converts to Goal Selection (Priority: P3)

A new athlete completes onboarding but hasn't picked a race yet, or an athlete has finished one training block and hasn't started another. They open the dashboard. The Today Card displays `NO_PLAN` state with the headline "Ready to train for something?" and a single primary button labeled "Pick a race." Tapping the button opens the chat with a pre-filled prompt: "I want to train for..." The card does not show cover-line stats — the athlete has nothing to drill into yet.

**Why this priority**: The dead state is a conversion moment. Without a clear CTA, new or between-blocks athletes see an empty dashboard and lose momentum. With one, the friction to start the next training arc drops to a single tap.

**Independent Test**: Seed an athlete with `onboarding_complete = true` but no active Goal. Hit `GET /api/today` — verify `state = "NO_PLAN"`, headline is the CTA copy, `actions.cta` contains the chat pre-fill, and `cover_lines` is empty or hidden.

**Acceptance Scenarios**:

1. **Given** an athlete with no active Goal, **When** the card loads, **Then** state is `NO_PLAN`, headline reads "Ready to train for something?", and a primary "Pick a race" button appears in the persona's accent color.
2. **Given** the athlete taps "Pick a race", **When** the chat panel opens, **Then** the input is pre-filled with "I want to train for..." and the athlete can continue the conversation into goal-setting.
3. **Given** NO_PLAN state, **When** the cover lines region renders, **Then** it is either hidden or replaced with the CTA button — health stats are not shown.

---

### User Story 6 — Off-Plan Day Surfaces Plan Gap (Priority: P3)

An athlete has an active Goal and a training plan, but today has no PlannedWorkout row — they're between training blocks, the plan ran out before the race date, or a plan-generation bug left a hole. They open the dashboard. The Today Card displays `OFF_PLAN` state with the headline "Your plan needs attention" and a primary button labeled "Review my plan." Tapping the button opens the chat with a pre-filled prompt: "I'm between training blocks." The card does not show cover-line stats. The state is distinct from REST_DAY — rest days are deliberate; OFF_PLAN days are gaps that need the athlete's attention.

**Why this priority**: Silently treating plan gaps as rest days erodes trust — athletes following the plan literally would stop running entirely if the system kept saying "Recovery is the workout" for a week. Making the gap visible turns a plan-generation failure into a low-friction conversation with the coach.

**Independent Test**: Seed an athlete with an active Goal but zero PlannedWorkout rows for today (and no `rest`-type row). Hit `GET /api/today` — verify `state = "OFF_PLAN"`, headline is "Your plan needs attention", `actions.cta` contains "Review my plan" and the pre-fill "I'm between training blocks.", and `cover_lines` is omitted.

**Acceptance Scenarios**:

1. **Given** an athlete with an active Goal but no PlannedWorkout for today, **When** the card loads, **Then** state is `OFF_PLAN`, headline reads "Your plan needs attention", and a primary "Review my plan" button appears in the persona's accent color.
2. **Given** the athlete taps "Review my plan", **When** the chat panel opens, **Then** the input is pre-filled with "I'm between training blocks." and the athlete can continue into a plan-coverage discussion.
3. **Given** OFF_PLAN state, **When** the cover lines region renders, **Then** it is omitted — the gap is the message, not the stats.
4. **Given** the athlete records a CompletedWorkout for today (impromptu run on a plan-gap day), **When** the activity poll picks it up, **Then** the card transitions to `COMPLETED` with `is_bonus = true` rather than staying in `OFF_PLAN`.

---

## Functional Requirements *(mandatory)*

### Endpoint

- **FR-001**: A new endpoint `GET /api/today` MUST be added, protected by the existing `@login_required` decorator. It MUST return a JSON object whose top-level fields are: `state`, `today_iso`, `today_pretty`, `athlete`, `headline`, `rationale`, `modifiers`, `cover_lines`, `actions`. The response MUST always include a `state` field with one of the values: `NO_PLAN`, `PRE_RUN`, `COMPLETED`, `REST_DAY`, `RACE_DAY`, `OFF_PLAN`.
- **FR-002**: The endpoint MUST compute the date in `athlete.timezone` (falling back to `America/New_York`), matching the timezone-safe pattern already used in `running_coach_ai/scheduler/morning.py:117`. Server-UTC `date.today()` MUST NOT be used for state determination.
- **FR-003**: The endpoint MUST resolve state in this precedence: (1) if no active Goal exists, `NO_PLAN`; (2) if today's PlannedWorkout has `workout_type = "race"`, `RACE_DAY`; (3) if today has a CompletedWorkout, `COMPLETED` (regardless of planned/bonus); (4) if today's PlannedWorkout has `workout_type = "rest"`, `REST_DAY`; (5) if no PlannedWorkout exists for today AND an active Goal exists, `OFF_PLAN`; (6) otherwise `PRE_RUN`. `OFF_PLAN` surfaces gaps between training blocks, plans that have run out, or generation bugs — distinct from deliberate rest days.
- **FR-004**: The endpoint p95 latency MUST be under 100ms for an athlete with a fully-seeded record (Goal, PlannedWorkout, HealthSnapshot, Notification). It MUST execute no more than 6 indexed DB queries and zero Claude API calls.

### Rationale sourcing

- **FR-005**: For PRE_RUN and REST_DAY states, the rationale text MUST be resolved in this order: (1) the body of the most recent `Notification(kind="morning_checkin")` with `created_at >= now - 24h`, parsed to extract the rationale paragraph; (2) if no such notification exists AND the current athlete-local time is before 7:00am, the placeholder copy "Coach is checking in soon — pull this up after 7am for today's call." with `rationale.source = "placeholder"`; (3) otherwise, the rule-based fallback (FR-008) with `rationale.source = "rule_based"`.
- **FR-006**: For COMPLETED state, the rationale text MUST be resolved in this order: (1) the rationale paragraph parsed from `CompletedWorkout.coach_analysis`; (2) the rule-based completed-summary fallback if `coach_analysis` is empty.
- **FR-007**: For RACE_DAY state, the rationale text MUST draw from a new `CoachPersona.race_morning_greeting` field. Each persona in `coach/personas.py` MUST be given a race-morning greeting written in that persona's voice. The classic-persona default is "Trust the work. Execute the plan, mile by mile."
- **FR-007a**: For NO_PLAN state, the rationale text is the static copy: "Ready to train for something? Pick a race and your coach will build a plan." with `rationale.source = "persona_static"`.
- **FR-007b**: For OFF_PLAN state, the rationale text is the static copy: "Your plan doesn't have a workout scheduled for today. This usually means you're between training blocks or the plan needs a refresh — let's talk." with `rationale.source = "persona_static"`. The byline still names the active coach persona to preserve voice continuity.
- **FR-008**: A new module `coach/today_rationale.py` MUST provide a pure function with signature `rule_based_morning(snap: HealthSnapshot | None, planned: PlannedWorkout, plan: TrainingPlan | None, goal: Goal | None, athlete_name: str) -> str` that returns a single short paragraph in coach voice. The function MUST always weave in the **why of the run** — referencing `planned.workout_type`, `plan.current_phase` and `plan.current_week` when available, and `goal.race_name` when available — regardless of whether the HealthSnapshot is present.
- **FR-008a**: When `snap` is non-null (athlete wore their watch overnight), `rule_based_morning` MUST select one of ~6 templates keyed by buckets across (HRV vs baseline, sleep duration, body battery). Each template MUST reference at least one observed metric numerically and the athlete by name. The voice MUST be intentionally simpler than the morning_checkin Claude output so athletes can tell the fallback is in effect (e.g., shorter sentences, fewer references to recent conversation history — but the periodization context still appears).
- **FR-008b**: When `snap` is null (athlete did not wear their watch overnight), `rule_based_morning` MUST emit a "no morning data, run by feel" variant that (a) explicitly acknowledges the missing data ("I don't have your overnight readings"), (b) still explains why this workout is on today's plan (workout_type, phase, goal context), and (c) provides a how-to-decide-by-feel cue tied to the workout type — e.g. for tempo: *"if your legs are heavy, hold the easy end of the pace range; if you're springy, run the target paces."* The athlete is named.
- **FR-008c**: Both branches of `rule_based_morning` MUST work without a `TrainingPlan` or `Goal` (early-onboarding athletes whose plan generation hasn't completed). When `plan` or `goal` is null, the function MUST omit the periodization clause without breaking sentence structure and MUST still reference the workout type.
- **FR-009**: The morning_checkin Claude prompt (in `coach/prompt.py` or wherever it is built) MUST be updated to enforce the athlete-centered single-paragraph rationale directive: *Write today's rationale as one paragraph addressed directly to {athlete_name}. You are their personal coach with no other clients — you know their plan, their recent runs, their recent conversations, their goal. Reference continuity: name what they did this week, what's coming, what you've been watching. Cover the workout's place in the arc, today's readiness data, and one execution cue — but make it feel like a coach who has been thinking about them specifically, not a system generating output. Two to three sentences. First person. No hedging.*
- **FR-010**: The post-run `coach_analysis` prompt MUST be updated with an analogous directive: a single conversational paragraph addressed to the athlete, covering how the run went relative to the plan, what one thing to take from it, and what's next. Same athlete-centered voice rules apply.
- **FR-011**: The rationale parser MUST cleanly extract the rationale paragraph from morning_checkin and coach_analysis bodies even if the surrounding notification contains additional structure (sign-off, links, etc.). Acceptance: the existing first-sentence excerpt in `running_coach_ai/web/api/magazine.py:_excerpt_first_sentences` MUST be retained but augmented by a paragraph-aware extractor (`_extract_rationale_paragraph`) that takes the first paragraph rather than the first sentence.

### Cover lines and modifiers

- **FR-012**: The `cover_lines` array MUST always contain exactly 4 entries in PRE_RUN, REST_DAY, COMPLETED, and RACE_DAY states. `NO_PLAN` and `OFF_PLAN` MAY omit cover_lines (the CTA button replaces them). Each entry MUST have fields `label`, `value`, and (for non-RACE_DAY states) `drill_to` pointing to either `"morning"` (Morning Readiness section) or `"last_run"` (Last Run section).
- **FR-013**: For PRE_RUN and REST_DAY states, cover_lines MUST source from today's HealthSnapshot — HRV, Body Battery, Sleep hours, Resting HR — falling back to the most recent snapshot within 3 days (matching the magazine endpoint's existing pattern in `web/api/magazine.py:339-356`).
- **FR-014**: For COMPLETED state, cover_lines MUST source from the CompletedWorkout — Distance (mi), Pace (min/mi), Average HR, Training Load.
- **FR-015**: For RACE_DAY state, cover_lines MUST source from the PlannedWorkout and the Goal — Distance (mi), Goal Pace, HR Cap (athlete's `lthr_bpm` × 0.92), and current Weather. Weather sourcing details are out of scope for this spec — placeholder "—" is acceptable v1.
- **FR-016**: The `modifiers.on_watch` boolean MUST be `true` if and only if today's PlannedWorkout has a non-null `garmin_workout_id`, and applies to PRE_RUN and RACE_DAY states only.
- **FR-017**: The `modifiers.is_bonus` boolean MUST be `true` if and only if today's CompletedWorkout has a null `planned_workout_id`, and applies to COMPLETED state only.

### Frontend

- **FR-018**: The existing `<section id="hero">…</section>` block in `running_coach_ai/web/static/magazine.html` MUST be replaced with a new `<section id="today">…</section>` block that hosts the Today Card. All six state variants (`NO_PLAN`, `PRE_RUN`, `COMPLETED`, `REST_DAY`, `RACE_DAY`, `OFF_PLAN`) MUST be rendered from this single section using state-conditional templates or attribute selectors.
- **FR-019**: The card MUST be the first visible section on page load; the existing `Morning Readiness`, `Last Run`, `Calendar`, and other sections MUST remain unchanged and unmoved below it.
- **FR-020**: The card MUST render mobile-first at 375px width: cover lines stack in a 2×2 grid; workout headline scales to fit on a single line. At 768px+ widths, cover lines flatten to a 4-column row.
- **FR-021**: The card's accent color (used for the ribbon, rationale border-left, on-watch badge, and CTA button) MUST be sourced from a new `CoachPersona.accent_color` field. Each persona defined in `coach/personas.py` MUST be given an accent color. Default for the classic persona is `#b8ff4f` (the existing race-green).
- **FR-022**: Tapping the workout headline (in PRE_RUN, COMPLETED, REST_DAY, RACE_DAY states) or the primary CTA button (in NO_PLAN, OFF_PLAN states) MUST open the existing chat panel with the input pre-filled. State-specific prompts: PRE_RUN→"Tell me about today's workout.", COMPLETED→"How did today's run go?", REST_DAY→"How should I make the most of today's recovery?", RACE_DAY→"Walk me through race execution.", NO_PLAN→"I want to train for…", OFF_PLAN→"I'm between training blocks." The NO_PLAN state's CTA button label is "Pick a race"; the OFF_PLAN state's CTA button label is "Review my plan". In both NO_PLAN and OFF_PLAN states the CTA button is the sole tap target — there is no workout headline.
- **FR-023**: Tapping the rationale paragraph MUST open the chat panel with the input pre-filled with "I have a question about today's plan." (PRE_RUN, REST_DAY) or "I have a follow-up on this run." (COMPLETED).
- **FR-024**: Tapping any cover-line stat MUST smooth-scroll to the appropriate drill-in section (`morning` for PRE_RUN/REST_DAY, `last_run` for COMPLETED) and trigger a 1-second highlight-pulse animation on the target section.
- **FR-025**: The chat panel MUST dismiss when: (a) the user taps on the dimmed backdrop outside the panel; (b) the user presses Escape; or (c) the user clicks the explicit close (X) button. All three paths discard any unsent draft text without confirmation.
- **FR-026**: The frontend MUST poll `GET /api/today` every 60 seconds while the page tab is visible (using the existing `visibilitychange` pattern), and additionally trigger an immediate re-fetch when (a) the notification bell registers a new notification of kind `morning_checkin` or `post_run_feedback`, or (b) the athlete successfully switches coach personas via `POST /api/coach`. The persona-switch refetch MUST also replace the cached payload in `localStorage` (FR-028a) so a subsequent first-load failure cannot render a card with the prior coach's voice.
- **FR-027**: The card MUST fade in on first page load (200ms ease-out). State transitions during a session (e.g., COMPLETED arriving after a poll) MUST animate the ribbon swap with a brief slide. No parallax or scroll-triggered effects.
- **FR-028**: The card MUST be keyboard-accessible. Tab order: workout headline → rationale paragraph → cover-line stats (left to right). Enter activates each focusable region.
- **FR-028a**: After each successful `GET /api/today` response, the frontend MUST persist the payload to `localStorage` under the key `runcoach.today.{athlete_id}` along with a fetched-at timestamp. The cached payload supersedes any in-memory state on subsequent page loads to make the card render immediately, with a background poll firing within 500ms of first paint to refresh.
- **FR-028b**: When `GET /api/today` fails (HTTP 5xx, network error, or timeout exceeding 5 seconds), the card MUST render the last successfully-cached payload from `localStorage`. Cover-line values in the rendered card MUST display a subtle stale indicator (a small grey dot next to the value). The chat-on-tap and drill-in interactions MUST remain functional during the stale state. No user-facing error message and no manual retry button are shown — the failure is silent.
- **FR-028c**: If `GET /api/today` fails on first load AND no cached payload exists in `localStorage`, the card MUST render a minimal skeleton placeholder (eyebrow line, headline bar, rationale block, four cover-line slots — all rendered as grey shapes matching the layout) and continue polling silently. The skeleton remains until the next successful poll. No error message is shown.
- **FR-028d**: The frontend MUST continue the 60-second polling loop during failure. On the first successful response after a failure, the stale indicators MUST clear (within a single render tick) and the card content updates without animation. State-transition animations (FR-027) apply only when the resolved state differs from the cached state.

### Backend additions

- **FR-029**: A new blueprint `running_coach_ai/web/api/today.py` MUST be added and registered in `running_coach_ai/web/app.py:create_app`. The blueprint MUST be the sole owner of `/api/today`.
- **FR-030**: A new module `running_coach_ai/coach/today_rationale.py` MUST contain the rule-based fallback function (FR-008) plus a helper `extract_rationale_paragraph(body: str) -> str` used by the today endpoint to parse morning_checkin and coach_analysis bodies. Both functions MUST be pure (no DB / Garmin / Claude side effects) and fully unit-testable.
- **FR-031**: The `CoachPersona` dataclass in `coach/personas.py` MUST be extended with two new fields: `accent_color: str` and `race_morning_greeting: str`. Each existing persona (classic, maya, jordan) and the `LEGACY_COACH_KEY_ALIASES` resolution MUST be updated to populate these fields.
- **FR-032**: The endpoint MUST NOT modify the existing `/api/magazine` endpoint. Other consumers and surfaces of `/api/magazine` (the rest of the magazine layout) remain unchanged.
- **FR-032a**: The endpoint MUST emit a `WebEvent` of kind `today.state_transition` whenever the resolved state for the requesting athlete differs from the most recent resolved state for that athlete. The transition is detected by comparing against the prior `today.state_transition` WebEvent for the athlete (or, for first-ever resolution, by treating "no prior event" as the implicit baseline so the first call to `/api/today` always emits one). The event payload MUST include `{from_state, to_state, athlete_id, timestamp}`. Per-request hits where the state is unchanged MUST NOT emit a WebEvent.
- **FR-032b**: The state-transition WebEvent emission MUST NOT block the HTTP response. Implementation may use an after-request hook, a deferred write, or accept that the WebEvent INSERT happens before the response flush — whichever path keeps the p95 latency target in FR-004 intact.

### Testing

- **FR-033**: A new integration test file `tests/integration/web/test_today_api.py` MUST cover, at minimum, one acceptance scenario per user story (across all six user stories) including OFF_PLAN, the rationale-source fallback ladder for each state (3–4 scenarios), the four cover-line sourcing branches, and one regression scenario asserting that an athlete with an active Goal but no PlannedWorkout returns `OFF_PLAN` rather than `REST_DAY` or `NO_PLAN`.
- **FR-034**: A new unit test file `tests/unit/test_today_rationale.py` MUST cover both branches of `rule_based_morning`: (a) the ~6 with-snapshot templates against representative `HealthSnapshot` permutations (good HRV, poor HRV, low body battery, short sleep, missing sleep but present HRV); (b) the no-snapshot "run by feel" variant for each workout type (easy / long_run / tempo / interval / strides / recovery); (c) the no-plan / no-goal degraded variants confirming periodization clauses are omitted without breaking sentence structure; (d) every test MUST assert the athlete name is woven into the returned text; (e) every test MUST assert the workout_type is referenced somewhere in the returned text — the **why of the run is never absent**. The paragraph-extraction helper (FR-011) is covered by separate test cases against representative morning_checkin and coach_analysis bodies.
- **FR-035**: All new tests MUST run against an in-memory SQLite DB and MUST NOT depend on Claude, Garmin, or any external network call.

---

## Edge Cases

- **Timezone**: All "today" comparisons use `athlete.timezone` (matching the recently fixed magazine endpoint and the existing scheduler pattern). Server UTC is not used.
- **Stale health data**: If today's HealthSnapshot is missing, the endpoint falls back to the most recent snapshot within 3 days and marks `cover_lines[].is_stale = true`. If no snapshot in 3 days, cover_line values render as "—".
- **Missed morning_checkin**: If the morning_checkin scheduler failed (Garmin 429, athlete-tz edge), the endpoint serves the rule-based fallback rationale and the cover-lines still render from any available HealthSnapshot.
- **Multiple morning_checkin notifications**: Take the most recent within the 24h freshness window (matching existing `_resolve_morning_message` pattern in `web/api/magazine.py:222-255`).
- **Workout type without a label**: Fall back to title-casing the workout_type string for the eyebrow text (matching the magazine's existing `_TYPE_LABEL` pattern).
- **Multiple PlannedWorkouts on the same day**: Take the first ordered by scheduled time / id (multi-workout-day support is not in scope for this spec).
- **Multiple CompletedWorkouts on the same day**: Take the most recent by `started_at` (or `id` if started_at is missing).
- **Athlete with no name**: Default to "Athlete" as the cover-star name.
- **Athlete in the middle of onboarding**: NO_PLAN state takes precedence; the card shows the "Pick a race" CTA even if onboarding is technically not yet complete.
- **Chat panel already open when the user taps headline**: Replace the current draft with the new pre-fill (no warning — drafts are not persisted anywhere else).

---

## Out of Scope

The following are explicitly out of scope for spec 007 and parked as follow-up work:

- **Manual / treadmill run logging.** A "Mark complete" affordance is a separate feature.
- **Live mid-run state.** Garmin's 30-minute poll cadence makes "in progress" too laggy to be valuable; revisit if Garmin push-events become available.
- **Persona avatar art.** Cover byline stays text-only in v1.
- **Weather widget for RACE_DAY.** Acceptable v1 to render "—" for weather; full weather sourcing is its own spec.
- **Push notifications when state transitions.** Web push for morning_checkin and post-run feedback is its own spec (mobile / PWA surface).
- **A/B testing of the new rationale prompt directive.** Ship the directive as written; iterate based on athlete feedback.
- **Multi-workout-per-day support.** AM + PM run handling is not in scope.
- **Long-press / context menu on the card.** Tap is the only gesture in v1.

---

## Constraints

- **No new DB migrations.** All data sources from existing models (`Athlete`, `Goal`, `PlannedWorkout`, `CompletedWorkout`, `HealthSnapshot`, `Notification`). New fields on `CoachPersona` (`accent_color`, `race_morning_greeting`) live in code on the dataclass.
- **No new Claude calls at request time.** The Today Card reads from already-written notification bodies and CompletedWorkout.coach_analysis fields. The two prompt updates (FR-009, FR-010) apply to existing scheduled Claude calls — they do not introduce new spend.
- **No new background jobs.** State transitions are driven by existing schedulers (activity_poll, morning_checkin, refresh_athlete_morning_jobs).
- **No changes to Garmin integration.** The card reads `garmin_workout_id` for the on-watch badge but does not write to Garmin.
- **Athlete data isolation.** Every DB query in the new endpoint MUST include `athlete_id` and use `database/session.py:scoped_query` where applicable. Adheres to the project's hard isolation rule.
- **Speckit workflow.** Spec proceeds through specify → clarify → plan → tasks → implement using the slash commands in `.claude/commands/`. This document is the output of the specify phase.

---

## Success Criteria

The Today Card is successful when:

1. An athlete opening the dashboard between 6am and 8am can answer "what am I running today, and why" in **under three seconds**, without scrolling or tapping.
2. The rationale paragraph **references athlete-specific continuity** in at least 80% of sampled morning_checkin bodies (e.g., names the recent long run, references a prior conversation, references a memory from `CoachMemory`). Measured by manual review of 20 production samples post-launch.
3. The card transitions cleanly through all five states without page reloads, within 60 seconds of the underlying state change.
4. p95 endpoint latency stays **under 100ms** under normal load (one athlete, fully seeded DB).
5. **Zero new database migrations** are introduced; all data sources from existing models.
6. Mobile users (375px viewport) can read the headline, rationale, and all four cover-line stats **without horizontal scrolling**.
7. Tapping the workout headline opens chat with the correct pre-fill in every state, and tapping outside the chat panel reliably dismisses it.

---

## Open Questions for Plan Phase

These remain as investigation items for `/speckit.plan` — they don't block specification but the plan must resolve them before tasks are decomposed:

- Q: Where exactly is the morning_checkin Claude prompt body built today? Is there a single template we update for FR-009, or is the body assembled across multiple calls? (Investigation: read `coach/prompt.py`, `coach/adapter.py`, and the morning scheduler job to locate.)
- Q: Does the existing chat panel implement tap-outside-to-dismiss and Escape-to-dismiss handlers? (Investigation: read `running_coach_ai/web/static/magazine.js` chat-panel code. Determines whether FR-025 is implementation work or just verification work.)
- Q: What are the canonical text strings for the ~6 with-snapshot templates and the no-snapshot run-by-feel variants in `rule_based_morning`? (Drafted during implementation by the engineer writing `coach/today_rationale.py`, reviewed by the user. Not a spec ambiguity — the FRs constrain shape and content; only the wording is open.)
