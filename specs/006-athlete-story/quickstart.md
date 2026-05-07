# Quickstart — Athlete Story / Magazine Spread

This guide assumes the migration has been applied and the feature has been implemented per `plan.md` + `tasks.md`. It walks through opting in, testing an interview, generating a story, and exercising the admin endpoints.

---

## Local dev setup

```bash
# 1. Apply the new migration (adds athlete_stories, story_interview_sessions,
#    story_questions, story_images + Athlete.story_opt_in/story_intro_seen_at)
alembic upgrade head

# 2. Set image storage dir (optional — defaults to /data/story_images/)
export STORY_IMAGE_DIR=/tmp/story_images
mkdir -p $STORY_IMAGE_DIR

# 3. Verify the magazine-template registry loads at boot
python -c "from running_coach_ai.coach.story_templates import load_registry; print(list(load_registry().keys()))"
# → ['vogue', 'runners_world', 'times_long_read', 'outside', 'gq_profile']

# 4. Start the web app
python web.py
```

---

## Athlete opt-in flow

1. Log in to `/magazine` as the athlete.
2. From a "Story" toggle in the magazine settings, set the opt-in switch to ON.
   - Triggers `POST /api/stories/opt-in` with `{"opted_in": true}`.
3. The next page load shows the multi-page intro modal (no triggers fire yet).
4. Click through the modal pages. The final page button calls `POST /api/stories/intro-acknowledged`.
   - Triggers can now fire for this athlete.

Verification:
```bash
sqlite3 data/coach.db "SELECT id, story_opt_in, story_intro_seen_at FROM athletes WHERE id=1;"
# → 1|1|2026-05-07 16:02:11.123456
```

---

## Testing the interview flow (admin shortcut)

You don't need to wait for a real PR or race. Use the admin endpoint to fire a session.

```bash
# Open an interview session for athlete 1 with a synthetic 'race_complete' trigger
curl -s -b "session=<your_admin_session>" \
  -X POST "http://localhost:8080/api/admin/athletes/1/start-interview?trigger=race_complete" \
  | python -m json.tool
# → {"session_id": 42, "first_question": {"question_id": 100, "question": "...", "options": [...]}}
```

The first question is a consent prompt. The athlete sees it in the chat panel:

> "I'd like to capture something for your story — can I ask a few questions about your race?"
> [ Yes, go ahead ] [ Maybe later ] [ Skip ]

If the athlete clicks "Skip" → `POST /api/stories/sessions/42/decline` closes the session.
If the athlete clicks "Yes, go ahead" → `POST /api/stories/sessions/42/respond` records the answer and Claude generates the next question (~2 s).

The athlete can answer free-text in the chat OR click an option button. Either way, the next adaptive question appears until the session reaches its natural end (5–10 questions).

---

## Generating a story (admin shortcut)

Once the athlete has answered ≥4 questions across one or more sessions, you can generate the editorial:

```bash
curl -s -b "session=<your_admin_session>" \
  -X POST "http://localhost:8080/api/admin/athletes/1/generate-story?milestone=race_complete" \
  | python -m json.tool
# → {"story_id": 7, "preview_url": "/story/abc123XYZ?preview=1"}
```

The story has been auto-template-selected, e.g. `vogue` for a city-half PR. View the draft (preview requires admin OR owning-athlete login):

```bash
open "http://localhost:8080/story/abc123XYZ?preview=1"
```

---

## Re-rendering in different templates (admin)

```bash
# Cycle through every template to inspect how the same editorial reads
for tpl in vogue runners_world times_long_read outside gq_profile; do
  curl -s -b "session=<admin>" \
    -X POST "http://localhost:8080/api/admin/stories/7/render?template=${tpl}" \
    | python -m json.tool
  echo "--- ${tpl}: open ${preview_url} ---"
done
```

---

## Athlete: swap, publish, unpublish, delete

```bash
# 1. Athlete swaps their template (locks it for future regenerations)
curl -s -b "session=<athlete>" \
  -X POST "http://localhost:8080/api/stories/7/template" \
  -H "Content-Type: application/json" \
  -d '{"template_key": "outside"}'
# → {"ok": true, "preview_url": "/story/abc123XYZ?preview=1", "template_key": "outside"}

# 2. Athlete publishes
curl -s -b "session=<athlete>" -X POST "http://localhost:8080/api/stories/7/publish"
# → {"ok": true, "share_url": "/story/abc123XYZ", "published_at": "..."}

# 3. Anyone can now view the public page (no login needed)
open "http://localhost:8080/story/abc123XYZ"

# 4. Athlete unpublishes (link returns 404 again, draft preserved)
curl -s -b "session=<athlete>" -X POST "http://localhost:8080/api/stories/7/unpublish"
# → {"ok": true, "story_id": 7}

# 5. Athlete soft-deletes (story disappears from /magazine)
curl -s -b "session=<athlete>" -X DELETE "http://localhost:8080/api/stories/7"
# → {"ok": true, "story_id": 7}

# 6. Admin hard-deletes (removes row + image files + sessions)
curl -s -b "session=<admin>" -X DELETE "http://localhost:8080/api/admin/stories/7?hard=1"
# → {"ok": true, "deleted": {"story_rows": 1, "image_rows": 3, ...}}
```

---

## Image upload

```bash
curl -s -b "session=<athlete>" \
  -X POST "http://localhost:8080/api/stories/current/images" \
  -F "file=@my_race_photo.jpg" \
  -F "caption=Mile 12, Prospect Park"
# → 201 {"image": {"id": 11, "url": "/api/stories/images/11/raw", ...}}
```

---

## Verifying the public route protections

```bash
# robots.txt
curl http://localhost:8080/robots.txt
# → User-agent: *
# → Disallow: /story/

# noindex meta tag
curl http://localhost:8080/story/abc123XYZ | grep -i 'name="robots"'
# → <meta name="robots" content="noindex,nofollow">

# Rate limit (run 31 times fast and the 31st returns 429)
for i in {1..31}; do curl -o /dev/null -s -w "%{http_code}\n" http://localhost:8080/story/abc123XYZ; done
# → 200 ... 200 (30 times) ... 429
```

---

## Files created or modified by implementation

| File | Status | Purpose |
|---|---|---|
| `running_coach_ai/coach/story.py` | NEW | Trigger detection helpers, session orchestration, Claude prompts |
| `running_coach_ai/coach/story_templates.py` | NEW | File-system magazine-template registry loader |
| `running_coach_ai/database/models.py` | EXTENDED | `AthleteStory`, `StoryInterviewSession`, `StoryQuestion`, `StoryImage`, `Athlete.story_*` |
| `running_coach_ai/database/migrations/versions/p2l3m4n5o6p7_add_athlete_stories.py` | NEW | Single migration |
| `running_coach_ai/coach/conversation.py` | EXTENDED | Pre-Claude hook captures answer when a `StoryQuestion` is pending |
| `running_coach_ai/scheduler/activity_poll.py` | EXTENDED | `pr_set` / `pace_recalibration` / `race_complete` detection after each ingest |
| `running_coach_ai/scheduler/morning.py` | EXTENDED | `race_upcoming` and `difficult_week` detection |
| `running_coach_ai/web/api/stories.py` | NEW | Athlete blueprint (opt-in, list, current, swap, publish, images, sessions) |
| `running_coach_ai/web/api/admin.py` | EXTENDED | start-interview, generate-story, render, hard-delete, list-templates |
| `running_coach_ai/web/routes/public_story.py` | NEW | Public route + robots.txt + per-IP rate limit |
| `running_coach_ai/web/templates/story_page.html` | NEW | Outer Jinja2 wrapper for the chosen template |
| `running_coach_ai/web/static/story_templates/<key>/` | NEW (×5) | One subdirectory per template — vogue, runners_world, times_long_read, outside, gq_profile |
| `running_coach_ai/web/static/magazine.html` | EXTENDED | Opt-in toggle, intro modal, "My Story" section, swap carousel, image upload |
| `running_coach_ai/web/static/magazine.css` | EXTENDED | Selectors for the new sections |
| `running_coach_ai/web/static/magazine.js` | EXTENDED | Modal flow, image upload UI, template swap UI, session response handling |
| `running_coach_ai/config.py` | EXTENDED | `STORY_IMAGE_DIR` Pydantic setting |
| `tests/unit/test_story_triggers.py` | NEW | PR / race / pace / difficult-week detection |
| `tests/unit/test_story_session.py` | NEW | Opt-in gate, consent flow, adaptive Q&A loop |
| `tests/unit/test_story_generation.py` | NEW | Editorial generation + auto-template selection |
| `tests/unit/test_story_templates.py` | NEW | Registry loader, fallback behavior |
| `tests/unit/web/test_stories_api.py` | NEW | Athlete endpoints |
| `tests/unit/web/test_admin_story_api.py` | NEW | Admin endpoints |
| `tests/unit/web/test_public_story_route.py` | NEW | Share-token routing, rate limit, robots.txt |
| `tests/integration/web/test_story_flow.py` | NEW | Opt-in → trigger → interview → generate → render flow |
