# Phase 0 Research — Athlete Story / Magazine Spread

This document resolves the open architectural choices that the spec left to planning. Each section follows the format: **Decision** / **Rationale** / **Alternatives considered**.

---

## R-001: Adaptive question generation — single-call vs multi-step Claude invocation

**Decision**: Each turn within a `StoryInterviewSession` is **one Claude call** that returns a structured JSON payload containing both the next question and a `session_complete` boolean. We do not use Claude's tool-use API for this — we ask for raw JSON in a single response.

**Rationale**:
- Tool-use adds latency and cost without meaningful safety here — questions are short, and the payload shape is fixed.
- A single call per turn matches the existing `coach.persona.call_claude` wrapper exactly; no new SDK pattern.
- The `session_complete` boolean lets Claude itself decide when 5 questions are enough vs when 10 are warranted, satisfying FR-S030's "adaptive depth" requirement without a separate "should I keep asking" call.
- Output schema: `{"question": "...", "options": ["a", "b", "c"], "session_complete": false}`. Validation is a one-liner (`isinstance(d["options"], list) and 2 <= len(d["options"]) <= 4`); on schema failure we retry once, then abort the session per Edge Cases.

**Alternatives considered**:
- **Tool use with a `next_question` tool**: stricter validation but adds 1–2 s latency and requires an Anthropic SDK upgrade pattern unused elsewhere in the codebase.
- **Streaming**: a chat-like trickle of tokens. Rejected — interview cadence is already inherently turn-based (athlete must answer each question before the next is generated), so streaming adds no UX value and complicates persistence.
- **Pre-generate a fixed question tree (`if answer is X, ask Y`)**: would defeat the "veteran interviewer adapts to the athlete" goal articulated by the user.

---

## R-002: Editorial generation — single Claude call returns body + template vote

**Decision**: A single Claude call per generation produces (a) the 400–600 word editorial body and (b) the `template_key` vote. The system prompt lists every registered template's key + voice description + trigger affinities and asks Claude to choose one. The response shape is `{"editorial_body": "...", "template_key": "<key>"}`.

**Rationale**:
- Saves a round-trip: one call instead of "generate body, then choose template".
- Lets Claude write toward the chosen voice — the voice description for the chosen template is in the prompt context, so the prose comes back already aligned with the layout.
- If `template_locked_by_athlete=True`, the prompt skips the template list and injects only the locked template's voice; the response shape becomes `{"editorial_body": "..."}` with the lock honored implicitly.

**Alternatives considered**:
- **Two-call design (body then vote)**: clean separation but doubles latency and cost; Claude has no extra context on the second call that justifies it.
- **Athlete-only template selection (no Claude vote)**: removes Claude's editorial judgement; trigger-affinity prior alone produces predictable but boring picks (every PR → Runner's World, every race → Vogue). Worse UX.
- **Template-locked voice without telling Claude about other templates**: works but means the voice description in the prompt is a single string. Same outcome — kept this approach for the locked path.

---

## R-003: Magazine template registry — file-system layout

**Decision**: Templates live under `web/static/story_templates/<key>/` with a fixed file set (`template.json`, `cover.html`, `inside.html`, `template.css`, `thumbnail.jpg`). Loading happens at Flask app startup via `coach.story_templates.load_registry()`, which scans the directory, parses each `template.json`, and returns an in-memory `dict[str, MagazineTemplate]` cached on the Flask app context. Hot-reload is not required (a deploy/restart is acceptable for adding a template).

**Rationale**:
- File-system registry means **adding a new template = drop a directory + restart**. No DB migration, no code change, no admin UI to register it. Matches FR-S032's contract.
- Registry is cheap to load — five small text files per template; total cold-start cost <50 ms even for 20 templates.
- Storing CSS in `template.css` (not inline in HTML) lets the browser cache them across athletes/stories per the existing `magazine.css` pattern.
- `template.json` validation at load time rejects malformed templates and logs a warning; the missing template is simply absent from the registry (the spec's edge-case fallback behavior covers the runtime impact).

**Alternatives considered**:
- **DB-backed templates**: would let admins add templates without a deploy. Rejected — templates contain raw HTML/CSS authored by developers, not user content; restarting Flask for a new template is not a real friction in a self-hosted single-tenant deploy.
- **Python class-based registry (`@register_template` decorator)**: more "Pythonic" but couples templates to Python imports and makes it harder for a non-Python contributor to add a template.
- **One mega-template with a CSS variant per key**: rejected because the layouts genuinely differ (column count, sidebar presence, photo grid shape) — CSS overrides alone can't express it cleanly.

---

## R-004: Trigger detection — where to inject each kind

**Decision**:

| Trigger | Detection point | Logic |
|---|---|---|
| `pr_set` | `scheduler/activity_poll.py:_ingest_and_feedback` after biomechanics analysis | Compare current `CompletedWorkout.distance_km` + `avg_pace_min_per_km` against the best-recorded pace at any greater-or-equal distance for the athlete; ≥1% improvement fires |
| `pace_recalibration` | `coach/side_effects.py:extract_and_apply_plan` after a `<plan>` block applies a faster `target_pace_min_per_km` to ≥3 sessions | Compute the average pace shift; ≥10 sec/mi faster across ≥3 sessions fires |
| `race_upcoming` | `scheduler/morning.py:_run_morning_checkin_for_athlete` (existing daily job) | Check `Goal.active=True` athletes for `race_date == today + 7 days`; fires once |
| `race_complete` | `scheduler/activity_poll.py:_ingest_and_feedback` after `parse_activity_summary` | Match `CompletedWorkout.date` ± 1 day against `Goal.race_date` for any active goal |
| `difficult_week` | `scheduler/morning.py:_run_morning_checkin_for_athlete` | Same job that already reads HRV trend; check (a) HRV declining ≥3 consecutive days, (b) ≥2 missed planned sessions in last 7 days, (c) any run with `hr_drift_pct > 15` in last 7 days |

Each detection writes a `StoryInterviewSession` row only if: athlete has `story_opt_in=True`, `story_intro_seen_at IS NOT NULL`, and no other session is currently active.

**Rationale**:
- Reuses existing pipelines — no new APScheduler job. `_ingest_and_feedback` already loads the activity, biomechanics, and athlete context the triggers need; the morning job already reads HRV data.
- Trigger collisions resolved by a centralized helper `coach.story.fire_trigger_if_eligible(athlete, trigger_kind, context)` that applies the priority rules from spec edge cases (race_complete > race_upcoming > pr_set > pace_recalibration > difficult_week) and the active-session guard.
- Detection logic is contained in unit-testable functions (`detect_pr_set(completed, history)`, `detect_difficult_week(athlete, db)`, etc.) — each can be tested without spinning up the scheduler.

**Alternatives considered**:
- **Dedicated "story trigger scan" job**: a new daily job would duplicate logic the morning/activity-poll jobs already do. Rejected.
- **Database triggers / event sourcing**: SQLite supports triggers but adds operational complexity for a single-tenant app. Rejected.

---

## R-005: Per-IP rate limit on `/story/<token>`

**Decision**: A **simple in-process sliding-window counter**, keyed by `request.remote_addr`, evaluated as a Flask `@before_request` decorator on the public route. Window = 60 s, limit = 30 requests. Implementation in `web/routes/public_story.py` using a `collections.deque[float]` per IP under a `threading.Lock`. Stale entries pruned on each request.

**Rationale**:
- 30 req/min is well below the rate at which an in-memory counter becomes a memory concern (worst case: 100 unique IPs × 30 timestamps = 3000 floats; <30 KB).
- No external Redis dependency for what is essentially a "block obvious bots" measure.
- Resets on Flask app restart (acceptable — restarts already happen on Railway redeploys).
- Behind a reverse proxy (Railway), `request.remote_addr` returns the proxy IP unless we trust `X-Forwarded-For`. We **do** trust it because Railway routes traffic through their proxy and sets it correctly; verified via `request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()`.

**Alternatives considered**:
- **`flask-limiter` library**: adds a dependency for one route; heavyweight for the use case.
- **Redis sliding window**: adds an external service to the deploy. Rejected for a v1 with predictable single-host load.
- **No rate limit**: leaves the route open to scrapers. Rejected per Q4 clarification.

---

## R-006: Image thumbnailing & validation

**Decision**: Server-side validation via `Pillow` at upload time only:
- Reject if MIME type not `image/jpeg`, `image/png`, or `image/webp` (FR-S019).
- Reject if file size > 10 MB (FR-S019).
- Open with `Pillow.Image.open(io.BytesIO(file.read()))` to validate the file is decodable; reject on `UnidentifiedImageError`.
- **No thumbnail generation in v1** — store the original file as-is. The browser handles display sizing via CSS `object-fit` per the magazine's existing pattern.

**Rationale**:
- Pillow is already an indirect dependency via standard Python image-handling needs; adding it explicitly is one line in `requirements.txt`.
- Skipping thumbnailing keeps the upload path fast (3 s SLA per SC-S003 is easy) and avoids storing two files per image.
- Browser-side responsive images via CSS `srcset` is a v1.1 optimization if pages feel slow on mobile.

**Alternatives considered**:
- **Generate thumbnails at upload**: doubles disk usage and complicates cleanup on hard-delete. Rejected for v1.
- **Thumbnail on first request and cache**: works but introduces a request-time hot path for an uncached photo. Rejected — too much complexity for the visible benefit.
- **No server-side validation, trust the client**: rejected — file uploads are a classic untrusted boundary; we always validate.

---

## R-007: Multi-page intro modal — implementation pattern

**Decision**: A single inline `<div id="story-intro-modal">` in `magazine.html`, hidden by default, shown on first opened-magazine load when `me.story_opt_in === true && me.story_intro_seen_at == null`. Internally it's a 5-page slide carousel with Prev/Next buttons; the final page has an "I'm ready — start interviews" button that POSTs to `/api/stories/intro-acknowledged` (sets `Athlete.story_intro_seen_at = now()`) before closing the modal.

**Rationale**:
- Reuses the existing modal/overlay pattern from the onboarding-Garmin and admin panels — same DOM structure, same close-on-outside-click behavior.
- Inline carousel logic is ~80 LOC of vanilla JS in `magazine.js`; no carousel library needed.
- Each page is plain HTML + CSS — infographics are SVG inlined in the page divs, animated via CSS keyframes (already a pattern in `magazine.css` for hero terrain lines and ring-pulse on the chat FAB).

**Alternatives considered**:
- **Dedicated `/onboarding/story-intro` route**: would interrupt the magazine flow. Rejected — modal-in-place feels less disruptive.
- **External tour library (Shepherd.js, Driver.js)**: adds a 30 KB dependency for one feature. Rejected.

---

## R-008: Conversation flow — capturing answers to story questions

**Decision**: Extend `_handle_message_core` in `coach/conversation.py` (the orchestrator from Phase 2) with a single early check: if there's an unanswered `StoryQuestion` for this athlete (oldest first), the user's reply is stored in `StoryQuestion.answer` and `is_custom_answer=True`, and the next adaptive question is generated. The reply is **not** passed to the regular Claude turn — story answers don't need a coaching response. A short coach-voice acknowledgement is inserted into the chat history alongside the next question so the thread flows naturally.

For multiple-choice clicks (rendered as buttons in the chat panel), a separate POST endpoint `/api/stories/sessions/<id>/answer` accepts `{"question_id": <id>, "answer": "<option>"}` and routes through the same handler with `is_custom_answer=False`.

**Rationale**:
- Single hook in the orchestrator keeps the special-case logic isolated; `process_message` and the XML extractors stay unchanged.
- Free-text answers + button clicks both end up in the same `StoryQuestion.answer` field; the only difference is `is_custom_answer`.
- The next-question generation is still a Claude call but uses a tightly scoped system prompt (just the session context, not the full coaching prompt) so it's faster (~2 s) than a regular coaching turn.

**Alternatives considered**:
- **Treat story answers as regular coaching turns**: would generate full coach replies even though the system has no advice to give about "what felt hardest about week 8". Wasteful.
- **A dedicated chat-panel mode that bypasses the chat history altogether**: cleaner separation but means the athlete can't see their own answers in the chat thread, which feels weird.
- **WebSocket-based interview UI**: rejected — overkill for a 5–10 question interaction that uses existing HTTP endpoints.

---

## R-009: Generation cooldown enforcement (FR-S021)

**Decision**: Enforce in code at the API layer — `POST /api/stories/<id>/regenerate` checks `last_regenerated_at` and returns HTTP 429 with `{"error": "...", "retry_after": <seconds>}` if the last regeneration was less than 1 hour ago. Admin endpoint bypasses the check. No DB-level constraint.

**Rationale**:
- DB constraint can't easily express time-based windows portably across SQLite/Postgres.
- API-layer enforcement is uniformly testable and the error message is in the right place.
- 429 is the conventional HTTP status for rate-limit / cooldown.

**Alternatives considered**:
- **Trigger-based DB enforcement**: SQLite triggers can do it but ties the logic to the DB layer where it's hard to test and reason about.
- **Rolling token bucket per athlete**: overengineered for "1 per hour".

---

## Summary

All eight Technical Context items are resolved without `NEEDS CLARIFICATION` markers. No external services beyond the existing Anthropic API are introduced. The feature ships with one Alembic migration, one new module per concern, and a single new static-asset directory. All new code paths are unit-testable in isolation; full integration tests cover the opt-in → trigger → interview → generate → render flow.
