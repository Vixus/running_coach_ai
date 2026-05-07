---

description: "Tasks for Athlete Story / Magazine Spread (006-athlete-story)"
---

# Tasks: Athlete Story / Magazine Spread

**Input**: Design documents from `/specs/006-athlete-story/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/{athlete-api,admin-api,public-route,claude-prompts}.md, quickstart.md

**Tests**: Mandatory per Constitution Principle VII (Test-First Development). Each user story includes unit tests before implementation; integration tests cover API surfaces and end-to-end flows.

**Organization**: Tasks are grouped by user story so each can be implemented and tested independently and shipped as an incremental delivery.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Parallelizable (different file, no dependency on incomplete tasks)
- **[Story]**: User-story tag (US1, US2, ..., US7) for tasks inside a user-story phase
- File paths are absolute or repo-relative

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add the new Pydantic config + library dependency before any model or migration touches the project.

- [X] T001 Add `Pillow>=10` to `requirements.txt` (validates uploaded images per FR-S019)
- [X] T002 Add `STORY_IMAGE_DIR: Path = Path("/data/story_images/")` to `running_coach_ai/config.py` Pydantic `Settings` class (default per data-model.md §9)
- [X] T003 Add `STORY_IMAGE_DIR` to `.env.example` and document the Railway-Volume requirement in a one-line comment

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, registry, and shared infrastructure that EVERY user story depends on. No user-story phase can begin until this is complete.

⚠️ **CRITICAL**: All P1 stories depend on these tasks landing first.

### Tests for foundational layer

- [X] T004 [P] Unit test for the magazine-template registry loader in `tests/unit/test_story_templates.py` (asserts: registry loads 5 keys from `web/static/story_templates/`, malformed templates are skipped + logged, fallback to `vogue` when a requested key is missing)
- [X] T005 [P] Unit test for the trigger-priority resolver in `tests/unit/test_story_triggers.py` (asserts the priority order from spec Edge Cases: `race_complete > race_upcoming > pr_set > pace_recalibration > difficult_week`)

### Database — models + migration

- [X] T006 Extend `running_coach_ai/database/models.py` with `Athlete.story_opt_in` (Bool, default False) and `Athlete.story_intro_seen_at` (DateTime, nullable) per data-model.md §1
- [X] T007 Add `AthleteStory` model to `running_coach_ai/database/models.py` with all 14 columns + 3 indexes per data-model.md §2 (CHECK constraint on `milestone_type`)
- [X] T008 Add `StoryInterviewSession` model to `running_coach_ai/database/models.py` with all 8 columns + 2 indexes per data-model.md §3 (CHECK constraint on `trigger_kind`)
- [X] T009 Add `StoryQuestion` model to `running_coach_ai/database/models.py` with all 11 columns + 2 indexes + UNIQUE(`session_id`, `question_index`) per data-model.md §4
- [X] T010 Add `StoryImage` model to `running_coach_ai/database/models.py` with all 6 columns + index per data-model.md §5
- [X] T011 Generate Alembic migration `running_coach_ai/database/migrations/versions/p2l3m4n5o6p7_add_athlete_stories.py` matching data-model.md §8 (single migration: 2 athlete columns + 4 new tables + 7 indexes + 2 CHECK constraints)
- [X] T012 Apply migration locally (`alembic upgrade head`) and verify schema with `python -c "from running_coach_ai.database.models import Base; print([t.name for t in Base.metadata.sorted_tables])"`

### Magazine-template registry — module + the default template

- [X] T013 Create `running_coach_ai/coach/story_templates.py` exposing `load_registry() -> dict[str, MagazineTemplate]`, `get_template(key)`, `list_templates()`, `is_registered(key)`. Loads at Flask app startup; caches on `current_app.extensions["story_templates"]`. Skips malformed entries with a logged warning per research.md R-003.
- [X] T014 [P] Create `running_coach_ai/web/static/story_templates/vogue/template.json` with `key`, `display_name`, `voice_description` (glossy, aspirational, present-tense scene-setting), `trigger_affinities: ["race_complete", "pr_set"]`, `thumbnail` per FR-S032
- [X] T015 [P] Create `running_coach_ai/web/static/story_templates/vogue/cover.html` (Jinja2 fragment — full-bleed cover image with overlay headline, drop cap, oversized DM Serif Display, athlete name + milestone label)
- [X] T016 [P] Create `running_coach_ai/web/static/story_templates/vogue/inside.html` (Jinja2 fragment — 2-column body, mid-article pull quote, photo grid)
- [X] T017 [P] Create `running_coach_ai/web/static/story_templates/vogue/template.css` (selectors namespaced under `.tpl-vogue`; paper/ink palette matching the magazine; vignette + duotone CSS treatments for `.tpl-vogue .photo`)
- [X] T018 [P] Create `running_coach_ai/web/static/story_templates/vogue/thumbnail.jpg` (320×400 placeholder for now — final art generated during US6 polish)
- [X] T019 Wire `load_registry()` into `running_coach_ai/web/app.py:create_app()` so it runs once at startup; log the registered keys at INFO

### Conversation hook + helpers shared by all stories

- [X] T020 Create `running_coach_ai/coach/story.py` skeleton with `format_trigger_label(trigger_kind, context, athlete, db) -> str` (pure Python labels per research.md R-009: e.g. "the Brooklyn Half on May 4", "your fastest 10K so far") and the `INTERVIEW_TRIGGERS` list constant. No Claude calls yet.
- [X] T021 Add `fire_trigger_if_eligible(athlete, trigger_kind, context, db) -> StoryInterviewSession | None` to `running_coach_ai/coach/story.py` enforcing: opt-in gate, intro-modal-seen gate, no-active-session gate, trigger-priority dedup (24h window) per research.md R-004. Returns the session row on success.
- [X] T022 Extend `running_coach_ai/coach/conversation.py:_handle_message_core` with an early hook: if there's an unanswered `StoryQuestion` for this athlete (oldest first), capture the user's reply into `answer` + `answered_at` + `is_custom_answer=True` and short-circuit out of the regular Claude turn. Helper goes in `coach/story.py:capture_chat_answer(athlete, text, db)`. Per research.md R-008.

**Checkpoint**: Database schema is live, the magazine-template registry loads `vogue`, the conversation hook captures free-text answers when a question is pending, and the trigger-eligibility gates exist. User-story phases can now begin in parallel.

---

## Phase 3: User Story 1 — Opt-in, Event-Triggered Interview Sessions (Priority: P1) 🎯 MVP part 1

**Goal**: Athlete opts in, sees the intro modal, and on the next qualifying training event Claude opens an interview session and runs an adaptive 5–10 question Q&A in the chat panel.

**Independent Test**: With `Athlete.story_opt_in=False`, simulate any trigger and verify NO `StoryInterviewSession` row is created. Set `story_opt_in=True`, simulate a trigger, verify a session is created and the first question (with options) is delivered as a chat message. Submit answers; verify follow-up questions are generated based on prior answers until the session closes (5–10 questions).

### Tests for User Story 1

- [X] T023 [P] [US1] Unit tests for trigger detection helpers in `tests/unit/test_story_triggers.py`: `detect_pr_set`, `detect_pace_recalibration`, `detect_race_complete`, `detect_race_upcoming`, `detect_difficult_week`. Mock `CompletedWorkout`/`Goal`/`HealthSnapshot` rows. Each test asserts both positive and negative cases.
- [X] T024 [P] [US1] Unit tests for `fire_trigger_if_eligible` in `tests/unit/test_story_session.py`: opt-in=False blocks, intro_seen_at=NULL blocks, active session blocks, priority collision blocks, success path returns the row.
- [X] T025 [P] [US1] Unit tests for `coach.story.next_question` (C-001 prompt) in `tests/unit/test_story_session.py`: mocks `call_claude` to return a known JSON payload, asserts the row is written with the right options, retry-once on malformed JSON, abort with `story_failed` notification on second failure.
- [X] T026 [P] [US1] Unit tests for `capture_chat_answer` in `tests/unit/test_story_session.py`: free-text answer populates `is_custom_answer=True`, second message after a closed session passes through to regular coaching.
- [X] T027 [P] [US1] Integration test for the full flow in `tests/integration/web/test_story_flow.py`: athlete sets `story_opt_in=True` via `POST /api/stories/opt-in`, acknowledges the modal via `POST /api/stories/intro-acknowledged`, admin fires `POST /api/admin/athletes/<id>/start-interview?trigger=pr_set`, athlete sends a chat message, the answer is captured, the next question is delivered. (The admin endpoint itself is implemented in US7; mock it in US1's integration test if US7 hasn't landed yet.)

### Trigger detection — wired into existing pipelines

- [X] T028 [P] [US1] Implement `detect_pr_set(completed: CompletedWorkout, db) -> bool` and `detect_pace_recalibration(athlete, applied_plan_diff, db) -> bool` in `running_coach_ai/coach/story.py`
- [X] T029 [P] [US1] Implement `detect_race_complete(completed, db) -> Goal | None`, `detect_race_upcoming(athlete, today, db) -> Goal | None`, and `detect_difficult_week(athlete, db) -> bool` in `running_coach_ai/coach/story.py` per research.md R-004
- [X] T030 [US1] Wire trigger detection into `running_coach_ai/scheduler/activity_poll.py:_ingest_and_feedback` after biomechanics analysis: call `detect_pr_set` and `detect_race_complete`, then `fire_trigger_if_eligible` for matches. Wrap in try/except so trigger failures never abort activity ingestion (Constitution V).
- [X] T031 [US1] Wire trigger detection into `running_coach_ai/scheduler/morning.py:_run_morning_checkin_for_athlete` for `race_upcoming` (7d before `Goal.race_date`) and `difficult_week`. Same try/except envelope.
- [X] T032 [US1] Wire `detect_pace_recalibration` into `running_coach_ai/coach/side_effects.py:extract_and_apply_plan` after a `<plan>` block applies a faster pace to ≥3 sessions; fire `pace_recalibration` trigger.

### Adaptive Q&A — Claude prompt C-001 implementation

- [X] T033 [US1] Add `next_question(session, db) -> StoryQuestion | None` to `running_coach_ai/coach/story.py`: builds the C-001 system prompt per `contracts/claude-prompts.md`, calls `call_claude(max_tokens=512)`, parses the JSON, validates options (2–4 strings), creates the `StoryQuestion` row, returns it. Returns `None` and closes the session when `session_complete=true`. Retry-once on malformed JSON; on second failure write `story_failed` notification + close session.
- [X] T034 [US1] Add session-close helper `close_session(session, db, *, skipped=False)` to `running_coach_ai/coach/story.py` setting `completed_at = now()` and `skipped` flag.
- [X] T035 [US1] Add 7-day stalled-session sweep in `running_coach_ai/scheduler/morning.py:_run_morning_checkin_for_athlete`: for any `StoryInterviewSession` with `completed_at IS NULL` and last `StoryQuestion.asked_at > 7 days ago`, close it with `skipped=False` per spec Edge Cases.

### Athlete API endpoints

- [X] T036 [P] [US1] Create `running_coach_ai/web/api/stories.py` blueprint scaffolding (`bp = Blueprint("stories", __name__)`, `@login_required` decorator wiring), register in `running_coach_ai/web/app.py`
- [X] T037 [US1] Implement `POST /api/stories/opt-in` in `running_coach_ai/web/api/stories.py` per athlete-api.md (toggles `Athlete.story_opt_in`; on opt-out, closes any active session with `skipped=True`)
- [X] T038 [US1] Implement `POST /api/stories/intro-acknowledged` in `running_coach_ai/web/api/stories.py` per athlete-api.md (sets `Athlete.story_intro_seen_at = now()`)
- [X] T039 [US1] Implement `POST /api/stories/sessions/<session_id>/respond` in `running_coach_ai/web/api/stories.py` per athlete-api.md (validates question is unanswered + in this session, records answer with `is_custom_answer=False`, calls `next_question`, returns `{ok, session_complete, next_question}`)
- [X] T040 [US1] Implement `POST /api/stories/sessions/<session_id>/decline` in `running_coach_ai/web/api/stories.py` per athlete-api.md (closes session with `skipped=True`)
- [X] T041 [US1] Extend `GET /api/magazine` response (in `running_coach_ai/web/api/magazine.py`) with the `story` block per athlete-api.md "Magazine bundle additions": `feature_enabled`, `needs_intro_modal`, plus `current_story` placeholder (returns `null` until US3 lands).

### Magazine UI — opt-in, intro modal, chat-panel options buttons

- [X] T042 [P] [US1] Add a "Story" settings toggle to `running_coach_ai/web/static/magazine.html` (gear-icon menu in the top-nav) wired to `POST /api/stories/opt-in`. CSS in `magazine.css`, JS in `magazine.js`.
- [X] T043 [US1] Add the multi-page intro modal (`#story-intro-modal`) to `running_coach_ai/web/static/magazine.html` per research.md R-007: 5 slides with Prev/Next buttons + final "I'm ready" calling `POST /api/stories/intro-acknowledged`. Modal CSS in `magazine.css`; logic in `magazine.js` (~80 LOC, no dependencies).
- [X] T044 [P] [US1] Author SVG infographics + CSS keyframe animations for each of the 5 modal pages in `running_coach_ai/web/static/magazine.css` (1: feature welcome, 2: when questions appear, 3: how answers are used, 4: opt-out anytime, 5: privacy & sharing). Animations use the existing `magazine.css` pattern (terrain lines, ring-pulse).
- [X] T045 [US1] Render multiple-choice option buttons inside the chat panel in `running_coach_ai/web/static/magazine.js` (`cpAdd` extension): when an inbound coach message includes story options metadata, render 2–4 clickable buttons calling `POST /api/stories/sessions/<id>/respond`. Free-text fallback uses the existing chat input.

**Checkpoint**: User can opt in, see the intro modal, and complete an event-triggered interview session. Story rows accumulate Q&A pairs but are not yet rendered as editorials — that comes in US3.

---

## Phase 4: User Story 3 — Editorial Generation and Preview (Priority: P1) 🎯 MVP part 2

**Goal**: A `race_complete` milestone fires, the system collects all answered Q&As + training stats + history, and Claude generates a 400–600 word editorial in the auto-selected magazine template's voice. Athlete can preview and (later) publish.

**Independent Test**: Stub `call_claude` to return a known editorial body + template_key. Call `generate_story(athlete, "race_complete", db)`; verify `AthleteStory` row is written with the stubbed text, a 12-char `share_token`, `template_key=<stub>`, `template_locked_by_athlete=False`, and a `story_ready` notification. Visit `/story/<token>?preview=1` while logged in as the owning athlete; verify the editorial renders in the chosen template's layout.

### Tests for User Story 3

- [X] T046 [P] [US3] Unit tests for `generate_story` in `tests/unit/test_story_generation.py`: stubs `call_claude` for the multi-template C-002 prompt, asserts the row is created with correct fields (including `template_key` from Claude's vote and `template_locked_by_athlete=False`), all linked sessions get `story_id` set, `story_ready` notification on success, `story_failed` notification on Claude parse failure. The locked-voice path is intentionally out of scope here — covered by T060 in US6 since it depends on the swap endpoint setting `template_locked_by_athlete=True`.
- [X] T047 [P] [US3] Unit test for the regeneration cooldown in `tests/unit/test_story_generation.py`: calling `regenerate_story` within 1 hour of `last_regenerated_at` raises `RegenerationCooldownError`; admin-triggered regen bypasses.
- [X] T048 [P] [US3] Unit test for the auto-selection fallback in `tests/unit/test_story_generation.py`: when Claude votes a `template_key` not in the registry, fall back to highest-affinity template per spec Edge Cases.
- [X] T049 [P] [US3] Integration test for milestone-triggered generation in `tests/integration/web/test_story_flow.py`: opt-in athlete with answered questions logs a race-day activity, `_ingest_and_feedback` fires both the session-trigger AND, if generation conditions are met, queues `generate_story`. Asserts a `StoryReady` notification appears in the inbox.

### Generation logic — Claude prompt C-002

- [X] T050 [US3] Add `generate_story(athlete, milestone_type, db) -> AthleteStory` to `running_coach_ai/coach/story.py`: collects all answered `StoryQuestion` rows from completed sessions for this athlete since the last published story, gathers training stats (km internally, surfaced as miles via `format_miles`; race time; weeks trained; HRV trend), builds the **multi-template** C-002 system prompt per `contracts/claude-prompts.md` (includes every registered template's key + voice + trigger affinities), calls `call_claude(max_tokens=4096)`, parses + validates the `{editorial_body, template_key}` JSON (fall back to highest-affinity template if Claude votes a key not in the registry — see spec Edge Cases), creates the `AthleteStory` row with `share_token = secrets.token_urlsafe(12)[:12]`, persists the chosen `template_key` with `template_locked_by_athlete=False`, sets `story_id` on all linked sessions + their questions, writes a `story_ready` notification on success or a `story_failed` notification on Claude parse/timeout/error per FR-S009.
- [X] T051 [US3] Add `regenerate_story(story, *, by_admin=False) -> AthleteStory` to `running_coach_ai/coach/story.py`: enforces 1-hour cooldown (raises `RegenerationCooldownError` returning the seconds remaining) unless `by_admin=True`; reuses the multi-template C-002 prompt path from `generate_story` (the locked-voice branch is added by T068 in US6 — at US3 time `template_locked_by_athlete` is always False since the swap endpoint that sets it doesn't yet exist, so this branch is unreachable until US6); overwrites `editorial_body`, increments `regeneration_count`, sets `last_regenerated_at`. Preserves `share_token`, `created_at`, `published_at`.
- [X] T052 [US3] Wire automatic generation in `running_coach_ai/scheduler/activity_poll.py:_ingest_and_feedback`: after `detect_race_complete`, if the athlete has ≥4 answered questions across linked sessions, call `generate_story`. Wrap in try/except so generation failures never abort activity ingestion.

### Athlete API — list, current, single, regenerate, publish/unpublish/delete

- [X] T053 [P] [US3] Implement `GET /api/stories` and `GET /api/stories/current` in `running_coach_ai/web/api/stories.py` per athlete-api.md (list excludes soft-deleted; current returns most recent draft/unpublished/published or `{"story": null}`)
- [X] T054 [P] [US3] Implement `POST /api/stories/<id>/regenerate` per athlete-api.md (calls `regenerate_story`; returns 429 with `retry_after` on cooldown)
- [X] T055 [P] [US3] Implement `POST /api/stories/<id>/publish` and `POST /api/stories/<id>/unpublish` per athlete-api.md
- [X] T056 [P] [US3] Implement `DELETE /api/stories/<id>` (soft-delete) per athlete-api.md

### Magazine UI — "My Story" section

- [X] T057 [US3] Add `<section id="mystory">` to `running_coach_ai/web/static/magazine.html`: cover image (or placeholder gradient), title, editorial preview (first 120 chars), Share / Regenerate / Unpublish / Delete actions. Hidden when `feature_enabled=false` or `current_story=null`.
- [X] T058 [US3] Wire the "My Story" section in `running_coach_ai/web/static/magazine.js`: hydrate from `GET /api/stories/current`, refresh after each action, show cooldown timer for the regenerate button using `can_regenerate_at`.

**Checkpoint**: Generation works end-to-end. Athletes see their stories in `/magazine`. The editorial renders in the auto-selected template (only `vogue` exists at this point, but the wiring is complete). MVP achieved — every other story enriches what's there.

---

## Phase 5: User Story 6 — Magazine Template Selection and Swap (Priority: P2)

**Goal**: System auto-selects a template at generation time. Athlete can browse all 5 templates in a "Try a different look" carousel and lock in any one. Adding a sixth template is just dropping a directory.

**Independent Test**: Generate a story; assert `template_key` is one of the 5 registered keys. POST a different valid key to `/api/stories/<id>/template`; assert `template_key` updates, `template_locked_by_athlete=True`, public URL re-renders without a Claude call.

### Tests for User Story 6

- [X] T059 [P] [US6] Unit test for the auto-selection result in `tests/unit/test_story_generation.py`: stubs Claude to vote `outside`; asserts `template_key="outside"`. Then stubs Claude to vote `nonexistent`; asserts fallback to highest-affinity for the trigger.
- [X] T060 [P] [US6] Unit test for the template-lock voice override in `tests/unit/test_story_generation.py`: with `template_locked_by_athlete=True`, the C-002 system prompt sent to Claude omits the templates list and injects only the locked voice description.
- [X] T061 [P] [US6] Unit/web test for `POST /api/stories/<id>/template` in `tests/unit/web/test_stories_api.py`: valid key updates row + sets lock flag; invalid key returns 400 with `available` list; not-owner returns 404.

### Four additional templates

- [X] T062 [P] [US6] Create `running_coach_ai/web/static/story_templates/runners_world/{template.json,cover.html,inside.html,template.css,thumbnail.jpg}` — practical reportage voice, 3-column body with stats sidebar, paper/sage palette, Bebas headlines + Inter body
- [X] T063 [P] [US6] Create `running_coach_ai/web/static/story_templates/times_long_read/{template.json,cover.html,inside.html,template.css,thumbnail.jpg}` — third-person observational, newsprint feel, narrow column with generous leading, hairline rules + drop cap + italic kicker
- [X] T064 [P] [US6] Create `running_coach_ai/web/static/story_templates/outside/{template.json,cover.html,inside.html,template.css,thumbnail.jpg}` — adventure prose voice, large landscape cover, route-map graphic if GPS available, earth-tone palette (paper/sage/terracotta)
- [X] T065 [P] [US6] Create `running_coach_ai/web/static/story_templates/gq_profile/{template.json,cover.html,inside.html,template.css,thumbnail.jpg}` — polished journalistic voice, matte black-and-white cover with single hero portrait, Bebas headline in caps, 2-column body with marginalia (stats inset)

### Swap endpoint + UI

- [X] T066 [US6] Implement `POST /api/stories/<id>/template` in `running_coach_ai/web/api/stories.py` per athlete-api.md (validates key, updates row, sets `template_locked_by_athlete=True`; returns `{ok, preview_url, template_key}` or 400 with `available` list)
- [X] T067 [US6] Add the "Try a different look" carousel to the `<section id="mystory">` block in `running_coach_ai/web/static/magazine.html`: thumbnails of all registered templates pulled from `available_templates` in `GET /api/stories/current`. Logic in `magazine.js` posts to the swap endpoint and re-renders the preview iframe.
- [X] T068 [US6] Add the locked-voice branch to `coach/story.py:generate_story` and `regenerate_story`: when `template_locked_by_athlete=True`, build the **locked-voice** C-002 system prompt variant per `contracts/claude-prompts.md` (omits the templates list + trigger-affinity prior; injects only the locked template's `voice_description`), and parses the `{editorial_body}`-only response (no `template_key` key expected in the locked path). Wire the same branch in `regenerate_story` so locked stories preserve their voice across regenerations per FR-S035. (T050's multi-template path is already implemented from US3.)

**Checkpoint**: All 5 templates are live, auto-selection works, athletes can swap, and the lock flag carries through future regenerations.

---

## Phase 6: User Story 2 — Photo Upload in "My Story" Section (Priority: P2)

**Goal**: Athlete uploads up to 5 photos per story (≤10 MB, jpeg/png/webp), captions them, reorders, deletes. Photos appear in the magazine spread per the active template's photo grid.

**Independent Test**: POST a valid 5 MB JPEG to `/api/stories/current/images`; assert `StoryImage` row + file on disk under `STORY_IMAGE_DIR/<story_id>/`. POST a 6th; assert 400 "Maximum 5 photos per story". POST an oversized file; assert 400 "File exceeds 10 MB". POST a non-image file; assert 400 "Unsupported file type".

### Tests for User Story 2

- [X] T069 [P] [US2] Unit/web tests for image upload in `tests/unit/web/test_stories_api.py`: valid upload creates row + file, oversized rejected, wrong MIME rejected, 6th rejected, non-decodable rejected (Pillow `UnidentifiedImageError`)
- [X] T070 [P] [US2] Unit/web test for caption PATCH and image DELETE in `tests/unit/web/test_stories_api.py`: caption updates persist, delete removes both row and file, owner-scope enforced (404 on cross-athlete attempt)

### Image API endpoints + storage

- [X] T071 [US2] Implement `POST /api/stories/current/images` in `running_coach_ai/web/api/stories.py` per athlete-api.md: multipart form, MIME + size + Pillow decode validation, count check (≤5), UUID-prefixed filename, `os.makedirs(STORY_IMAGE_DIR/<story_id>/, exist_ok=True)`, write file, insert row
- [X] T072 [US2] Implement `GET /api/stories/images/<id>/raw` in `running_coach_ai/web/api/stories.py` per athlete-api.md (owner-scoped, `send_file` with the right Content-Type)
- [X] T073 [US2] Implement `PATCH /api/stories/images/<id>` (caption + sort_order) and `DELETE /api/stories/images/<id>` (removes file + row) in `running_coach_ai/web/api/stories.py`
- [X] T074 [US2] Add `os.makedirs(STORY_IMAGE_DIR, exist_ok=True)` to `create_app()` in `running_coach_ai/web/app.py`
- [X] T075 [US2] Update `cover_image_path` on `AthleteStory` whenever the first image (lowest `sort_order`) changes — set it on insert/delete/sort-reorder via a small helper in `running_coach_ai/coach/story.py:refresh_cover_image_path(story, db)`

### Magazine UI — upload widget + thumbnails

- [X] T076 [US2] Add an upload widget + thumbnail grid + caption inline-edit + delete button to the `<section id="mystory">` block in `running_coach_ai/web/static/magazine.html`. JS in `magazine.js` for drag-drop + progress, optimistic thumbnail render, `PATCH` on caption blur, `DELETE` on trash icon.
- [X] T077 [US2] Update each of the 5 templates' `inside.html` to render a photo grid from the `images` context list (4–5 photos max in the layout; layout-specific styling in each `template.css`)

**Checkpoint**: Athletes can attach up to 5 photos to their story; photos render in every template.

---

## Phase 7: User Story 4 — Public Story Page (Priority: P2)

**Goal**: A published story is viewable at `/story/<token>` without login. Layout reflows on mobile. Search engines are blocked via `noindex` + `robots.txt`. Per-IP rate limit prevents abuse.

**Independent Test**: Publish a story → fetch `GET /story/<token>` without a session cookie → assert 200 + correct HTML. Fetch with an invalid token → 404. Soft-deleted token → 404. Unpublished token without `?preview=1` → 404. Hammer the route 31 times in a minute → assert 429 on the 31st. `GET /robots.txt` returns the `Disallow: /story/` text. Rendered HTML contains `<meta name="robots" content="noindex,nofollow">`.

### Tests for User Story 4

- [X] T078 [P] [US4] Unit/web tests for the public route in `tests/unit/web/test_public_story_route.py`: valid published token returns 200 with the chosen template's HTML, invalid token 404, soft-deleted 404, unpublished without preview 404, unpublished with preview as owning athlete 200, malformed token (regex fail) 404 without DB hit
- [X] T079 [P] [US4] Unit/web test for the per-IP rate limit in `tests/unit/web/test_public_story_route.py`: 30 reqs in <60s = 200; 31st = 429; sliding window prunes timestamps older than 60s
- [X] T080 [P] [US4] Unit/web test for `GET /robots.txt` in `tests/unit/web/test_public_story_route.py`: returns 200 text/plain with the exact body
- [X] T081 [P] [US4] Unit/web test for the `noindex` meta tag in `tests/unit/web/test_public_story_route.py`: published response contains `<meta name="robots" content="noindex,nofollow">`

### Public route + outer Jinja wrapper

- [X] T082 [US4] Create `running_coach_ai/web/routes/__init__.py` (empty) and `running_coach_ai/web/routes/public_story.py` exposing `bp = Blueprint("public_story", __name__)` (no url_prefix). Implements `GET /story/<share_token>` and `GET /robots.txt` per `contracts/public-route.md`. Token regex pre-validation, rate-limit decorator, ownership/preview gating, render via `story_page.html`.
- [X] T083 [US4] Implement the in-memory sliding-window rate limiter in `running_coach_ai/web/routes/public_story.py` per research.md R-005: `dict[str, deque[float]]` under a `threading.Lock`, prune-then-check, 60s window, 30 req limit. Use `request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()` for IP.
- [X] T084 [US4] Create `running_coach_ai/web/templates/story_page.html` outer Jinja2 wrapper: `<head>` with Google Fonts links + `noindex` meta + canonical link + the chosen template's `template.css`, `<body>` includes the chosen template's `cover.html` then `inside.html` with the story context (`story`, `images`, `athlete`, `training_stats`).
- [X] T085 [US4] Register `public_story.bp` in `running_coach_ai/web/app.py:create_app()` and verify the route resolves at `/story/<token>` (not `/api/stories/...`).

**Checkpoint**: Public sharing works. Stories aren't indexed; abuse is bounded.

---

## Phase 8: User Story 7 — Admin Testing for Interviews and Rendering (Priority: P2)

**Goal**: Admin can fire interview sessions on demand for any athlete and re-render any story in any template. Critical for iterating on prompts and templates without waiting for real triggers.

**Independent Test**: As admin, `POST /api/admin/athletes/1/start-interview?trigger=pr_set` returns `{session_id, first_question}` and creates the row even if the athlete hasn't seen the intro modal. `POST /api/admin/stories/1/render?template=outside` updates `template_key` (without setting the lock flag) and returns a preview URL. Non-admin gets 403.

### Tests for User Story 7

- [X] T086 [P] [US7] Unit/web tests for `POST /api/admin/athletes/<id>/start-interview` in `tests/unit/web/test_admin_story_api.py`: admin opens a session with each valid trigger kind, bypasses intro-modal gate, rejects unknown trigger (400 with valid list), rejects non-admin (403), rejects athlete with active session (400)
- [X] T087 [P] [US7] Unit/web tests for `POST /api/admin/stories/<id>/render` in `tests/unit/web/test_admin_story_api.py`: admin updates template_key without setting lock flag, unknown key returns 400 with available list, non-admin 403
- [X] T088 [P] [US7] Unit/web test for `POST /api/admin/athletes/<id>/generate-story` in `tests/unit/web/test_admin_story_api.py`: admin triggers synchronous generation, returns `{story_id, preview_url}`, Claude timeout returns 504 + `story_failed` notification
- [X] T089 [P] [US7] Unit/web test for `DELETE /api/admin/stories/<id>?hard=1` in `tests/unit/web/test_admin_story_api.py`: removes row, image rows + files on disk, session rows; without `?hard=1` returns 400; non-admin 403
- [X] T090 [P] [US7] Unit/web test for `GET /api/admin/stories/templates` in `tests/unit/web/test_admin_story_api.py`: returns list of registered templates with display name + voice + affinities + thumbnail URL

### Admin endpoints in the existing admin blueprint

- [X] T091 [US7] Implement `POST /api/admin/athletes/<id>/start-interview` in `running_coach_ai/web/api/admin.py` per admin-api.md (calls `fire_trigger_if_eligible(...,bypass_intro=True)`, then `next_question` for the first question)
- [X] T092 [US7] Implement `POST /api/admin/athletes/<id>/generate-story` in `running_coach_ai/web/api/admin.py` per admin-api.md (synchronous generation with 60s timeout; on failure returns 504 + `story_failed` notification; reuses `coach.story.generate_story`)
- [X] T093 [US7] Implement `POST /api/admin/stories/<id>/render` in `running_coach_ai/web/api/admin.py` per admin-api.md (validates key against registry, updates `template_key`, leaves `template_locked_by_athlete=False`, returns `{ok, story_id, template_key, preview_url}`)
- [X] T094 [US7] Implement `DELETE /api/admin/stories/<id>?hard=1` in `running_coach_ai/web/api/admin.py` per admin-api.md (cascade hard-delete: image files + image rows + session rows + story row; without `?hard=1` returns 400)
- [X] T095 [US7] Implement `GET /api/admin/stories/templates` in `running_coach_ai/web/api/admin.py` per admin-api.md (returns registry contents)

### Admin panel UI (extends the existing admin overlay)

- [X] T096 [US7] Add a "Stories" tab to the admin panel in `running_coach_ai/web/static/magazine.html` + `magazine.js` (sibling to existing Athletes / Invites / Events tabs). UI shows: per-athlete "Start interview" button with trigger dropdown, per-story "Render in template" dropdown, per-story "Hard delete" button with confirmation dialog, a list of all registered templates with thumbnails.

**Checkpoint**: Full admin tooling for the feature; QA can exercise every code path on demand.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, baseline coverage, and the housekeeping items I flagged at end of Phase 1.

- [X] T097 Update `CLAUDE.md` with a new section "### Athlete Story (`coach/story.py`, `coach/story_templates.py`)" describing the trigger pipeline, generation flow, template registry, and admin tooling
- [X] T098 Update the routes table in `CLAUDE.md` with `web/api/stories.py`, `web/routes/public_story.py`, and the new admin endpoints (sequenced after T097 — same file)
- [X] T099 Replace the `vogue` thumbnail placeholder JPEG (T018) with final art at `running_coach_ai/web/static/story_templates/vogue/thumbnail.jpg` (320×400)
- [ ] T100 Run quickstart.md validation end-to-end: opt-in → admin start-interview → answer 6 questions → admin generate-story → swap template → publish → public URL → unpublish → delete. All commands in `quickstart.md` should produce the expected output.
- [X] T101 Run `pytest tests/ --cov=running_coach_ai --cov-report=term-missing` and confirm new modules (`coach/story.py`, `coach/story_templates.py`, `web/api/stories.py`, `web/routes/public_story.py`) reach >80% coverage
- [ ] T102 Resolve the `specs/006-athlete-story/` vs branch-name `006-magazine-features/` directory mismatch — either rename the spec dir to match the branch OR cut a `006-athlete-story` branch from the current head. Update `plan.md`'s Branch line accordingly.

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)** — Pillow + config — no dependencies, can start immediately
- **Phase 2 (Foundational)** — depends on Phase 1; **BLOCKS all user stories**
- **Phase 3 (US1 Interview)** — depends on Phase 2; **BLOCKS US3 (no Q&A → no editorial)**
- **Phase 4 (US3 Generation)** — depends on Phase 3 (and Phase 2)
- **Phase 5 (US6 Templates)** — depends on Phase 4 (the swap endpoint + UI need a generated story); the four template directories T062–T065 can be authored in parallel with Phase 4
- **Phase 6 (US2 Photos)** — depends on Phase 4 (need a story to attach to); template `inside.html` photo grids (T077) integrate with Phase 5's templates
- **Phase 7 (US4 Public page)** — depends on Phase 4; can run in parallel with Phase 5/6
- **Phase 8 (US7 Admin)** — depends on Phase 4 (admin endpoints need real generation/rendering); can run in parallel with 5/6/7 once 4 lands
- **Phase 9 (Polish)** — depends on all desired user stories being complete

### Within each phase

- Tests (T004–T005, T023–T027, T046–T049, T059–T061, T069–T070, T078–T081, T086–T090) MUST be written and FAIL before implementation per Constitution VII
- Models before services
- Services before endpoints
- Endpoints before UI

### Critical path to MVP

**MVP = Phase 1 + Phase 2 + Phase 3 (US1) + Phase 4 (US3)**. After Phase 4 the athlete can opt in, do an interview, get a generated story, and view it in `/magazine`. The `vogue` template is the only layout, sharing isn't public yet, photos can't be attached, admin tooling is missing — but the core narrative-generation cycle works end-to-end.

### Parallel opportunities

- **Within Phase 2**: T014–T018 (the 5 vogue template files) can run in parallel; T006–T010 (the 4 model classes) are sequential because they live in the same `models.py` file.
- **Within Phase 3**: T023–T027 (the 5 unit/integration test files) all `[P]`. T028–T029 (trigger detection helpers) `[P]`. T036, T042, T044 `[P]` (different files).
- **Within Phase 4**: T046–T049 tests `[P]`. T053–T056 endpoints `[P]` (different methods, same file — but each is a separate function so contention is low).
- **Within Phase 5**: T059–T061 tests `[P]`. T062–T065 (the four new template directories) all `[P]`.
- **Within Phase 6**: T069–T070 tests `[P]`.
- **Within Phase 7**: T078–T081 tests `[P]`.
- **Within Phase 8**: T086–T090 tests `[P]`.
- **Across phases**: once Phase 4 lands, Phases 5/6/7/8 can all be staffed in parallel by different developers.

---

## Parallel Example: User Story 1

```bash
# Launch all US1 unit tests together (each in a different file):
Task: "Trigger detection unit tests in tests/unit/test_story_triggers.py"  # T023
Task: "Session orchestration unit tests in tests/unit/test_story_session.py"  # T024–T026
Task: "End-to-end integration test in tests/integration/web/test_story_flow.py"  # T027

# Launch trigger detection helpers in parallel:
Task: "Implement detect_pr_set + detect_pace_recalibration in coach/story.py"  # T028
Task: "Implement detect_race_complete + detect_race_upcoming + detect_difficult_week in coach/story.py"  # T029
```

---

## Implementation Strategy

### MVP first (User Story 1 + User Story 3 only)

1. Complete Phase 1: Setup (3 tasks)
2. Complete Phase 2: Foundational (19 tasks) — **CRITICAL, blocks everything**
3. Complete Phase 3: User Story 1 (23 tasks) — interview flow live
4. Complete Phase 4: User Story 3 (13 tasks) — editorial generation live
5. **STOP and VALIDATE** — ship to a small group of athletes for tone calibration before adding the rest. The story is private (no public URL yet) and renders only in `vogue` (no swap), but the core experience works.
6. Iterate on the C-001 question prompt and C-002 editorial prompt based on real outputs before committing to all 5 templates.

### Incremental delivery beyond MVP

After MVP, ship in this order, with a deploy + dogfood between each:

- US6 (4 more templates + swap) — visual variety, athlete agency
- US2 (photos) — narrative richness
- US4 (public page) — sharing capability
- US7 (admin) — iteration speed

### Parallel team strategy

With 2+ developers after Phase 4 lands:

- Dev A: US6 (4 new templates + swap endpoint + carousel UI) — focused on template authoring + visual polish
- Dev B: US2 (photo upload + Pillow validation + per-template photo grids) — focused on backend + storage
- Dev C: US7 (admin endpoints + admin panel UI) — focused on QA tooling

US4 can land between or after — single-dev work on the public route + Jinja wrapper.

---

## Notes

- Tests are mandatory per Constitution Principle VII; do not skip them.
- Every new query against an athlete's story/session/question/image must use `scoped_query` per Constitution Principle I (the public route is the one intentional exception, scoped by `share_token`).
- All training-stat values in the editorial body MUST be converted to imperial via `coach.persona.format_miles` / `format_pace_mi` per Constitution Principle III.
- Claude failures (parse, timeout, API error) MUST never abort the activity ingest pipeline; wrap trigger detection + generation in try/except per Constitution Principle V.
- Each user story's checkpoint is a real stop-and-ship point. Don't pile multiple stories into a single PR.
- Avoid: vague tasks, cross-story dependencies that break independence, mixing test + implementation tasks in a single commit.
