# Feature Specification: Web Analytics Dashboard

**Feature Branch**: `005-web-analytics-dashboard`
**Created**: 2026-04-18
**Status**: Draft
**Input**: User description: "Web analytics dashboard for monitoring athlete stats, viewing coach feedback, training plan calendar, activity feed with biomechanics, and coach chat — based on the Running Coach AI design prototype."

## Clarifications

### Session 2026-04-18

- Q: How does the web dashboard identify which athlete's data to display? → A: Full login — username/password form with server-side session cookie; supports multiple athletes each seeing only their own data.
- Q: Where is the weekly review narrative stored? → A: Persisted in a new `WeeklyReviewSummary` DB table — written by the Sunday scheduler job at generation time; web screen reads it (new model + Alembic migration required).
- Q: Does Coach Chat history persist between sessions? → A: Shared with Slack — web and Slack chat read/write the same conversation history table, forming one unified thread per athlete.
- Q: How does the web server run alongside the existing bot process? → A: Separate process — a standalone `web.py` entry point added as a second service in `docker-compose.yml`, sharing the same SQLite file with WAL mode enabled to handle concurrent access.
- Q: What happens when the Garmin sync button fails? → A: Inline error — button transitions to a red error state with a brief reason (e.g., "Garmin session expired") and returns to a retry-able state; no modal or silent failure.
- Added: Admin observability — both structured server-side logging (requests, errors, Garmin sync outcomes, auth events) and a web Admin screen surfacing recent errors, sync history, and auth events. Admin screen restricted to users with the admin role.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - View Daily Readiness & Today's Workout (Priority: P1)

An athlete opens the web dashboard each morning to check their readiness metrics (HRV, Body Battery, sleep, resting HR) and see what workout is scheduled for today. They can expand the workout card to see target pace, estimated duration, elevation, HR zone, and coach notes. The morning message from their selected coach persona appears prominently at the top.

**Why this priority**: This is the core daily use case — the same information the morning check-in bot delivers via Slack, now surfaced in a visual, at-a-glance format that athletes can refer back to throughout the day.

**Independent Test**: Can be tested by visiting the Dashboard screen with a seeded athlete in the database; delivers value even before any other screen is built.

**Acceptance Scenarios**:

1. **Given** an athlete with health data in the database, **When** they open the Dashboard, **Then** they see HRV, Body Battery, Sleep, and Resting HR displayed as metric cards with current values and week-over-week trend indicators.
2. **Given** today has a scheduled workout, **When** the athlete expands the workout card, **Then** they see target pace, distance, estimated duration, elevation gain, HR zone, and a coach note.
3. **Given** the athlete has selected a coach persona, **When** the Dashboard loads, **Then** the coach's morning message appears with the correct name and appropriate tone for that persona.
4. **Given** a readiness metric is below the athlete's typical range, **When** the Dashboard loads, **Then** the metric is visually distinguished (color or trend indicator) to indicate below-average status.
5. **Given** the Dashboard, **When** displayed, **Then** a weekly plan strip shows all 7 days (Mon–Sun) with workout type, completion status, and today highlighted.

---

### User Story 2 - Browse Training Calendar (Priority: P2)

An athlete navigates to the Training Plan screen to see their upcoming workouts in a monthly calendar view. Calendar cells show workout type, distance, duration, target pace, and training stress score (TSS). Completed workouts display actual pace and a zone-compliance bar. The athlete can switch months to browse forward through their plan. Clicking a day opens a detail panel with full workout description and coach notes. Weeks start on Monday.

**Why this priority**: Visual training plan review is a primary reason athletes would choose a web interface over Slack — seeing the full arc of their training in one view is uniquely suited to a dashboard.

**Independent Test**: Can be tested independently by navigating to Training Plan with a populated `PlannedWorkout` table.

**Acceptance Scenarios**:

1. **Given** the athlete has a training plan, **When** they open Training Plan, **Then** they see a calendar grid with weeks running Monday–Sunday, color-coded by workout type (easy, tempo, long, strength, rest, workout).
2. **Given** a calendar cell, **When** viewed, **Then** it shows workout name, distance (mi), estimated duration, target pace, TSS, and an intensity bar (1–4 segments, color-coded Easy→Peak).
3. **Given** a completed workout day, **When** viewed in the calendar, **Then** actual pace and a micro heart-rate-zone bar replace the intensity bar.
4. **Given** the athlete clicks a day, **When** the detail panel opens, **Then** they see distance, duration, target pace, HR zone, TSS, intensity level, and coach notes for that workout.
5. **Given** multiple months of plan data, **When** the athlete clicks the month navigation arrows, **Then** the calendar updates to show the selected month without a page reload.
6. **Given** the Garmin sync button, **When** clicked and sync succeeds, **Then** the button shows a "✓ Synced to Garmin" confirmation state.
7. **Given** the Garmin sync button, **When** clicked and sync fails (session expired, 429 rate-limit, network error), **Then** the button shows a red error state with a brief reason and allows the athlete to retry.

---

### User Story 3 - Review Activity Feed with Biomechanics (Priority: P2)

An athlete navigates to the Activity Feed to review their recent completed runs. Each run card shows distance, time, pace, and average HR at a glance. Expanding a card reveals biomechanics data (cadence, ground contact time, vertical oscillation, power), HR zone distribution, post-run HRV, coach analysis text, and flagged form observations. Athletes can submit feedback on each run: how it felt (5-point emoji scale), perceived effort (1–10), and free-text notes.

**Why this priority**: Post-run analysis is a key differentiator of this coaching system; surfacing it visually completes the loop between Garmin data and athlete insight in a way Slack messages cannot.

**Independent Test**: Can be tested with existing activity data in the database, independently of other screens.

**Acceptance Scenarios**:

1. **Given** the athlete has completed activities, **When** they open Activity Feed, **Then** they see a summary row (weekly miles, YTD miles, 7-day avg pace, training load) and a list of recent activities sorted by date descending.
2. **Given** an activity card, **When** expanded, **Then** biomechanics data (cadence, GCT, vertical oscillation, power), HR zone bar with percentages, post-run HRV, coach analysis text, and flagged observations appear.
3. **Given** a flagged biomechanics observation (e.g., heel strike, cadence drop), **When** displayed, **Then** it shows a warning indicator distinguishing it from positive checkmarks.
4. **Given** the feedback section on an expanded card, **When** the athlete selects a feel emoji, RPE value, types notes, and clicks Save, **Then** their feedback is persisted and a "✓ Saved" confirmation appears.
5. **Given** previously saved feedback, **When** the athlete re-opens the activity card, **Then** their saved feel, RPE, and notes are pre-populated.

---

### User Story 4 - Chat with Coach Persona (Priority: P3)

An athlete navigates to Coach Chat to have a real-time conversation with their selected coach. Messages are processed server-side using the coach's persona and the athlete's current training context (week, HRV, today's workout). Quick-reply chips suggest common questions. The typing indicator appears while a response is being generated.

**Why this priority**: Extends the existing Slack-based coaching to the web, giving athletes an alternative channel for longer, richer conversations.

**Independent Test**: Can be tested using the Claude API with stubbed athlete context, independently of Garmin/Slack infrastructure.

**Acceptance Scenarios**:

1. **Given** the athlete sends a message, **When** submitted, **Then** the coach's response appears within 15 seconds, reflecting the selected coach persona's voice and athlete training context.
2. **Given** quick-reply chips, **When** the athlete taps one, **Then** it is sent as a message without manual typing.
3. **Given** a response is in-flight, **When** displayed, **Then** an animated typing indicator is visible in the conversation.
4. **Given** the athlete switches coach persona, **When** they send the next message, **Then** the response uses the newly selected coach's voice.
5. **Given** the athlete previously messaged their coach via Slack, **When** they open Coach Chat on the web, **Then** the prior Slack conversation history is visible in the web thread.

---

### User Story 5 - Weekly Review Summary (Priority: P3)

The athlete navigates to the Weekly Review screen to see an auto-generated summary of the past training week: miles completed, elevation, average HRV, total TSS, daily volume chart, body battery trend, AI narrative from the coach, and a preview of next week's plan.

**Why this priority**: Provides weekly context and motivation; athletes benefit from seeing progress summarized alongside upcoming training.

**Independent Test**: Can be tested by seeding a `WeeklyReviewSummary` row directly (bypassing the Sunday scheduler); `GET /api/review` must return the correct shape; the React WeeklyReview screen renders all metric cards, charts, narrative, and next-week strip. The 404 empty state is testable without any seeded data.

**Acceptance Scenarios**:

1. **Given** a completed training week, **When** the athlete opens Weekly Review, **Then** they see aggregate metrics (miles, elevation, avg HRV, TSS) as metric cards with trend indicators.
2. **Given** the Weekly Review screen, **When** displayed, **Then** a daily volume bar chart and body battery trend line chart appear.
3. **Given** the coach has generated a weekly review narrative, **When** displayed, **Then** the AI summary text appears with key metrics (e.g., improved efficiency) highlighted.
4. **Given** next week's plan, **When** shown in the preview strip, **Then** each day displays workout type and brief description.

---

### User Story 6 - Coach & Theme Switcher (Priority: P4)

The sidebar contains a coach switcher showing all three personas (Alex, Maya, Jordan). Selecting a different coach updates the greeting message, chat context, and accent color throughout the UI. A light/dark mode toggle in the sidebar switches between warm parchment (light) and warm charcoal (dark) color schemes. Preferences persist across page refreshes.

**Why this priority**: Personalisation is what differentiates a generic analytics dashboard from an athlete's coaching companion. Coach switching and theme control are low-cost, high-impact features that increase daily engagement and make the product feel tailored.

**Independent Test**: Can be tested without any API calls — switch coach in the sidebar and verify the Dashboard greeting updates within 100ms; toggle dark mode and verify the palette changes within 200ms; refresh the page and verify all selections are restored. The `test_theme_constants.py` test (T040b) validates the underlying `THEMES`/`ACCENTS` constants structurally without a browser.

**Acceptance Scenarios**:

1. **Given** the sidebar, **When** the athlete selects a different coach, **Then** the Dashboard greeting updates immediately to reflect the new coach's voice.
2. **Given** the light/dark toggle, **When** clicked, **Then** the entire UI switches color schemes in under 200ms with no layout shift.
3. **Given** the user's selected coach and mode, **When** the page is refreshed, **Then** preferences are restored.
4. **Given** three accent colors (Sage, Terracotta, Stone), **When** the athlete selects one, **Then** all accent-colored elements update across all screens.

---

### User Story 7 - Admin Observability Screen (Priority: P4)

An admin user navigates to the Admin screen to troubleshoot the system. They can see a live feed of recent events: web request errors (5xx), Garmin sync attempts and outcomes, Claude API calls and latency, failed login attempts, and scheduler job executions. Each event shows a timestamp, severity level, category, and a brief message. The admin can filter by category (auth, garmin, claude, scheduler, http). Server-side structured logs are also written to stdout so they are accessible via `docker-compose logs -f web`.

**Why this priority**: Operational visibility is essential for a self-hosted system — when Garmin auth breaks or Claude returns errors, the admin needs to diagnose quickly without SSHing into the host and tailing raw logs.

**Independent Test**: Can be tested by triggering known events (failed login, Garmin sync) and verifying they appear in both the Admin screen and `docker-compose logs`.

**Acceptance Scenarios**:

1. **Given** the admin user is logged in, **When** they navigate to the Admin screen, **Then** they see a list of recent events sorted by timestamp descending, each showing time, severity (info/warn/error), category, and message.
2. **Given** a Garmin sync attempt (success or failure), **When** the event occurs, **Then** it appears in the Admin event feed within the next page load, including the outcome and any error reason.
3. **Given** a failed login attempt, **When** it occurs, **Then** it is logged as a warning event with the attempted username (not password) and source IP.
4. **Given** a Claude API call, **When** it completes, **Then** it is logged with latency in ms and the coach persona used.
5. **Given** the category filter, **When** the admin selects a category (e.g., "garmin"), **Then** only events in that category are shown.
6. **Given** any web request that results in a 5xx error, **When** it occurs, **Then** it is logged server-side with the route, error type, and stack trace summary, and appears in the Admin event feed as an error.
7. **Given** a non-admin authenticated user, **When** they attempt to access the Admin screen URL, **Then** they receive a 403 response and are not shown any log data.

---

### Edge Cases

- What happens if the Admin event log grows very large — is there a retention limit or pagination?
- What happens when no Garmin health data is available for today (athlete didn't wear watch)?
- How does the calendar display weeks at month boundaries where some days belong to the previous or next month?
- What if the athlete has no completed activities yet — is the Activity Feed empty state clear?
- What if Claude API is unavailable when the athlete sends a chat message?
- What happens when an activity has partial biomechanics data (e.g., no power meter)?
- How is the dashboard displayed if the athlete's onboarding is incomplete? → Web access does not gate on `onboarding_complete`. An athlete with web credentials set but incomplete Slack onboarding can still log in and see whatever data exists (which may be empty). The web dashboard is read-only — it does not attempt to run onboarding steps.
- What happens when a session expires mid-use — is the athlete redirected to login gracefully?
- What happens on repeated failed login attempts? → **Out of scope for v1**: IP-based rate limiting is deferred to a network-layer proxy (nginx/reverse proxy). Werkzeug's constant-time `check_password_hash` prevents timing attacks. No lockout mechanism is implemented in the Flask layer.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST serve the web dashboard via a standalone `web.py` process on a configurable port, deployed as a second service in `docker-compose.yml` alongside the existing bot/scheduler service.
- **FR-001a**: System MUST present a username/password login form to unauthenticated visitors and redirect them to it from any protected route.
- **FR-001b**: System MUST establish a server-side session cookie upon successful login and scope all subsequent API responses to the authenticated athlete.
- **FR-001c**: System MUST provide a logout action that invalidates the session and redirects to the login form.
- **FR-002**: System MUST display the Dashboard screen with live athlete health metrics (HRV, Body Battery, Sleep, Resting HR) sourced from the database.
- **FR-003**: System MUST display today's scheduled workout on the Dashboard with all key targets (distance, pace, duration, elevation, HR zone, RPE), expandable on click.
- **FR-004**: System MUST display the selected coach's morning message on the Dashboard.
- **FR-005**: System MUST provide a Training Plan calendar view organized in Monday-first weekly rows, with month navigation covering the full plan period.
- **FR-006**: System MUST render per-day calendar cells showing: workout type emoji, distance, workout name, duration, target pace or zone, TSS, and intensity bar.
- **FR-007**: System MUST show completed days with actual pace and a micro heart-rate-zone compliance bar instead of the intensity bar.
- **FR-008**: System MUST show a workout detail side panel when a calendar day is selected, including all key targets and coach notes.
- **FR-009**: System MUST provide a Garmin sync button on the Training Plan screen that triggers re-upload of upcoming workouts to Garmin Connect. On success, the button shows a "✓ Synced" confirmation. On failure, the button transitions to a red error state displaying a brief reason (e.g., "Garmin session expired — re-auth needed") and reverts to a retry-able state.
- **FR-010**: System MUST display an Activity Feed listing recent completed workouts with collapsed summary (distance, time, pace, avg HR, zone bar).
- **FR-011**: System MUST show expandable activity cards with biomechanics data, HR zone distribution, post-run HRV, coach analysis, and flagged observations.
- **FR-012**: System MUST allow athletes to submit and persist run feedback (feel emoji rating, perceived effort 1–10, free-text notes) per activity.
- **FR-013**: System MUST provide a real-time Coach Chat interface that reads from and writes to the same conversation history used by the Slack coaching thread, so messages sent via Slack and the web form a single unified history per athlete. Claude responses must use the selected coach persona and full athlete context.
- **FR-014**: System MUST display a Weekly Review screen reading from the persisted `WeeklyReviewSummary` record, showing aggregate weekly metrics, daily volume chart, body battery trend, AI narrative, and next-week plan preview. If no review has been generated yet, the screen displays an empty state indicating the next generation time (Sunday 20:00).
- **FR-015**: System MUST support switching between three coach personas (Alex, Maya, Jordan) via a sidebar switcher that immediately updates the greeting, chat tone, and accent color.
- **FR-016**: System MUST support light and dark mode toggling, using warm natural palettes (parchment light / charcoal dark).
- **FR-017**: System MUST support three accent color themes (Sage green, Terracotta, Stone blue) selectable by the user.
- **FR-018**: System MUST persist user preferences (coach selection, color mode, accent color) across page refreshes.
- **FR-019**: System MUST enforce data isolation — only data belonging to the authenticated athlete (resolved from the active session) is served via the web API; unauthenticated requests receive a 401 response.
- **FR-020**: System MUST display metric trend indicators (directional arrows with delta values) for all readiness metrics on the Dashboard.
- **FR-021**: System MUST write structured logs to stdout for all significant server-side events, including: HTTP requests (method, route, status, latency), authentication events (login success/failure with username and IP, logout, session expiry), Garmin sync attempts (athlete, outcome, error reason if any), Claude API calls (coach persona, latency, success/failure), and APScheduler job executions (job name, outcome).
- **FR-022**: System MUST provide a web Admin screen, accessible only to users with the admin role, displaying a paginated, filterable event feed of recent log events (timestamp, severity, category, message). Categories: auth, garmin, claude, scheduler, http.
- **FR-023**: System MUST restrict the Admin screen to admin-role users; non-admin authenticated users must receive a 403 response when accessing any admin route.
- **FR-024**: System MUST retain Admin event log entries for at least 30 days, with pagination to browse older entries.

### Key Entities

- **Athlete**: The user whose data is displayed; one active session per browser in v1.
- **PlannedWorkout**: A scheduled training session with date, type, distance, target pace, and Garmin sync status; source of calendar and today's workout data.
- **CompletedWorkout**: A Garmin-synced activity record with biomechanics telemetry, HR zones, and coach analysis (stored in `coach_analysis` Text column, populated by the activity poll scheduler job); source of Activity Feed data.
- **HealthSnapshot**: Daily health metrics (HRV, Body Battery, sleep duration, resting HR) from Garmin; source of Dashboard readiness cards and trend charts.
- **CoachMemory**: Persistent coach observations associated with an athlete; informs chat context.
- **ConversationMessage**: Existing per-athlete message history (SQLAlchemy model `ConversationMessage`, table `conversation_messages`) shared between the Slack coaching thread and the web Coach Chat. Web messages are appended to this same store so both channels form one unified conversation.
- **WebEvent**: A structured log entry capturing a server-side event (auth, garmin, claude, scheduler, http). Attributes: timestamp, severity (info/warn/error), category, message, athlete_id (nullable for system events). Persisted to the database for the Admin screen; also emitted to stdout. Retained for 30 days.
- **RunFeedback**: Athlete-submitted post-run feedback (feel rating, RPE, notes) associated with a CompletedWorkout; new entity requiring a DB migration.
- **WeeklyReviewSummary**: AI-generated weekly narrative and aggregate metrics (miles, elevation, avg HRV, TSS, next-week plan snapshot) written to the database by the Sunday scheduler job; source of the Weekly Review screen. New model + Alembic migration required.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Athletes can load the Dashboard and see today's readiness metrics in under 2 seconds on a local network connection.
- **SC-002**: Athletes can navigate between all 6 screens (Dashboard, Activity Feed, Training Plan, Coach Chat, Weekly Review, Onboarding) without a page reload.
- **SC-003**: The training calendar renders all weeks for the current plan cycle within 3 seconds.
- **SC-004**: Coach Chat responses are delivered within 15 seconds of sending a message under normal Claude API conditions.
- **SC-005**: Activity feedback (feel, RPE, notes) submitted via the web UI is persisted and visible in subsequent sessions.
- **SC-006**: Light/dark mode toggle applies globally in under 200ms with no layout shift.
- **SC-007**: Coach persona switching updates the greeting message and accent color within 100ms (no API call required for the switch itself).
- **SC-008**: The web dashboard renders correctly at 1280px width and above (desktop-first).
- **SC-009**: All Garmin sync attempts, Claude API calls, and auth events appear in the Admin event feed within one page load of occurring.
- **SC-010**: The Admin event feed loads the most recent 50 events in under 1 second; pagination allows browsing up to 30 days of history.

## Assumptions

- The web dashboard is desktop-first; mobile/responsive layout is out of scope for v1.
- Authentication uses username/password with a server-side session cookie; credentials are stored in the existing `Athlete` table or a dedicated web credentials store (detail deferred to planning).
- Multi-athlete login is supported via the login form — each athlete authenticates with their own credentials and sees only their own data.
- The web server runs as a separate process (`web.py`) on the same host, added as a second service in `docker-compose.yml`. Both the bot process and the web process share the same SQLite database file via a volume mount. SQLite WAL mode (`PRAGMA journal_mode=WAL`) must be enabled to handle concurrent reads from the web process while the scheduler writes.
- Triggering Garmin sync from the web UI calls the same server-side sync logic used by `!admin resync-garmin`.
- Run feedback (`RunFeedback` model) is a new data entity not currently in the backend; a new model and Alembic migration are required.
- `WeeklyReviewSummary` is a new data entity not currently in the backend; the Sunday scheduler job must be updated to persist the generated narrative and aggregates to this table in addition to sending the Slack DM.
- Chart data (HRV trend, weekly volume, body battery) is computed from existing HealthSnapshot and PlannedWorkout/CompletedWorkout records in the database.
- Coach Chat on the web calls Claude server-side using the same coach persona prompting logic as the Slack conversation handler — no client-side API key is exposed. Web messages are appended to the same `ConversationMessage` rows as Slack messages, creating a single unified thread per athlete.
- The Onboarding screen in the web UI is a visual prototype of the existing Slack onboarding flow; it does not replace the Slack onboarding in v1.
- Color palette matches the design prototype: light mode parchment (#f2ede6) / dark mode charcoal (#1c1a15); accent colors Sage (#5a8a62), Terracotta (#b8673e), Stone (#5e7e96).
- The UI framework used in the prototype (React) is the target implementation technology for the web frontend.
- The admin role is determined by a flag on the `Athlete` record (e.g., `is_admin`), defaulting to the athlete whose Slack ID matches `ADMIN_SLACK_USER_ID`.
- `WebEvent` log entries are written to both the database (for the Admin screen) and stdout (for `docker-compose logs`). The 30-day retention window is enforced by a periodic cleanup job or database TTL.

---

## Implementation Status (as of 2026-04-23)

**Feature-complete on branch `005-web-analytics-dashboard`** — all 59 tasks in `tasks.md` marked done. Awaiting final review/merge.

Shipped components:
- Flask entry point `web.py` + factory `running_coach_ai/web/app.py` (WAL mode enabled, admin seeded on startup, 5xx/unhandled-exception logging)
- Auth: `web/auth.py` with session cookies; login page at `/login`, SPA at `/app`
- APIs: `web/api/{dashboard,activities,plan,chat,review,admin}.py`
- Event log: `web/events.py` with `WebEventHandler` (namespaces: `running_coach_ai.web`, `running_coach_ai.garmin`, `running_coach_ai.coach.personas`)
- Schema: migrations `e1a2b3c4d5e6` (web credentials), `f2b3c4d5e6f7` (run feedback), `g3c4d5e6f7g8` (weekly review summary + web event), and several follow-ups
- Seed script: `scripts/set_web_credentials.py`
- Single-page dashboard: `running_coach_ai/web/static/app.html`

Post-spec enhancements not in the original spec:
- Commit `6e4fb08` applied 8 UX improvements to the dashboard HTML
- Additional migration `j6f7g8h9i0j1_add_prescription_style_to_athletes` introduced a per-athlete prescription style field consumed by the dashboard and coach prompt
- Backfill scripts `scripts/backfill_activities.py` and `scripts/backfill_workout_durations.py` were added to populate the dashboard with historical data

Known uncommitted changes as of this audit: the current working tree has in-progress edits to `web/static/app.html` and related files — see `git status` for the authoritative list.
