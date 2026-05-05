# Feature Specification: Athlete Story / Magazine Spread

**Feature Branch**: `006-athlete-story` (to be created from `006-magazine-features`)
**Created**: 2026-05-05
**Status**: Draft
**Input**: User description: "A personalized milestone magazine spread — when an athlete completes a training block or race, the system interviews them with periodic questions, collects optional photos, and generates a Claude-authored magazine-style editorial article with a shareable public URL."

## Clarifications

### Session 2026-05-05

- Q: What triggers a story — only race completions, or also training blocks? → A: Both. `race_complete` fires when the athlete logs an activity on race day (detected from `Goal.race_date`). `block_complete` fires at the end of a training cycle defined by the coach. Admin can also manually trigger for testing without waiting for a real milestone.
- Q: How are the interview questions delivered to the athlete? → A: Via the existing chat FAB in `/magazine`. A scheduler job issues one question per week during the relevant training block. Questions appear as coach messages in the chat thread; the athlete answers naturally and the response is captured.
- Q: Can athletes upload photos? → A: Yes — a dedicated "My Story" section in `/magazine` shows the current in-progress story with an upload widget. Images are stored as files on disk (same pattern as `GARMIN_SESSION_DIR`). Maximum 5 images per story, each under 10 MB.
- Q: Who can see the story? → A: The public `GET /story/<share_token>` page requires no login. The share token is a URL-safe random string (12 chars). Unpublished stories are only visible to the athlete in their `/magazine` "My Story" section.
- Q: Is the editorial article editable by the athlete? → A: Not in v1. Athletes can regenerate (which calls Claude again) or leave feedback but cannot directly edit the generated text. Admins can trigger regeneration via the admin panel.
- Q: What does the admin test mode look like? → A: `POST /api/admin/athletes/<id>/generate-story?milestone=race_complete` — generates a draft story without requiring an actual race event. Returns `{story_id, preview_url}`. Preview URL is `/story/<share_token>?preview=1` which shows the story even before publication (authenticated only).
- Q: How long does generation take? → A: Claude call with full Q&A context — expect 10–20 seconds. Endpoint is async (returns `{job_id}` immediately); athlete can poll `/api/stories/status/<job_id>` or wait for a notification in the bell feed.
- Q: What format is the editorial output? → A: Plain text (400–600 words) formatted in paragraphs. The API returns raw text; the story page renders it with the magazine's DM Serif Display / Bebas Neue typography, large drop-cap on the first letter, pull quote highlighted mid-article.
- Q: What happens to old stories when a new milestone fires? → A: Each milestone creates a new `AthleteStory` row. Athletes accumulate a library of stories over time. `/magazine` "My Story" section shows the most recent; older stories are accessible from a "Past stories" list.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Interview Flow During Training Block (Priority: P1)

During a training block the athlete receives one coach question per week via the chat FAB in `/magazine`. They answer naturally in the chat thread. After 4–8 questions have been collected, the system has enough material to generate the editorial.

**Why this priority**: The interview is the core data-collection mechanism. Without answers the generated article has no personal material to draw on — it degenerates into generic platitudes.

**Independent Test**: Seed a `StoryQuestion` row with `answered_at=None` and verify the scheduler emits it as a chat message; seed a user reply and verify it is captured in `StoryQuestion.answer`.

**Acceptance Scenarios**:

1. **Given** the scheduler runs on Monday morning for an athlete in an active training block, **When** fewer than 4 questions have been answered this block, **Then** a new question is issued as a coach message in the athlete's chat thread and a `StoryQuestion` row is written with `story_id=None, answered_at=None`.
2. **Given** an unanswered question exists in the chat, **When** the athlete sends a reply in the next chat turn, **Then** `StoryQuestion.answer` and `answered_at` are populated; the conversation continues normally.
3. **Given** a question is already pending (unanswered), **When** the scheduler tick fires again, **Then** no new question is issued for that athlete.
4. **Given** the athlete has answered at least 4 questions and a milestone fires, **When** generation is triggered, **Then** all linked Q&A pairs are included in the Claude prompt.

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

### Edge Cases

- What if the athlete has answered no questions when a milestone fires? → Generate anyway using training stats alone; the editorial will be more generic but still complete.
- What if `Goal.race_date` is null? → `race_complete` milestone is never triggered. Admin can still fire manually.
- What if the athlete completes multiple races in a season? → Each creates a separate `AthleteStory` with its own interview thread. The "My Story" section shows the most recent active story.
- What if an image file is corrupted or cannot be thumbnailed? → Accept the upload (store the raw file) but skip thumbnail generation; show a fallback icon on the story page.
- What if the Claude-generated text contains the athlete's full name or sensitive health metrics the athlete wouldn't want public? → Out of scope for v1 — athlete previews before publishing. A future moderation step can be added.
- What if `share_token` collides? → Tokens are 12-character URL-safe random strings (72 bits entropy). Collision probability is negligible for any realistic user count. Uniqueness is enforced by a DB UNIQUE constraint; retry with a new token on the rare collision.
- What if generation is triggered while a previous generation job is already running? → Deduplicate by checking for a running job for the same `(athlete_id, milestone_type)` before enqueuing.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-S001**: System MUST create a `StoryQuestion` row and deliver the question as a coach message in the athlete's chat thread once per week during an active training block (up to 8 questions per block).
- **FR-S002**: System MUST capture the athlete's next chat reply following a pending question and store it in `StoryQuestion.answer` + `answered_at`.
- **FR-S003**: System MUST NOT issue a new question while a previous question for the same story is unanswered.
- **FR-S004**: System MUST detect a `race_complete` milestone when a `CompletedWorkout` date matches `Goal.race_date` (±1 day tolerance for time-zone edge cases).
- **FR-S005**: System MUST detect a `block_complete` milestone at the end of a training cycle (the last planned workout of a `TrainingPlan` is completed or past its date with no remaining planned sessions).
- **FR-S006**: System MUST generate an `AthleteStory` editorial via Claude when a milestone is detected, using: athlete profile, all answered `StoryQuestion` Q&A pairs, training stats (total miles, race time if applicable, weeks trained), and weekly HRV/sleep history.
- **FR-S007**: Generated editorial MUST be 400–600 words, structured in 3–5 paragraphs, in first-person coach voice narrating the athlete's journey.
- **FR-S008**: System MUST assign a unique 12-character URL-safe `share_token` to every `AthleteStory` row at creation time.
- **FR-S009**: System MUST write a bell `Notification` when story generation completes (success or failure).
- **FR-S010**: System MUST expose `GET /story/<share_token>` as a public route (no authentication required) that renders the story page in magazine typography.
- **FR-S011**: Unpublished stories (`published_at IS NULL`) MUST return 404 from the public route except when accessed by the owning athlete with `?preview=1`.
- **FR-S012**: System MUST provide a "My Story" section in `/magazine` showing the athlete's most recent in-progress or published story, including: cover image or placeholder, article preview (first 120 characters), upload widget, and Share/Republish button.
- **FR-S013**: Athletes MUST be able to upload up to 5 images per story (JPEG, PNG, WebP; max 10 MB each). Images are stored on disk under `STORY_IMAGE_DIR/<story_id>/`.
- **FR-S014**: Athletes MUST be able to add or edit a caption on each uploaded image. Captions are stored in `StoryImage.caption`.
- **FR-S015**: Athletes MUST be able to delete individual uploaded images before publication.
- **FR-S016**: System MUST expose `POST /api/admin/athletes/<id>/generate-story?milestone=<type>` (admin-only) to trigger story generation without a real milestone event.
- **FR-S017**: Admin endpoint MUST return `{story_id, preview_url}` synchronously (generation runs in a background thread); the preview URL is accessible to admins and the owning athlete before publication.
- **FR-S018**: System MUST enforce data isolation — all story/question/image queries are scoped to `athlete_id`; a logged-in athlete cannot access another athlete's draft story or images.
- **FR-S019**: Story image uploads MUST be rejected if the payload exceeds 10 MB or the MIME type is not image/jpeg, image/png, or image/webp.
- **FR-S020**: System MUST provide an API to list an athlete's past published stories (`GET /api/stories`) returning `[{story_id, title, milestone_type, created_at, published_at, preview_url, share_url}]`.

### Key Entities

- **`AthleteStory`**: `id`, `athlete_id`, `milestone_type` (`race_complete` | `block_complete`), `title` (athlete name + milestone label), `editorial_body` (Claude-generated text), `cover_image_path` (nullable — first `StoryImage`), `created_at`, `published_at` (nullable), `share_token` (unique, 12-char URL-safe random). One Alembic migration.
- **`StoryQuestion`**: `id`, `athlete_id`, `story_id` (nullable until linked at generation time), `question` (text), `answer` (nullable text), `asked_at`, `answered_at` (nullable). Same migration.
- **`StoryImage`**: `id`, `story_id`, `filename` (stored name on disk), `caption` (nullable text), `sort_order` (int, default 0), `uploaded_at`. Same migration.
- **Notification**: Reuses existing model — `kind="story_ready"` or `kind="story_failed"`.

### New Environment Variable

- **`STORY_IMAGE_DIR`**: Directory for athlete story images. Default: `/data/story_images/`. Each story's images live in `<STORY_IMAGE_DIR>/<story_id>/`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-S001**: Story generation completes within 30 seconds of being triggered (Claude call + DB write).
- **SC-S002**: Public story page (`GET /story/<token>`) loads in under 1.5 seconds on a local network connection with no authentication.
- **SC-S003**: Image upload round-trip (POST + DB write + disk write) completes within 3 seconds for a 5 MB image.
- **SC-S004**: The "My Story" section in `/magazine` renders within the existing hydrate call — no additional page-load latency.
- **SC-S005**: Admin test-mode endpoint returns `{story_id, preview_url}` within 2 seconds (generation runs async).
- **SC-S006**: Weekly question issuance is idempotent — running the scheduler job multiple times in the same week produces exactly one question per athlete.

## Assumptions

- Story generation is triggered server-side (scheduler job or activity poll callback), not by the athlete explicitly pressing a button.
- The public story page is a server-rendered Jinja2 template, not a React/JS SPA — simpler, faster, and more indexable by search engines.
- Images are stored on the same host filesystem as Garmin OAuth tokens (NAS volume). No S3 or CDN in v1.
- The scheduler for weekly questions uses APScheduler's existing `BlockingScheduler` — a new daily job added alongside the existing morning check-in and activity poll jobs.
- Questions are predefined — Claude does not generate questions on the fly; they come from a hardcoded list in `coach/story.py` (`INTERVIEW_QUESTIONS`) covering: motivation, a memorable training moment, the toughest week, race-day emotions, what they learned about themselves.
- `block_complete` detection depends on `TrainingPlan.end_date` being set; if null, only `race_complete` triggers are active.
- The magazine "My Story" section is a new `<section id="mystory">` card added to `magazine.html`, populated via a new `GET /api/stories/current` endpoint that returns the most recent in-progress story (or `null`).
- Story page typography reuses the existing Google Fonts already loaded by `/magazine` (DM Serif Display, Bebas Neue, Inter) — no additional font loading.
