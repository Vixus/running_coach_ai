# Implementation Plan: Athlete Story / Magazine Spread

**Branch**: `006-magazine-features` (spec authored as `006-athlete-story` continuation)  | **Date**: 2026-05-07 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/006-athlete-story/spec.md`

## Summary

The Athlete Story feature turns key training-cycle moments into a personalized, magazine-style editorial article, optionally accompanied by athlete-uploaded photos and shareable via a public URL. It is **opt-in**, **event-triggered** (PR set, race upcoming/complete, pace recalibration, difficult week), and **adaptive** — Claude conducts 5–10 question interviews with multiple-choice answers per session and generates a 400–600 word editorial when a milestone fires. Each generated story is rendered through one of five magazine templates (Vogue / Runner's World / Times Long Read / Outside / GQ Profile) where each template encodes both a visual identity and a writing voice; auto-selection at generation time combines a trigger-affinity prior with a Claude vote, and athletes can swap templates instantly without re-billing the API. Admin endpoints let QA fire interviews and re-render stories on demand.

**Technical approach**: extend the existing Flask + APScheduler architecture with a new `coach/story.py` module owning trigger detection + Claude orchestration, three new SQLAlchemy models + one Alembic migration, a file-system template registry under `web/static/story_templates/<key>/`, an opt-in toggle + multi-page intro modal in `magazine.html`, a public Jinja2-rendered `/story/<token>` route with `noindex` + per-IP rate limit, and admin endpoints under the existing admin blueprint. No new external services in v1; AI image editing is deliberately deferred to v1.1.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Flask 3.x, SQLAlchemy 2.x, Anthropic SDK, APScheduler, Pydantic-settings, Werkzeug (file uploads), Pillow (image thumbnail validation)
**Storage**: SQLite via SQLAlchemy ORM + filesystem (`STORY_IMAGE_DIR` for athlete-uploaded photos; on Railway this is a Railway Volume same as `GARMIN_SESSION_DIR`)
**Testing**: pytest (existing suite — 322 unit + 4 integration; new tests added beside existing ones)
**Target Platform**: Linux server (Railway managed deploy + local Docker development); served behind the existing Flask app
**Project Type**: web-service (existing Flask `web.py` entrypoint + APScheduler `main.py` entrypoint sharing the same SQLite database)
**Performance Goals**: Story generation completes within 30 s of trigger (SC-S001); public story page renders within 1.5 s (SC-S002); athlete template swap completes within 500 ms (SC-S007); admin start-interview returns within 5 s (SC-S008)
**Constraints**: Per-IP 30 req/min on the public route (FR-S026); ≤10 MB per image upload (FR-S019); ≤5 photos per story (FR-S013); 1-hour regeneration cooldown for athletes (FR-S021); claude generation budget bounded by single Claude call per question + one editorial call per generation (no streaming)
**Scale/Scope**: Single-deployment self-hosted (1–N athletes), expected dozens of stories per athlete per year, hundreds of `StoryQuestion` rows; magazine template registry holds ~5 templates v1, designed to scale to ~20 with no DB or code changes

## Constitution Check

_GATE: Must pass before Phase 0 research. Re-check after Phase 1 design._

- [x] **I. Athlete Data Isolation**: Every story/question/session/image query is scoped via `scoped_query(db_session, Model, athlete_id)` per FR-S018. Public route reads by `share_token` (intentionally cross-athlete by design) and is the only un-scoped path; soft-deleted and unpublished rows return 404.
- [x] **II. AI Coach Persona Integrity**: Interview questions are issued as natural-language coach messages in the existing chat thread; the editorial body is plain prose (no XML tags surface to the athlete). Internal Claude calls follow the existing `coach.persona.call_claude` pattern with persona-block injection per `coach/personas.py`.
- [x] **III. Metric-First Architecture**: All training stats embedded in the editorial (mileage, paces, race time) are read in km/min-per-km internally and converted to miles/min-per-mile by `coach/persona.py:format_miles`/`format_pace_mi` at template render time. No imperial values stored.
- [x] **IV. Encrypted Secrets Management**: No new secrets introduced — feature reuses the existing Anthropic API key. Story image files are stored on the same protected volume as Garmin sessions.
- [x] **V. Graceful Degradation**: Claude failures during question generation abort the session cleanly with a `story_failed` notification (Edge Cases: malformed question JSON). Generation Claude failures write a `story_failed` Notification per FR-S009. Trigger-detection failures inside `_ingest_and_feedback` are caught and logged without aborting activity ingestion.
- [x] **VI. Structured Observability**: All trigger detections, session lifecycle events, generation calls, and template-render fallbacks log via the standard `logging.getLogger(__name__)` pattern with athlete_id and story_id context. No new logging stack.
- [x] **VII. Test-First Development**: Each new module ships with unit tests written before the implementation lands (per the existing project convention captured in spec acceptance scenarios). Coverage targets: >80% for `coach/story.py`, `coach/story_templates.py`, the new web blueprint(s); integration tests for the public route, the swap endpoint, and admin endpoints.
- [x] **VIII. Configuration as Code**: New `STORY_IMAGE_DIR` setting added to `running_coach_ai/config.py` Pydantic settings with default `/data/story_images/`. No other configuration introduced.

**Result**: PASS — no constitution violations. No `Complexity Tracking` entries required.

## Project Structure

### Documentation (this feature)

```text
specs/006-athlete-story/
├── plan.md              # This file
├── research.md          # Phase 0 — open architecture choices resolved
├── data-model.md        # Phase 1 — full entity field specs + migration outline
├── quickstart.md        # Existing, will be updated for the new flow
├── contracts/           # Phase 1 — API surface (athlete + admin + public)
│   ├── athlete-api.md
│   ├── admin-api.md
│   ├── public-route.md
│   └── claude-prompts.md
└── tasks.md             # Phase 2 (created by /speckit.tasks; NOT here)
```

### Source Code (repository root)

```text
running_coach_ai/
├── coach/
│   ├── story.py                 # NEW — INTERVIEW_TRIGGERS, generate_story(), session orchestration, Claude prompts
│   ├── story_templates.py       # NEW — file-system MagazineTemplate registry loader
│   ├── side_effects.py          # EXTENDED — answer-capture hook in conversation flow when StoryQuestion is pending
│   └── ...                      # (existing modules unchanged)
├── database/
│   ├── models.py                # EXTENDED — AthleteStory, StoryInterviewSession, StoryQuestion, StoryImage rows
│   │                            #   + Athlete.story_opt_in, Athlete.story_intro_seen_at columns
│   └── migrations/versions/
│       └── p2l3m4n5o6p7_add_athlete_stories.py  # NEW — single migration for all four new tables + 2 columns
├── scheduler/
│   ├── activity_poll.py         # EXTENDED — pr_set / pace_recalibration / difficult_week detection after each ingest
│   └── morning.py               # EXTENDED — race_upcoming detection (7d before Goal.race_date)
└── web/
    ├── api/
    │   ├── stories.py           # NEW — athlete blueprint: GET /api/stories, GET /api/stories/current,
    │   │                        #   POST /api/stories/<id>/template, POST /api/stories/<id>/unpublish,
    │   │                        #   DELETE /api/stories/<id>, POST /api/stories/current/images,
    │   │                        #   PATCH /api/stories/images/<id>, DELETE /api/stories/images/<id>
    │   ├── admin.py             # EXTENDED — POST /api/admin/athletes/<id>/generate-story,
    │   │                        #   POST /api/admin/athletes/<id>/start-interview,
    │   │                        #   POST /api/admin/stories/<id>/render,
    │   │                        #   DELETE /api/admin/stories/<id>?hard=1
    │   └── ...                  # (existing blueprints unchanged)
    ├── routes/
    │   └── public_story.py      # NEW — public Jinja2 route GET /story/<token>, GET /robots.txt
    ├── static/
    │   ├── magazine.html        # EXTENDED — opt-in toggle, intro modal, "My Story" section, swap carousel
    │   ├── magazine.css         # EXTENDED — selectors for the new sections
    │   ├── magazine.js          # EXTENDED — modal flow, image upload UI, template swap UI
    │   └── story_templates/     # NEW DIRECTORY — file-system template registry
    │       ├── vogue/
    │       │   ├── template.json
    │       │   ├── cover.html
    │       │   ├── inside.html
    │       │   ├── template.css
    │       │   └── thumbnail.jpg
    │       ├── runners_world/
    │       ├── times_long_read/
    │       ├── outside/
    │       └── gq_profile/
    └── templates/
        └── story_page.html      # NEW — outer Jinja2 wrapper that includes the chosen template's cover.html + inside.html
tests/
├── unit/
│   ├── test_story_triggers.py           # NEW — pr/race/pace/difficult-week detection
│   ├── test_story_session.py            # NEW — opt-in gate, consent flow, adaptive Q&A loop
│   ├── test_story_generation.py         # NEW — Claude editorial call + auto-selection
│   ├── test_story_templates.py          # NEW — registry loader, fallback behavior
│   └── web/
│       ├── test_stories_api.py          # NEW — athlete endpoints
│       ├── test_admin_story_api.py      # NEW — admin endpoints
│       └── test_public_story_route.py   # NEW — share-token routing, rate limit, robots.txt
└── integration/
    └── web/
        └── test_story_flow.py           # NEW — full opt-in → trigger → interview → generate → render flow
```

**Structure Decision**: The story feature integrates into the existing **single-package web-service** layout (`running_coach_ai/{coach,scheduler,web,database,...}/`). One new module per concern (trigger detection + Claude orchestration in `coach/story.py`, registry loader in `coach/story_templates.py`), one new API blueprint (`web/api/stories.py`), one new public route module (`web/routes/public_story.py`), and a new sibling directory under `web/static/` for the template registry. **No new top-level packages, no new processes, no new external services** — the feature reuses the existing Flask app factory, the existing scheduler entry points, and the existing SQLite database with one Alembic migration.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

_None — the design passes all constitution gates._

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| _N/A_     | _N/A_      | _N/A_                                |
