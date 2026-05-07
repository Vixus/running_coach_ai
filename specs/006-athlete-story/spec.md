# Feature Specification: Athlete Story / Magazine Spread

**Feature Branch**: `006-athlete-story` (to be created from `006-magazine-features`)
**Created**: 2026-05-05
**Status**: Draft
**Input**: User description: "A personalized milestone magazine spread — when an athlete completes a training block or race, the system interviews them with periodic questions, collects optional photos, and generates a Claude-authored magazine-style editorial article with a shareable public URL."

## Clarifications

### Session 2026-05-06

- Q: Where are story images stored when deployed on Railway, given the host filesystem is ephemeral on redeploy? → A: A Railway Volume mounted at `STORY_IMAGE_DIR`, same pattern already used for `GARMIN_SESSION_DIR`. Local-dev path stays unchanged.
- Q: When an athlete (or admin) regenerates a story, does it overwrite the existing row or create a new one? → A: Overwrite `editorial_body` in place; `share_token`, `created_at`, and `published_at` are preserved so already-shared links keep working. Track `regeneration_count` and `last_regenerated_at`; enforce a 1 hour per-athlete cooldown to prevent Claude API spam.
- Q: How can an athlete revoke or delete a story they've published? → A: Three-tier model. (1) Unpublish — sets `published_at=NULL`; the public URL returns 404 but the draft + `share_token` are retained so the athlete can re-publish later. (2) Athlete soft-delete — sets `deleted_at`; the story disappears from "My Story", the public URL returns 404, but image files are retained. (3) Admin hard-delete — removes the `AthleteStory` row, all `StoryImage` rows + their files on disk, and any pending `StoryQuestion` rows.
- Q: How is the public `/story/<token>` route protected from search-engine indexing and basic abuse? → A: Render the page with `<meta name="robots" content="noindex,nofollow">`, serve `robots.txt: Disallow: /story/` from the Flask app, and apply a per-IP in-memory rate limit of 30 req/min on the public route. No athlete-level indexing toggle in v1; v2 may add an opt-in `indexable` flag.
- Q: When does the weekly question scheduler start issuing questions, and what does the question flow look like? → A: **Replaces the weekly-cadence model from the 2026-05-05 session.** The feature is **opt-in** — athletes are not enrolled automatically. The first time the trigger conditions are met for an opted-in athlete, the magazine shows a multi-page introductory modal (with infographic animations) explaining what the story feature is, what to expect, and how their answers will be used; only after the athlete confirms does the first interview session begin. Questions are **event-triggered, not weekly** — sessions fire on key moments worth narrating: a PR (new best for a distance), a goal race, a notable improvement (e.g., consistent pace recalibration upward), and a difficult run or week (e.g., HRV slump, missed sessions, abnormal hard run). Each session asks **5–10 questions adaptively**, like a veteran magazine interviewer building a positive narrative — questions are Claude-generated per session using the trigger event + athlete profile + recent history, NOT a hardcoded list. The first question of each session is framed as a consent prompt ("I'd like to capture something for your story — can I ask a few questions about \[event\]?") so the athlete always knows the purpose. Each question presents 2–4 multiple-choice options plus a free-form text fallback for the athlete to write their own answer.
- Q: Should the published story support multiple magazine-style visual templates (e.g., Vogue-style, Runner's World-style, Times Magazine), and how are templates picked? → A: Yes, ship five v1 templates as a file-system registry under `web/static/story_templates/<key>/`. Each template is **both a visual identity and a writing voice** — the chosen template's voice instructions are injected into the Claude generation prompt so the prose matches the layout. v1 templates: `vogue` (editorial profile), `runners_world` (the build-up), `times_long_read` (the long read), `outside` (field notes), `gq_profile` (the profile). Auto-selection at generation time combines a trigger-affinity prior (declared in each template's `template.json`) with a Claude vote based on the collected answers. Athletes can swap to any other registered template post-generation via a "Try a different look" carousel in the "My Story" section; a swap re-renders only the layout (no new Claude editorial call) and sets `template_locked_by_athlete=True` so future regenerations preserve the chosen look.
- Q: How are uploaded photos treated visually inside the magazine spread? → A: v1 is **CSS-only** — no AI image editing, no external image-generation provider. Athletes upload photos as-is; each template applies its own treatments (vignettes, duotones, crop framing, blur-overlay backgrounds) in `template.css`. AI-driven person-preserving image editing is deferred to v1.1 to avoid vendor lock-in before validating the overall flow.
- Q: What admin testing capability is required to iterate on the feature without waiting for real triggers? → A: Two new admin endpoints, both gated by `is_admin=True`. (1) `POST /api/admin/athletes/<id>/start-interview?trigger=<kind>` — manually opens a `StoryInterviewSession` for the athlete with the specified trigger kind, bypassing the real detection logic so QA can exercise the full Q&A flow on demand. (2) `POST /api/admin/stories/<story_id>/render?template=<key>` — re-renders an existing story in any registered template (overrides the auto-selected `template_key`) and returns a preview URL; used to inspect how a single editorial reads across all templates.

### Session 2026-05-05

- Q: What triggers a story — only race completions, or also training blocks? → A: Both. `race_complete` fires when the athlete logs an activity on race day (detected from `Goal.race_date`). `block_complete` fires at the end of a training cycle defined by the coach. Admin can also manually trigger for testing without waiting for a real milestone. _(Updated 2026-05-07 — `block_complete` deferred from v1; only `race_complete` ships in v1. See FR-S006 and the trigger list in FR-S028.)_
- Q: How are the interview questions delivered to the athlete? → A: Via the existing chat FAB in `/magazine`. _(Superseded 2026-05-06 — see "When does the weekly question scheduler start issuing questions" in the 2026-05-06 session: weekly cadence is replaced by event-triggered adaptive sessions.)_
- Q: Can athletes upload photos? → A: Yes — a dedicated "My Story" section in `/magazine` shows the current in-progress story with an upload widget. Images are stored as files on disk (same pattern as `GARMIN_SESSION_DIR`). Maximum 5 images per story, each under 10 MB.
- Q: Who can see the story? → A: The public `GET /story/<share_token>` page requires no login. The share token is a URL-safe random string (12 chars). Unpublished stories are only visible to the athlete in their `/magazine` "My Story" section.
- Q: Is the editorial article editable by the athlete? → A: Not in v1. Athletes can regenerate (which calls Claude again) or leave feedback but cannot directly edit the generated text. Admins can trigger regeneration via the admin panel.
- Q: What does the admin test mode look like? → A: `POST /api/admin/athletes/<id>/generate-story?milestone=race_complete` — generates a draft story without requiring an actual race event. Returns `{story_id, preview_url}`. Preview URL is `/story/<share_token>?preview=1` which shows the story even before publication (authenticated only).
- Q: How long does generation take? → A: Claude call with full Q&A context — expect 10–20 seconds. Endpoint is async (returns `{job_id}` immediately); athlete can poll `/api/stories/status/<job_id>` or wait for a notification in the bell feed.
- Q: What format is the editorial output? → A: Plain text (400–600 words) formatted in paragraphs. The API returns raw text; the story page renders it with the magazine's DM Serif Display / Bebas Neue typography, large drop-cap on the first letter, pull quote highlighted mid-article.
- Q: What happens to old stories when a new milestone fires? → A: Each milestone creates a new `AthleteStory` row. Athletes accumulate a library of stories over time. `/magazine` "My Story" section shows the most recent; older stories are accessible from a "Past stories" list.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Opt-in, Event-Triggered Interview Sessions (Priority: P1)

The story feature is **opt-in**. An athlete enables it from a Settings toggle in `/magazine`. From then on, when a key narrative moment is detected — a PR, an upcoming/just-completed race, a sustained improvement, or a notably difficult run/week — the system fires an **interview session**: the chat opens with a brief consent prompt naming the purpose ("I'd like to capture something for your story — can I ask a few questions about \[trigger\]?"), and on confirmation Claude conducts a 5–10 question adaptive interview, picking each follow-up question based on the previous answer. Every question is rendered with 2–4 multiple-choice options plus a free-text fallback. **Before the very first session ever fires for an athlete**, the magazine shows a multi-page introductory modal with infographic animations explaining the feature; the first session only begins after the athlete dismisses or confirms the modal.

**Why this priority**: The interview is the core data-collection mechanism. Without good Q&A material the generated editorial degenerates into generic platitudes. Adaptive, event-triggered sessions produce richer, more personalized narratives than fixed weekly questions.

**Independent Test**: With `Athlete.story_opt_in=False`, simulate a trigger event and verify NO `StoryInterviewSession` row is created. Set `story_opt_in=True`, simulate the same event, verify a `StoryInterviewSession` row is created and the first question (with options) is delivered as a chat message. Submit answers and verify each follow-up question is generated based on the previous answer until the session completes (between 5 and 10 questions).

**Acceptance Scenarios**:

1. **Given** an athlete with `story_opt_in=False`, **When** any trigger event fires, **Then** no interview session is started and no chat message is sent.
2. **Given** an athlete who just toggled `story_opt_in=True` and has not yet seen the intro modal, **When** they next open `/magazine`, **Then** the multi-page intro modal renders before any interview session can fire.
3. **Given** an opted-in athlete and the first trigger event after the intro modal has been seen, **When** the trigger fires, **Then** a `StoryInterviewSession` row is created and a coach chat message is sent containing the consent prompt + first question + 2–4 multiple-choice options + a "Type your own answer" affordance.
4. **Given** the athlete answers a question (option click or free text), **When** the answer is submitted, **Then** `StoryQuestion.answer`, `answered_at`, and `is_custom_answer` are populated; if the session has fewer than 10 questions and the narrative arc is incomplete, Claude generates the next adaptive question; if the session has reached its target depth (≥5 questions and a satisfying arc), the session is closed (`StoryInterviewSession.completed_at` set).
5. **Given** an interview session is in progress, **When** another trigger event would normally fire, **Then** no new session is opened until the current one is closed (one active session per athlete at a time).
6. **Given** a `race_complete` milestone fires, **When** generation is triggered, **Then** all Q&A pairs from every closed `StoryInterviewSession` for this athlete since the last published story are included in the Claude prompt.

---

### User Story 2 — Photo Upload in "My Story" Section (Priority: P2)

The athlete sees a "My Story" card in `/magazine`. Before publication they can upload up to 5 photos. Each upload shows a thumbnail with an optional caption field. They can delete individual photos.

**Why this priority**: Photos make the story visually compelling. The generation flow can proceed without them but the shareable page is significantly richer with images.

**Independent Test**: POST a valid JPEG under 10 MB to `/api/stories/current/images` and verify a `StoryImage` row is written and the file exists on disk.

**Acceptance Scenarios**:

1. **Given** the "My Story" section, **When** the athlete taps the upload area, **Then** the device file picker opens (accept: image/jpeg,image/png,image/webp).
2. **Given** a valid image file under 10 MB, **When** uploaded, **Then** a thumbnail appears immediately and a `StoryImage` row is created.
3. **Given** 5 images already uploaded, **When** the athlete tries to upload a 6th, **Then** the upload is rejected with a message "Maximum 5 photos per story".
4. **Given** a photo with a caption field, **When** the athlete types a caption and moves focus away, **Then** `StoryImage.caption` is persisted via a PATCH request.
5. **Given** a photo thumbnail, **When** the athlete taps the delete button, **Then** the file is removed from disk and the `StoryImage` row is deleted.

---

### User Story 3 — Editorial Generation and Preview (Priority: P1)

A milestone fires (race completed or block ended). The system collects all answered `StoryQuestion` rows + training stats + weekly history and calls Claude to generate a 400–600 word magazine-style article. The athlete receives a notification ("Your race story is ready") and can preview it before publishing.

**Why this priority**: Generation is the centrepiece of the feature — everything else (interview, photos, sharing) is in service of this moment.

**Independent Test**: Stub Claude to return a known string; call `generate_story(athlete_id, milestone="race_complete")` and verify an `AthleteStory` row is written with the stubbed text and a non-null `share_token`.

**Acceptance Scenarios**:

1. **Given** a race-day activity is logged, **When** the activity poll detects the race date matches `Goal.race_date`, **Then** `generate_story` is enqueued and a `StoryQuestion` row linkage is applied to the current story.
2. **Given** `generate_story` runs, **When** Claude responds within 30 seconds, **Then** `AthleteStory.editorial_body` is set, `share_token` is assigned, `created_at` is set, and a bell notification "Your race story is ready" is written.
3. **Given** generation fails (Claude timeout or error), **When** the job exits, **Then** a bell notification "Story generation failed — try again from your magazine" is written, and the admin panel shows the failure.
4. **Given** a draft story, **When** the athlete opens the "My Story" section in `/magazine`, **Then** they see: a cover image (first uploaded photo, or a gradient placeholder), the article text, and a "Share" button.
5. **Given** the "Share" button is clicked, **When** the story is not yet published, **Then** `published_at` is set to now and the public URL `/story/<share_token>` is shown in a copy-to-clipboard dialog.

---

### User Story 4 — Public Story Page (Priority: P2)

Anyone with the link can view the story page at `/story/<share_token>`. No login required. The page uses the magazine's editorial typography (DM Serif Display drop cap, Bebas Neue headline) with photo grid and training stats inset.

**Why this priority**: Shareability is the social hook that justifies the interview effort. Without it the story is just a private journal entry.

**Independent Test**: Fetch `GET /story/<valid_token>` without a session cookie and assert 200 with the correct HTML. Fetch with an invalid token and assert 404.

**Acceptance Scenarios**:

1. **Given** a published story, **When** a non-logged-in visitor visits `/story/<token>`, **Then** they see: headline, athlete first name, editorial text with drop cap, training stats sidebar (miles, race time, weeks of training), and photo grid if photos exist.
2. **Given** an unpublished story (no `published_at`), **When** visited by anyone other than the story's athlete, **Then** the page returns 404.
3. **Given** a preview URL `/story/<token>?preview=1`, **When** visited by the owning athlete while logged in, **Then** the unpublished story is visible with a "This story is not yet public" banner.
4. **Given** the story page, **When** rendered on mobile (≤ 480px), **Then** the layout reflows to a single column (the story page IS mobile-friendly, unlike `/magazine`).
5. **Given** the story page, **When** the athlete shares the URL and it is opened by a friend who is also a platform athlete, **Then** the page still renders without requiring the friend to log in.

---

### User Story 5 — Admin Test Mode (Priority: P3)

An admin can trigger story generation for any athlete from the admin panel without waiting for a real milestone. This is used to demo the feature and QA the generated output.

**Why this priority**: Without this, every change to the generation prompt requires waiting for a real race to test. Test mode is essential for iterating on the editorial quality.

**Independent Test**: Call `POST /api/admin/athletes/1/generate-story?milestone=race_complete` and verify a `story_id` and `preview_url` are returned and the `AthleteStory` row exists.

**Acceptance Scenarios**:

1. **Given** an admin user, **When** they POST to `/api/admin/athletes/<id>/generate-story?milestone=race_complete`, **Then** a draft story is generated and `{story_id, preview_url}` is returned.
2. **Given** the generated draft, **When** the admin visits `preview_url` while logged in as admin, **Then** the full story renders even though `published_at` is null.
3. **Given** a non-admin user, **When** they attempt the same endpoint, **Then** a 403 response is returned.

---

### User Story 6 — Magazine Template Selection and Swap (Priority: P2)

When the editorial is generated, the system auto-selects one of five magazine templates based on the trigger event and the collected Q&A. The athlete sees the chosen template by default in their "My Story" preview. They can browse the other four templates via a "Try a different look" carousel and lock in any one — swapping is layout-only (no new Claude call), so previewing alternatives is instant and free. The chosen template determines both the visual layout (HTML/CSS) and the writing voice used by Claude for any future regeneration of that story.

**Why this priority**: Different runners experience the same race in very different ways — a first-time marathoner's story reads naturally as a `runners_world` build-up, while a defending champion's reads as a `gq_profile`. Letting the athlete swap means they aren't stuck with an aesthetic that doesn't match their identity, and matching voice to layout makes the result feel professionally produced rather than generated.

**Independent Test**: Generate a story, verify `AthleteStory.template_key` is non-null and points at a template directory that exists in `web/static/story_templates/`. POST to the swap endpoint with a different valid template key and verify `template_key` updates, `template_locked_by_athlete` is True, and the public URL renders the new layout without invoking Claude.

**Acceptance Scenarios**:

1. **Given** a story is generated, **When** Claude finishes the editorial, **Then** `AthleteStory.template_key` is set to one of the five registered templates based on the trigger-affinity prior + Claude's vote, and `template_locked_by_athlete=False`.
2. **Given** a published story rendered with template X, **When** the owning athlete opens "My Story" in `/magazine`, **Then** they see a "Try a different look" carousel with thumbnails of all other registered templates.
3. **Given** the athlete clicks a different template thumbnail, **When** the swap is confirmed, **Then** `AthleteStory.template_key` updates, `template_locked_by_athlete=True`, the public URL re-renders with the new layout, and Claude is NOT re-invoked.
4. **Given** a future regeneration of the editorial body, **When** `template_locked_by_athlete=True`, **Then** Claude's generation call uses the locked template's voice instructions (the auto-selection re-vote is skipped).
5. **Given** a sixth template is added by dropping a directory into `web/static/story_templates/`, **When** the athlete opens the carousel, **Then** the new template appears alongside the existing five with no code or DB changes required.

---

### User Story 7 — Admin Testing for Interviews and Rendering (Priority: P2)

An admin user can exercise the interview flow and the template rendering without waiting for real trigger conditions. This unblocks iterative tuning of the question prompts and template designs — a single feature change can be QA'd end-to-end in under a minute.

**Why this priority**: Without this, every change to the question-generation prompt or to a template requires waiting for a real PR / race / hard week to fire on a real athlete account. Test mode is essential for getting the prose and layout right.

**Independent Test**: As admin, POST to `/api/admin/athletes/1/start-interview?trigger=pr_set`; verify a new `StoryInterviewSession` row is created with `trigger_kind=pr_set` and the first question is delivered as a chat message. Then POST to `/api/admin/stories/<id>/render?template=outside` for an existing story; verify the public URL reflects the `outside` template even though the story was originally auto-selected for a different template.

**Acceptance Scenarios**:

1. **Given** an admin user, **When** they POST `/api/admin/athletes/<id>/start-interview?trigger=race_complete`, **Then** a `StoryInterviewSession` row is created with the specified trigger kind, the consent prompt is delivered to chat, and the response includes the new `session_id`.
2. **Given** an admin user, **When** they POST `/api/admin/stories/<id>/render?template=<key>` with a valid template key, **Then** `AthleteStory.template_key` updates to the requested key, `template_locked_by_athlete=False` (admin override is not the athlete's choice), and the response includes a preview URL.
3. **Given** an admin user, **When** they request a template key that does not exist in the registry, **Then** the response is HTTP 400 with `{"error": "Unknown template key: <key>", "available": [...]}`.
4. **Given** a non-admin user, **When** they hit either endpoint, **Then** HTTP 403 is returned.

---

### Edge Cases

- What if the athlete has answered no questions when a milestone fires? → Generate anyway using training stats alone; the editorial will be more generic but still complete.
- What if `Goal.race_date` is null? → `race_complete` milestone is never triggered for this goal. Admin can still fire `POST /api/admin/athletes/<id>/generate-story?milestone=race_complete` manually.
- What if the athlete completes multiple races in a season? → Each creates a separate `AthleteStory` with its own interview thread. The "My Story" section shows the most recent active story.
- What if an image file is corrupted or cannot be thumbnailed? → Accept the upload (store the raw file) but skip thumbnail generation; show a fallback icon on the story page.
- What if the Claude-generated text contains the athlete's full name or sensitive health metrics the athlete wouldn't want public? → Out of scope for v1 — athlete previews before publishing. A future moderation step can be added.
- What if `share_token` collides? → Tokens are 12-character URL-safe random strings (72 bits entropy). Collision probability is negligible for any realistic user count. Uniqueness is enforced by a DB UNIQUE constraint; retry with a new token on the rare collision.
- What if generation is triggered while a previous generation job is already running? → Deduplicate by checking for a running job for the same `(athlete_id, milestone_type)` before enqueuing.
- What if the athlete starts a session but stops answering partway through? → After 7 days with no answer to the most-recent question, the system closes the session (`completed_at` set, no `skipped` flag) and the partial Q&A is still eligible for the next generation. A new trigger event can open a new session.
- What if multiple trigger events fire within minutes of each other (e.g., a PR set on race day fires both `pr_set` and `race_complete`)? → Trigger priority order: `race_complete` > `race_upcoming` > `pr_set` > `pace_recalibration` > `difficult_week`. Only the highest-priority unfired trigger in the last 24 hours opens a session; lower-priority triggers within the same window are suppressed.
- What if Claude returns malformed question JSON (no options, options not a list, options > 4)? → Retry once. If still malformed, abort the session with `completed_at` set + `skipped=False` (it is not the athlete's choice — log a warning) and write a `story_failed` notification suggesting the athlete try answering "/story start" later.
- What if the athlete has `story_opt_in=True` but `story_intro_seen_at IS NULL` and a trigger fires? → Defer: the trigger is dropped silently (we don't queue it). The next eligible trigger after the modal is acknowledged opens the session. The athlete is not punished for the deferred trigger but it is not held forever either.
- What if Claude's template-vote returns a key that isn't in the registry? → Fall back to the highest-affinity template for the trigger kind. If no template lists the trigger, fall back to `vogue`.
- What if a template directory is removed after stories were generated against it? → On render the system falls back to `vogue` and logs a warning. Existing `AthleteStory.template_key` values are NOT auto-migrated; admin can use `POST /api/admin/stories/<id>/render?template=<new>` to fix them in batch if desired.
- What if an athlete locks a template (`template_locked_by_athlete=True`) and that template is later removed from the registry? → Same as above — fall back to `vogue` at render time and log a warning. The lock flag stays True so the next regeneration uses the lock fallback voice (`vogue`).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-S001**: The story feature MUST be opt-in. `Athlete.story_opt_in` defaults to `False`. The athlete enables it from a Settings toggle in `/magazine`. The system MUST NOT create any `StoryInterviewSession`, `StoryQuestion`, or `AthleteStory` rows for athletes with `story_opt_in=False`.
- **FR-S002**: When the athlete answers a question — by clicking a multiple-choice option or by submitting free text via the chat — the system MUST populate `StoryQuestion.answer`, `answered_at`, and `is_custom_answer` (True if free text, False if a preset option) on the row.
- **FR-S003**: At most one `StoryInterviewSession` per athlete may be active (`completed_at IS NULL`) at any time. While a session is active, no further trigger events open a new session; questions within the active session are issued one at a time, with each follow-up generated only after the previous answer is recorded.
- **FR-S004**: System MUST detect a `race_complete` milestone when a `CompletedWorkout` date matches `Goal.race_date` (±1 day tolerance for time-zone edge cases).
- **FR-S006**: System MUST generate an `AthleteStory` editorial via Claude when a `race_complete` milestone is detected, using: athlete profile, all answered `StoryQuestion` Q&A pairs from completed sessions linked to the milestone, training stats (mileage in km internally, surfaced as miles in the editorial; race time; weeks trained), and weekly HRV/sleep history.
- **FR-S007**: Generated editorial MUST be 400–600 words, structured in 3–5 paragraphs, written in the voice declared by the chosen template's `voice_description` field in `template.json`. The voice varies by template (e.g., vogue: present-tense scene-setting; runners_world: practical reportage in second person; times_long_read: third-person observational; outside: first-person adventure prose; gq_profile: polished journalistic). Word count and paragraph structure remain fixed across all templates.
- **FR-S008**: System MUST assign a unique 12-character URL-safe `share_token` to every `AthleteStory` row at creation time.
- **FR-S009**: System MUST write a bell `Notification` when story generation completes (success or failure).
- **FR-S010**: System MUST expose `GET /story/<share_token>` as a public route (no authentication required) that renders the story page in magazine typography.
- **FR-S011**: Unpublished stories (`published_at IS NULL`) MUST return 404 from the public route except when accessed by the owning athlete with `?preview=1`. Soft-deleted stories (`deleted_at IS NOT NULL`) MUST return 404 from the public route unconditionally and MUST NOT be visible to the owning athlete in `/magazine` or `/api/stories`.
- **FR-S012**: System MUST provide a "My Story" section in `/magazine` showing the athlete's most recent in-progress or published story, including: cover image or placeholder, article preview (first 120 characters), upload widget, and Share/Republish button.
- **FR-S013**: Athletes MUST be able to upload up to 5 images per story (JPEG, PNG, WebP; max 10 MB each). Images are stored on disk under `STORY_IMAGE_DIR/<story_id>/`.
- **FR-S014**: Athletes MUST be able to add or edit a caption on each uploaded image. Captions are stored in `StoryImage.caption`.
- **FR-S015**: Athletes MUST be able to delete individual uploaded images before publication.
- **FR-S016**: System MUST expose `POST /api/admin/athletes/<id>/generate-story?milestone=<type>` (admin-only) to trigger story generation without a real milestone event.
- **FR-S017**: Admin endpoint MUST return `{story_id, preview_url}` synchronously after the Claude generation call completes (target ≤30 s per SC-S001; 60 s hard timeout returns HTTP 504 plus a `story_failed` notification). The preview URL is accessible to admins and the owning athlete before publication.
- **FR-S018**: System MUST enforce data isolation — all story/question/image queries are scoped to `athlete_id`; a logged-in athlete cannot access another athlete's draft story or images.
- **FR-S019**: Story image uploads MUST be rejected if the payload exceeds 10 MB or the MIME type is not image/jpeg, image/png, or image/webp.
- **FR-S020**: System MUST provide an API to list an athlete's past published stories (`GET /api/stories`) returning `[{story_id, title, milestone_type, created_at, published_at, preview_url, share_url}]`.
- **FR-S021**: Regeneration MUST overwrite `AthleteStory.editorial_body` in place; `share_token`, `created_at`, and `published_at` are preserved so previously shared URLs remain valid. The system MUST increment `regeneration_count` and set `last_regenerated_at` on each regeneration. Regenerations triggered by an athlete are rate-limited to one per hour per story; admin-triggered regenerations bypass the cooldown.
- **FR-S022**: System MUST expose `POST /api/stories/<story_id>/unpublish` (athlete-only, owner-scoped) which sets `published_at = NULL`; the draft and `share_token` are preserved so the athlete can re-publish later via the existing Share button.
- **FR-S023**: System MUST expose `DELETE /api/stories/<story_id>` (athlete-only, owner-scoped) which performs a soft delete by setting `deleted_at = now()`. Soft-deleted stories are excluded from `/api/stories`, the "My Story" section, and the public route.
- **FR-S024**: System MUST expose `DELETE /api/admin/stories/<story_id>?hard=1` (admin-only) which performs a hard delete: removes the `AthleteStory` row, deletes every `StoryImage` row and its file under `STORY_IMAGE_DIR/<story_id>/`, and removes any pending unanswered `StoryQuestion` rows linked to the story.
- **FR-S025**: The public `GET /story/<share_token>` page MUST render with `<meta name="robots" content="noindex,nofollow">` to suppress search-engine indexing. The Flask app MUST serve `GET /robots.txt` returning `User-agent: *\nDisallow: /story/\n`.
- **FR-S026**: The public `GET /story/<share_token>` route MUST enforce a per-IP rate limit of 30 requests/minute (in-memory sliding window keyed by `request.remote_addr`). Requests over the limit return HTTP 429.
- **FR-S027**: The first time an opted-in athlete opens `/magazine` after setting `story_opt_in=True`, the magazine MUST display a multi-page introductory modal (3–5 pages, with infographic animation on each page) explaining: what the story feature is, that questions will appear in chat at key training moments, what data is used (training stats + answers), and how to publish/unpublish/delete a story. The athlete MUST acknowledge the final page before any interview session is allowed to fire. The system records `Athlete.story_intro_seen_at` on acknowledgement; sessions are only triggered when this timestamp is non-null.
- **FR-S028**: The system MUST detect and fire interview sessions on the following trigger events for opted-in athletes whose `story_intro_seen_at IS NOT NULL`:
  - **`pr_set`** — a `CompletedWorkout` whose pace beats every prior workout of comparable distance (e.g., 5K best, 10K best, longest-run best) by at least 1%.
  - **`race_upcoming`** — 7 days before `Goal.race_date`.
  - **`race_complete`** — a `CompletedWorkout` on `Goal.race_date` (±1 day for time-zone tolerance).
  - **`pace_recalibration`** — `RunningProfile` shows aerobic decoupling improving and an upward pace recalibration applied via `<plan>` in conversation.
  - **`difficult_week`** — HRV trend declining for ≥3 consecutive days, OR ≥2 missed planned sessions in a 7-day window, OR a single run with HR drift > 15%.
- **FR-S029**: The first message of every interview session MUST be framed as an explicit consent prompt naming the trigger and the purpose. Wording template: "I'd like to capture something for your story — can I ask a few questions about \[trigger label\]?" If the athlete declines (free-text "no" / "not now" / option "Skip"), the session is closed with `completed_at` set and `skipped=True`; no questions follow.
- **FR-S030**: Within an interview session, the system MUST conduct an **adaptive 5–10 question interview** generated by Claude. Each question is produced from a Claude call given: the trigger event, the athlete profile, the recent training context, and the running transcript of the current session's prior questions and answers. Each question MUST present 2–4 multiple-choice options + a free-text fallback. Sessions terminate when Claude indicates a satisfying narrative arc has been collected (>=5 questions answered) OR when 10 questions have been answered, whichever is sooner.
- **FR-S031**: The system MUST persist each question's options as `StoryQuestion.options_json` (list of strings) at issue time. When the athlete clicks an option, the chosen option string is stored in `answer` and `is_custom_answer=False`. When the athlete types free text, the typed text is stored in `answer` and `is_custom_answer=True`.
- **FR-S032**: The system MUST maintain a **magazine-template registry** read at startup from `web/static/story_templates/<key>/`. Each template directory MUST contain `template.json` (display name, voice description, recommended trigger affinities), `cover.html` (Jinja2 template for the cover page), `inside.html` (Jinja2 for the inside spread), and `template.css` (template-specific styles). The system MUST ship with five v1 templates: `vogue`, `runners_world`, `times_long_read`, `outside`, `gq_profile`. Adding a new template MUST require only adding a directory; no code or database change.
- **FR-S033**: At story-generation time the system MUST auto-select a template by combining (a) a trigger-affinity prior from each template's `template.json` and (b) a single-token Claude vote ("which of these template keys best fits the story you just wrote: <list>?") issued in the same Claude call as the editorial body. The selected key is persisted to `AthleteStory.template_key` and `template_locked_by_athlete=False`.
- **FR-S034**: The system MUST expose `POST /api/stories/<story_id>/template` (athlete-only, owner-scoped) accepting `{"template_key": "<key>"}`. On success: validates the key against the registry, updates `template_key`, sets `template_locked_by_athlete=True`, and returns `{"ok": true, "preview_url": "..."}`. The endpoint MUST NOT invoke Claude — layout-only swap.
- **FR-S035**: When `template_locked_by_athlete=True`, future calls to regenerate the editorial body MUST inject the locked template's voice instructions into the Claude system prompt instead of re-voting. When `False`, the voice instructions of the freshly auto-selected template are used.
- **FR-S036**: The public route `GET /story/<share_token>` MUST render the story using the template identified by `AthleteStory.template_key`. If the key is missing from the registry at render time (e.g., a template was removed), the system MUST fall back to the `vogue` template and log a warning.
- **FR-S037**: System MUST expose `POST /api/admin/athletes/<id>/start-interview?trigger=<kind>` (admin-only). It opens a `StoryInterviewSession` for the athlete with `trigger_kind=<kind>` regardless of real-world detection state, delivers the consent prompt + first question via the chat, and returns `{"session_id": <id>}`. Bypasses the `story_intro_seen_at` requirement when called by admin (so QA can test sessions for athletes who haven't yet seen the modal).
- **FR-S038**: System MUST expose `POST /api/admin/stories/<story_id>/render?template=<key>` (admin-only). It validates the requested template key against the registry, sets `AthleteStory.template_key` to the requested key, leaves `template_locked_by_athlete=False`, and returns `{"preview_url": "..."}`. Returns HTTP 400 with `{"error": "Unknown template key: <key>", "available": [...]}` if the key is not registered.

### Key Entities

- **`AthleteStory`**: `id`, `athlete_id`, `milestone_type` (`race_complete` only in v1; future iterations may add other values), `title` (athlete name + milestone label), `editorial_body` (Claude-generated text, overwritten on regeneration), `cover_image_path` (nullable — first `StoryImage`), `created_at`, `published_at` (nullable), `share_token` (unique, 12-char URL-safe random; preserved across regenerations), `regeneration_count` (int, default 0), `last_regenerated_at` (nullable timestamp), `deleted_at` (nullable timestamp — soft-delete marker; rows with non-null `deleted_at` are excluded from all athlete-facing queries and return 404 from the public route), `template_key` (string, FK by convention to a directory under `web/static/story_templates/`; defaults to `vogue` if registry is unreachable), `template_locked_by_athlete` (bool, default False — True after the athlete swaps templates manually). One Alembic migration.
- **`StoryInterviewSession`**: `id`, `athlete_id`, `trigger_kind` (`pr_set` | `race_upcoming` | `race_complete` | `pace_recalibration` | `difficult_week`), `trigger_context_json` (free-form JSON describing the trigger — e.g., the workout id for a PR, the goal id for a race), `started_at`, `completed_at` (nullable), `skipped` (bool, default False — True if athlete declined the consent prompt), `story_id` (nullable until linked at generation time). Same migration.
- **`StoryQuestion`**: `id`, `session_id` (FK → `StoryInterviewSession`), `athlete_id` (denormalised for scoped queries), `story_id` (nullable until linked at generation time), `question_index` (int, ordering within the session, starting at 1), `question` (text — Claude-generated), `options_json` (JSON list of strings, 2–4 multiple-choice options), `answer` (nullable text), `is_custom_answer` (nullable bool — True if free text, False if a preset option, NULL if unanswered), `asked_at`, `answered_at` (nullable). Same migration.
- **`StoryImage`**: `id`, `story_id`, `filename` (stored name on disk), `caption` (nullable text), `sort_order` (int, default 0), `uploaded_at`. Same migration.
- **`Athlete`** (existing model — adds two columns): `story_opt_in` (bool, default False), `story_intro_seen_at` (nullable timestamp — set when the athlete acknowledges the multi-page intro modal). Same migration.
- **Notification**: Reuses existing model — `kind="story_ready"` or `kind="story_failed"`.
- **`MagazineTemplate`** (file-system registry, not a DB table): each template lives at `web/static/story_templates/<key>/` and contains:
  - `template.json` — `{"key": "<key>", "display_name": "<human label>", "voice_description": "<Claude voice instructions>", "trigger_affinities": ["<trigger_kind>", ...], "thumbnail": "thumbnail.jpg"}`
  - `cover.html` — Jinja2 fragment rendering the story cover (page 1)
  - `inside.html` — Jinja2 fragment rendering the inside spread (pages 2–3)
  - `template.css` — template-specific styles (no global selectors; namespaced under `.tpl-<key>`)
  - `thumbnail.jpg` — 320×400 JPEG used in the swap carousel
  - The registry is loaded at Flask app startup; missing or malformed templates are logged and skipped, but the system continues to serve registered templates.

### v1 Template Catalogue

| Key | Display name | Voice (passed to Claude) | Trigger affinities |
|---|---|---|---|
| `vogue` | Editorial Profile | Glossy, aspirational, present-tense scene-setting; opens with a vignette; ends with a quote. | `race_complete`, `pr_set` |
| `runners_world` | The Build-Up | Practical, reportage-style, second person ("you found your stride…"); training-stat sidebars. | `race_complete`, `pace_recalibration` |
| `times_long_read` | The Long Read | Third-person observational; reads like a Sunday magazine profile. Quietly confident, broken into chapters by subhead. | `pace_recalibration`, `difficult_week` |
| `outside` | The Field Notes | Adventure prose, sensory, weather-aware; first person. | `race_complete`, `difficult_week` |
| `gq_profile` | The Profile | Polished, journalistic, achievement-focused; pull quotes mid-spread; crisp present tense. | `pr_set`, `race_complete` |

### New Environment Variable

- **`STORY_IMAGE_DIR`**: Directory for athlete story images. Default: `/data/story_images/`. Each story's images live in `<STORY_IMAGE_DIR>/<story_id>/`. On Railway this path MUST be backed by a Railway Volume (same configuration approach as `GARMIN_SESSION_DIR`); on local development any directory writable by the process is sufficient.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-S001**: Story generation completes within 30 seconds of being triggered (Claude call + DB write).
- **SC-S002**: Public story page (`GET /story/<token>`) loads in under 1.5 seconds on a local network connection with no authentication.
- **SC-S003**: Image upload round-trip (POST + DB write + disk write) completes within 3 seconds for a 5 MB image.
- **SC-S004**: The "My Story" section in `/magazine` renders within the existing hydrate call — no additional page-load latency.
- **SC-S005**: Admin generate-story endpoint returns `{story_id, preview_url}` within the same 30 s target as automatic generation (per SC-S001). Hard timeout at 60 s returns HTTP 504 with a `story_failed` notification written to the athlete's inbox.
- **SC-S006**: Trigger detection is idempotent — running activity-poll or morning-check-in twice on the same data produces exactly one `StoryInterviewSession` per qualifying trigger event.
- **SC-S007**: Athlete template swap (`POST /api/stories/<id>/template`) completes in under 500 ms — pure DB update, no Claude call.
- **SC-S008**: Admin "start interview" endpoint (`POST /api/admin/athletes/<id>/start-interview`) returns the new `session_id` within 5 seconds, including the first Claude question generation.
- **SC-S009**: Adding a sixth template by dropping a new directory under `web/static/story_templates/` and restarting the Flask app makes it appear in the swap carousel for every existing story without any database migration.

## Assumptions

- Story generation is triggered server-side (scheduler job or activity poll callback), not by the athlete explicitly pressing a button.
- The public story page is a server-rendered Jinja2 template, not a React/JS SPA — simpler, faster, and more indexable by search engines (note: indexing is suppressed by `noindex` + `robots.txt` per FR-S025; SSR still helps OpenGraph preview cards on shared links).
- The story page is rendered through a per-template Jinja2 layout (`cover.html` + `inside.html`) loaded from the `MagazineTemplate` registry. The same editorial body is rendered into different layouts depending on `AthleteStory.template_key`.
- v1 ships **no AI-driven image editing**. Athletes upload photos as-is; templates apply visual treatments (vignettes, duotones, crop framing) entirely in CSS. AI-driven person-preserving image editing (subject-mask + background change) is deferred to a future iteration.
- Images are stored on a persistent volume — local-dev uses a host filesystem path; Railway uses a Railway Volume mounted at `STORY_IMAGE_DIR` (same pattern as `GARMIN_SESSION_DIR`). No S3 or CDN in v1.
- Trigger detection runs **inside the existing pipelines**, not as a new scheduled job. PR / pace-recalibration / difficult-week / race-day triggers fire from `_ingest_and_feedback` after a new activity is ingested; `race_upcoming` is detected by a once-daily check inside the existing morning check-in job. No new APScheduler job is required.
- Questions are **Claude-generated per session** based on the trigger event + athlete profile + recent context + the running transcript of prior questions/answers in the same session. Each call asks Claude for one question, 2–4 multiple-choice options, and a continuation signal ("ask another" or "session complete"). There is no hardcoded `INTERVIEW_QUESTIONS` list.
- v1 ships `race_complete` as the only milestone for story generation. The `milestone_type` column accepts only `race_complete` (CHECK constraint); future iterations may relax the constraint to add e.g. `block_complete` without a schema change beyond updating the CHECK.
- The magazine "My Story" section is a new `<section id="mystory">` card added to `magazine.html`, populated via a new `GET /api/stories/current` endpoint that returns the most recent in-progress story (or `null`).
- Story page typography reuses the existing Google Fonts already loaded by `/magazine` (DM Serif Display, Bebas Neue, Inter) — no additional font loading.
