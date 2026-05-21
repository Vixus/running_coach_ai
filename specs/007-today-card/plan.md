# Implementation Plan: Today Card

**Branch**: `007-today-card` | **Date**: 2026-05-15 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/007-today-card/spec.md`

## Summary

Replace the decorative `<section id="hero">` at the top of the magazine dashboard with a magazine-cover Today Card that answers the runner's single morning question — *"what am I running today, and why?"* — in the coach's voice. The card morphs through five states (`PRE_RUN`, `COMPLETED`, `REST_DAY`, `RACE_DAY`, `NO_PLAN`) driven by a new `GET /api/today` endpoint that performs only reads against existing models (`Goal`, `PlannedWorkout`, `CompletedWorkout`, `HealthSnapshot`, `Notification`). A day with an active Goal but no PlannedWorkout row resolves to REST_DAY (recovery day with cues), not a separate alarm state. The rationale is sourced in priority order from the morning_checkin notification body, the post-run `coach_analysis`, a rule-based two-branch fallback (with vs. without overnight watch data), and persona-static text for terminal states.

Two cross-cutting prompt updates are part of this work: the `MORNING_CHECKIN_PROMPT` in `coach/adapter.py:17` and the `POST_RUN_FEEDBACK_PROMPT` in `coach/feedback.py:14` both gain an athlete-centered single-paragraph rationale directive — the source of the coach voice the new card will display. No new database migrations, no new background jobs, no new Claude calls at request time.

## Technical Context

**Language/Version**: Python 3.11+ (backend), ES2020 vanilla JS (frontend — no framework). Type hints used throughout backend.
**Primary Dependencies**: Flask 3.x (web), SQLAlchemy 2.x (DB ORM), Anthropic Python SDK (Claude). No new dependencies introduced by this feature.
**Storage**: SQLite via SQLAlchemy with WAL mode (existing). Frontend uses `localStorage` for the Today Card cache (FR-028a/b) — no new DB tables, no new migrations.
**Testing**: pytest with in-memory SQLite for backend integration tests; pure-function unit tests for `coach/today_rationale.py`. No new test infrastructure. External services (Claude, Garmin) are not touched by the new endpoint at request time, so no mocking gymnastics are required.
**Target Platform**: Linux Docker container (Synology NAS deployment); athlete-facing surface is a responsive web page (mobile-first at 375px through desktop ≥1024px). Existing magazine.html, magazine.css, magazine.js are the only frontend files modified.
**Project Type**: Web service + static frontend (this is the existing pattern for `running_coach_ai/web/`).
**Performance Goals**: `/api/today` p95 latency <100ms with at most 6 indexed DB queries and zero Claude API calls (FR-004). Frontend polls every 60s while tab is visible (FR-026).
**Constraints**: No new DB migrations; no new background jobs; no new Claude calls at request time; athlete data isolation via `scoped_query()`; all "today" computation uses `athlete.timezone` (athlete-local, not server-UTC).
**Scale/Scope**: One athlete = one card per page load + ~60 background polls/hour while tab is visible. Multi-athlete scale already proven by existing endpoints sharing the same SQLite DB.

## Constitution Check

_GATE: Must pass before Phase 0 research. Re-check after Phase 1 design._

- [x] **I. Athlete Data Isolation**: All queries in `web/api/today.py` use `scoped_query(db_session, Model, athlete_id)`. WebEvent emission scoped per athlete. **PASS** (Initial + Post-Design)
- [x] **II. AI Coach Persona Integrity**: No new conversational surface; the new endpoint reads from existing Claude-generated bodies. Two existing Claude prompts (`MORNING_CHECKIN_PROMPT`, `POST_RUN_FEEDBACK_PROMPT`) gain an athlete-centered single-paragraph directive that strengthens persona consistency. No XML tag changes. **PASS** (Initial + Post-Design)
- [x] **III. Metric-First Architecture**: All distances/paces continue to be stored in km / min-km internally; the Today Card converts to miles / min-mile only at the API response boundary using existing helpers (`km_to_mi`, `format_pace_mi`). **PASS** (Initial + Post-Design)
- [x] **IV. Encrypted Secrets Management**: No new secrets; no Garmin credential interaction. **PASS (N/A)** (Initial + Post-Design)
- [x] **V. Graceful Degradation**: Endpoint never crashes — FR-005/FR-006/FR-007/FR-007a/FR-007b/FR-008 prescribe a complete fallback ladder. Frontend FR-028b/FR-028c/FR-028d prescribe stale-rendering and skeleton-fallback so transient failures self-heal silently. **PASS** (Initial + Post-Design)
- [x] **VI. Structured Observability**: FR-032a/b prescribe a `today.state_transition` WebEvent on state changes only, scoped per athlete and integrated with the existing `WebEventHandler` dual-sink logger. **PASS** (Initial + Post-Design)
- [x] **VII. Test-First Development**: FR-033 (integration tests covering all 5 states across 6 user stories, including the no-planned-row → REST_DAY regression), FR-034 (unit tests for two-branch rule-based fallback + paragraph extraction), FR-035 (in-memory SQLite, no external network). **PASS** (Initial + Post-Design)
- [x] **VIII. Configuration as Code**: No new environment variables; persona accent colors and race-morning greetings live in code on the `CoachPersona` dataclass (FR-031). **PASS** (Initial + Post-Design)

**No violations to justify.** Complexity Tracking section omitted.

## Project Structure

### Documentation (this feature)

```text
specs/007-today-card/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output — resolves 3 investigation items from spec
├── data-model.md        # Phase 1 output — CoachPersona field additions + WebEvent kind
├── quickstart.md        # Phase 1 output — manual verification recipe
├── contracts/
│   └── today-api.md     # Phase 1 output — /api/today response shape per state
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
running_coach_ai/
├── coach/
│   ├── personas.py                  # MODIFY: add accent_color, race_morning_greeting to CoachPersona dataclass + populate for classic/maya/jordan
│   ├── adapter.py                   # MODIFY: MORNING_CHECKIN_PROMPT gains athlete-centered single-paragraph directive (FR-009)
│   ├── feedback.py                  # MODIFY: POST_RUN_FEEDBACK_PROMPT gains athlete-centered directive (FR-010)
│   └── today_rationale.py           # NEW: rule_based_morning() two-branch fallback + extract_rationale_paragraph()
├── web/
│   ├── app.py                       # MODIFY: register today blueprint in create_app()
│   ├── api/
│   │   └── today.py                 # NEW: GET /api/today endpoint, state resolver, WebEvent emission
│   └── static/
│       ├── magazine.html            # MODIFY: replace <section id="hero"> with <section id="today">
│       ├── magazine.css             # MODIFY: replace #hero styles with #today styles (mobile-first)
│       └── magazine.js              # MODIFY: hydrateToday(), tap handlers, polling, localStorage cache, chat tap-outside dismissal
└── database/
    └── models.py                    # NO CHANGE — no new tables, no new columns

tests/
├── integration/web/
│   └── test_today_api.py            # NEW: integration tests for all 5 states + no-planned-row regression + fallback ladder + WebEvent emission
└── unit/
    └── test_today_rationale.py      # NEW: unit tests for two-branch fallback + paragraph extraction
```

**Structure Decision**: Web service pattern (existing). The new endpoint joins `running_coach_ai/web/api/` alongside the existing 8 blueprints. The new rationale module joins `running_coach_ai/coach/` alongside `prompt.py`, `adapter.py`, `feedback.py`, `personas.py` — consistent with the existing split where each module owns one piece of coaching surface.

## Complexity Tracking

> No constitution violations; no justifications required.

---

## Phase Outputs

- **Phase 0 (research.md)** — resolves the 3 investigation items parked in the spec's Open Questions section and pins persona accent colors.
- **Phase 1 (data-model.md, contracts/today-api.md, quickstart.md)** — documents CoachPersona field additions, the /api/today response shape per state, and a manual verification recipe.
- **Agent context** — refreshed via `update-agent-context.ps1 -AgentType claude`.

## Post-Design Constitution Re-Check

After completing data-model.md and contracts/today-api.md, all 8 principles still pass:

- Data isolation: confirmed via contract — every response field traces back to `athlete_id`-scoped queries.
- Persona integrity: confirmed — the rationale field's `coach` byline always reflects the athlete's currently active persona.
- Metric-first: confirmed — `cover_lines[].value` for distance/pace fields are emitted in imperial; internal queries stay metric.
- Encrypted secrets: N/A.
- Graceful degradation: confirmed — contract specifies fallback values for every state when source data is missing.
- Observability: confirmed — contract specifies the WebEvent payload shape.
- Test-first: confirmed — quickstart.md includes the verification commands; FR-033/034/035 define test coverage.
- Configuration as code: confirmed — no new env vars; new persona fields are dataclass attributes.

**Phase 2 (/speckit.tasks) is the next phase, not produced by /speckit.plan.**
