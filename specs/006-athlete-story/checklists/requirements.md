# Requirements Checklist: Athlete Story

## Functional Requirements

- [ ] FR-S001 — Weekly question issuance via scheduler (once per week, max 8 per block)
- [ ] FR-S002 — Capture athlete chat reply as `StoryQuestion.answer`
- [ ] FR-S003 — No new question while previous is unanswered
- [ ] FR-S004 — Detect `race_complete` milestone from activity poll
- [ ] FR-S005 — Detect `block_complete` milestone from training plan end
- [ ] FR-S006 — Generate `AthleteStory` editorial via Claude on milestone
- [ ] FR-S007 — Editorial is 400–600 words, 3–5 paragraphs, first-person coach voice
- [ ] FR-S008 — Assign unique 12-char URL-safe `share_token` at creation
- [ ] FR-S009 — Write bell Notification on generation success and failure
- [ ] FR-S010 — `GET /story/<share_token>` public route, no auth required
- [ ] FR-S011 — Unpublished stories return 404 for non-owners; preview=1 for owner
- [ ] FR-S012 — "My Story" section in `/magazine` (cover, preview text, upload widget, Share button)
- [ ] FR-S013 — Image upload: up to 5, JPEG/PNG/WebP, max 10 MB, stored on disk
- [ ] FR-S014 — Caption add/edit per image (PATCH)
- [ ] FR-S015 — Image delete before publication
- [ ] FR-S016 — Admin endpoint `POST /api/admin/athletes/<id>/generate-story?milestone=<type>`
- [ ] FR-S017 — Admin endpoint returns `{story_id, preview_url}` synchronously
- [ ] FR-S018 — Data isolation: all queries scoped to `athlete_id`
- [ ] FR-S019 — Upload validation: reject > 10 MB and non-image MIME types
- [ ] FR-S020 — `GET /api/stories` returns athlete's published story list

## Non-Functional Requirements

- [ ] SC-S001 — Generation completes within 30 seconds
- [ ] SC-S002 — Public story page loads in under 1.5 seconds
- [ ] SC-S003 — Image upload round-trip under 3 seconds for 5 MB
- [ ] SC-S004 — "My Story" section within existing hydrate call (no extra latency)
- [ ] SC-S005 — Admin test endpoint returns within 2 seconds
- [ ] SC-S006 — Question issuance is idempotent (one per athlete per week)
