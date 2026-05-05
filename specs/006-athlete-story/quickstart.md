# Quickstart: Athlete Story

## Local dev setup

```bash
# Apply the new migration (adds athlete_stories, story_questions, story_images)
alembic upgrade head

# Set image storage dir (optional — defaults to /data/story_images/)
export STORY_IMAGE_DIR=/tmp/story_images

# Admin test-mode: generate a draft story for athlete 1
curl -s -b "session=<your_session>" \
  -X POST "http://localhost:8080/api/admin/athletes/1/generate-story?milestone=race_complete" \
  | python -m json.tool
# → {"story_id": 1, "preview_url": "/story/<token>?preview=1"}

# View the draft (must be logged in as admin or the owning athlete)
open http://localhost:8080/story/<token>?preview=1

# Publish (athlete presses Share in /magazine — or simulate directly)
curl -s -b "session=<your_session>" \
  -X POST "http://localhost:8080/api/stories/1/publish" \
  | python -m json.tool
# → {"ok": true, "share_url": "/story/<token>"}

# Public story page (no login needed once published)
open http://localhost:8080/story/<token>
```

## Testing the interview flow

```bash
# Seed a StoryQuestion manually to simulate an unanswered question
# (scheduler normally does this)
python - <<'EOF'
from running_coach_ai.database.session import get_session
from running_coach_ai.database.models import StoryQuestion
from datetime import datetime, timezone

with get_session() as db:
    db.add(StoryQuestion(
        athlete_id=1,
        question="What was the toughest week of this training block, and what got you through it?",
        asked_at=datetime.now(timezone.utc),
    ))
EOF

# Then open /magazine and send a chat message — the next reply
# should be captured as the answer to the pending question.
```

## Key files (to be created)

| File | Purpose |
|---|---|
| `running_coach_ai/coach/story.py` | `INTERVIEW_QUESTIONS` list, `generate_story()`, `detect_milestone()`, `issue_question()` |
| `running_coach_ai/web/api/stories.py` | `/api/stories`, `/api/stories/current`, `/api/stories/<id>/publish`, `/api/stories/<id>/images` |
| `running_coach_ai/web/api/admin.py` | Extend with `POST /api/admin/athletes/<id>/generate-story` |
| `running_coach_ai/web/templates/story.html` | Public story page (Jinja2, magazine typography, mobile-friendly) |
| `running_coach_ai/database/migrations/versions/<hash>_add_story_tables.py` | New migration |
| `running_coach_ai/web/static/magazine.html` | Add `#mystory` section |
| `running_coach_ai/scheduler/jobs.py` | Add `issue_story_questions` weekly job |
