# Data Model — Athlete Story / Magazine Spread

All four new tables and the two new `Athlete` columns are introduced in a single Alembic migration: **`p2l3m4n5o6p7_add_athlete_stories.py`**. SQLAlchemy 2.x declarative style, matches the existing pattern in `running_coach_ai/database/models.py`.

---

## 1. `Athlete` — extended (existing model, two new columns)

| Column | Type | Constraints | Default | Notes |
|---|---|---|---|---|
| `story_opt_in` | `Boolean` | `NOT NULL` | `False` | Athlete must explicitly enable from `/magazine` settings (FR-S001) |
| `story_intro_seen_at` | `DateTime` | `NULL` allowed | `NULL` | Set when athlete acknowledges the multi-page intro modal (FR-S027) |

**Migration**: `op.add_column("athletes", sa.Column("story_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()))` and `op.add_column("athletes", sa.Column("story_intro_seen_at", sa.DateTime(), nullable=True))`.

---

## 2. `AthleteStory` — new

Represents one editorial spread for one milestone. Generated, swappable across templates, publishable, soft-deletable.

| Column | Type | Constraints | Default | Notes |
|---|---|---|---|---|
| `id` | `Integer` | PK, autoincrement | — | |
| `athlete_id` | `Integer` | FK → `athletes.id`, `NOT NULL`, indexed | — | Cascade-on-delete from athlete row |
| `milestone_type` | `String(32)` | `NOT NULL`, CHECK = `race_complete` | — | The milestone that triggered generation. v1 ships only `race_complete`; the column type allows future expansion without a schema change. |
| `title` | `String(255)` | `NOT NULL` | — | "<athlete first name> · <event label>"  |
| `editorial_body` | `Text` | `NOT NULL` | — | Claude-generated 400–600 word prose; overwritten on regeneration |
| `cover_image_path` | `String(512)` | `NULL` | `NULL` | Filesystem path to first `StoryImage` (or `NULL` for placeholder gradient) |
| `template_key` | `String(64)` | `NOT NULL` | `vogue` | FK by convention to a directory under `web/static/story_templates/` |
| `template_locked_by_athlete` | `Boolean` | `NOT NULL` | `False` | True after athlete swaps via `/api/stories/<id>/template` |
| `share_token` | `String(16)` | `UNIQUE`, `NOT NULL`, indexed | — | 12 URL-safe chars (~72 bits entropy); preserved across regenerations |
| `regeneration_count` | `Integer` | `NOT NULL` | `0` | Increments on each regeneration |
| `last_regenerated_at` | `DateTime` | `NULL` | `NULL` | Used for the 1-hour athlete cooldown (FR-S021) |
| `created_at` | `DateTime` | `NOT NULL` | `now()` | Set once at creation; preserved across regenerations |
| `published_at` | `DateTime` | `NULL` | `NULL` | Set on Share button click; cleared on Unpublish |
| `deleted_at` | `DateTime` | `NULL`, indexed | `NULL` | Soft-delete marker — non-NULL excludes from all athlete queries (FR-S023) |

**Indexes**:
- `(athlete_id, deleted_at)` for the most common athlete-scoped query
- `(athlete_id, milestone_type, deleted_at)` for the existing-story-for-this-milestone lookup at trigger time
- `share_token` (unique) for the public route

**Lifecycle states** (informational, no `state` column):
- **draft** — `published_at IS NULL AND deleted_at IS NULL`
- **published** — `published_at IS NOT NULL AND deleted_at IS NULL`
- **unpublished** — `published_at IS NULL AND deleted_at IS NULL` (after explicit unpublish; same shape as draft, distinguishable from draft only by behavior in /api/stories/current vs /api/stories)
- **soft-deleted** — `deleted_at IS NOT NULL` (excluded from all athlete-facing queries, returns 404 on public route)

---

## 3. `StoryInterviewSession` — new

Represents one event-triggered interview session. Groups multiple `StoryQuestion` rows.

| Column | Type | Constraints | Default | Notes |
|---|---|---|---|---|
| `id` | `Integer` | PK, autoincrement | — | |
| `athlete_id` | `Integer` | FK → `athletes.id`, `NOT NULL`, indexed | — | |
| `trigger_kind` | `String(32)` | `NOT NULL`, CHECK in (`pr_set`, `race_upcoming`, `race_complete`, `pace_recalibration`, `difficult_week`) | — | What event opened the session |
| `trigger_context_json` | `JSON` | `NOT NULL` | `{}` | Free-form — e.g. `{"completed_workout_id": 42}` for `pr_set`, `{"goal_id": 7}` for race triggers |
| `started_at` | `DateTime` | `NOT NULL` | `now()` | |
| `completed_at` | `DateTime` | `NULL`, indexed | `NULL` | NULL = active; only one active session per athlete (FR-S003) |
| `skipped` | `Boolean` | `NOT NULL` | `False` | True if athlete declined the consent prompt; closed without questions |
| `story_id` | `Integer` | FK → `athlete_stories.id`, `NULL` | `NULL` | Set when the session's Q&As are linked into a generated story |

**Indexes**:
- `(athlete_id, completed_at)` for the "is there an active session?" check
- `(athlete_id, story_id)` for collecting all sessions feeding a particular story

**State transitions**:
- `started` (`completed_at IS NULL`) → `completed` (`completed_at = now()`, `skipped = False`) when Claude marks session_complete OR 10 questions answered
- `started` → `skipped` (`completed_at = now()`, `skipped = True`) when athlete declines consent or stalls 7+ days
- `completed` → linked (set `story_id`) at generation time

---

## 4. `StoryQuestion` — new

One question + (optional) answer. Belongs to a session; gets linked to a story at generation time.

| Column | Type | Constraints | Default | Notes |
|---|---|---|---|---|
| `id` | `Integer` | PK, autoincrement | — | |
| `session_id` | `Integer` | FK → `story_interview_sessions.id`, `NOT NULL`, indexed | — | Cascade on session deletion |
| `athlete_id` | `Integer` | FK → `athletes.id`, `NOT NULL`, indexed | — | Denormalised for `scoped_query` per the constitution |
| `story_id` | `Integer` | FK → `athlete_stories.id`, `NULL` | `NULL` | Set at generation time |
| `question_index` | `Integer` | `NOT NULL` | — | 1-based ordering within the session; `(session_id, question_index)` UNIQUE |
| `question` | `Text` | `NOT NULL` | — | Claude-generated question text |
| `options_json` | `JSON` | `NOT NULL` | — | List of 2–4 strings (multiple-choice options) |
| `answer` | `Text` | `NULL` | `NULL` | Athlete's chosen option string OR free-text |
| `is_custom_answer` | `Boolean` | `NULL` | `NULL` | `False` if athlete picked an option, `True` if free text, `NULL` if unanswered |
| `asked_at` | `DateTime` | `NOT NULL` | `now()` | |
| `answered_at` | `DateTime` | `NULL` | `NULL` | Set when answer is captured |

**Indexes**:
- `(session_id, question_index)` — primary access pattern (load all questions for a session, ordered)
- `(athlete_id, answered_at)` — for "questions awaiting answer for this athlete" check (FR-S003 enforcement)

---

## 5. `StoryImage` — new

Athlete-uploaded photo attached to a story. File on disk under `STORY_IMAGE_DIR/<story_id>/<filename>`.

| Column | Type | Constraints | Default | Notes |
|---|---|---|---|---|
| `id` | `Integer` | PK, autoincrement | — | |
| `story_id` | `Integer` | FK → `athlete_stories.id`, `NOT NULL`, indexed | — | Cascade on story hard-delete (rows go); preserved on soft-delete |
| `filename` | `String(255)` | `NOT NULL` | — | Stored name on disk (UUID-prefixed); not user-input |
| `caption` | `Text` | `NULL` | `NULL` | Athlete-typed caption (FR-S014) |
| `sort_order` | `Integer` | `NOT NULL` | `0` | Athlete-controlled display order |
| `uploaded_at` | `DateTime` | `NOT NULL` | `now()` | |

**Constraints**:
- Per-story image count enforced at the API layer (FR-S013: max 5) — checked before insert.
- File size + MIME type validation at upload (FR-S019).

**Indexes**:
- `(story_id, sort_order)` — ordered display

---

## 6. Validation rules (enforced at API/service layer)

These don't live in the schema but are part of the data contract:

- `AthleteStory.template_key` MUST be present in the magazine-template registry at the time of write (validated by `coach.story_templates.is_registered(key)`).
- `StoryQuestion.options_json` MUST be a list of 2–4 non-empty strings; rejected at insert otherwise.
- `StoryQuestion.is_custom_answer` MUST be `NULL` when `answered_at IS NULL`, and non-`NULL` otherwise.
- `StoryInterviewSession`: at most one row per `athlete_id` with `completed_at IS NULL` at any moment (FR-S003).
- `AthleteStory.published_at` MAY be set or cleared multiple times (publish → unpublish → republish per FR-S022).
- Hard-delete (`DELETE /api/admin/stories/<id>?hard=1`): cascades `StoryImage` rows + their files on disk and any `StoryInterviewSession` rows whose `story_id` matches.

---

## 7. Relationships

```
Athlete ──< AthleteStory ──< StoryImage
       │                ╲
       │                 ╲── StoryInterviewSession.story_id (set at generation)
       │                       │
       │                       ╲── StoryQuestion.story_id (set at generation)
       │
       ╲── StoryInterviewSession ──< StoryQuestion
```

---

## 8. Migration outline (single file)

```python
# database/migrations/versions/p2l3m4n5o6p7_add_athlete_stories.py

revision = "p2l3m4n5o6p7"
down_revision = "<latest_existing_head>"   # filled when the migration is generated

def upgrade():
    # 1. Extend athletes table
    op.add_column("athletes", sa.Column("story_opt_in", sa.Boolean(), nullable=False,
                                         server_default=sa.false()))
    op.add_column("athletes", sa.Column("story_intro_seen_at", sa.DateTime(), nullable=True))

    # 2. Create athlete_stories
    op.create_table("athlete_stories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("athlete_id", sa.Integer(),
                  sa.ForeignKey("athletes.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("milestone_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("editorial_body", sa.Text(), nullable=False),
        sa.Column("cover_image_path", sa.String(512), nullable=True),
        sa.Column("template_key", sa.String(64), nullable=False, server_default="vogue"),
        sa.Column("template_locked_by_athlete", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("share_token", sa.String(16), unique=True, nullable=False, index=True),
        sa.Column("regeneration_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_regenerated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, index=True),
        sa.CheckConstraint("milestone_type = 'race_complete'",
                           name="ck_athlete_stories_milestone_type"),
    )
    op.create_index("ix_athlete_stories_athlete_deleted",
                    "athlete_stories", ["athlete_id", "deleted_at"])
    op.create_index("ix_athlete_stories_athlete_milestone",
                    "athlete_stories", ["athlete_id", "milestone_type", "deleted_at"])

    # 3. Create story_interview_sessions
    op.create_table("story_interview_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("athlete_id", sa.Integer(),
                  sa.ForeignKey("athletes.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("trigger_kind", sa.String(32), nullable=False),
        sa.Column("trigger_context_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True, index=True),
        sa.Column("skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("athlete_stories.id"),
                  nullable=True),
        sa.CheckConstraint(
            "trigger_kind IN ('pr_set','race_upcoming','race_complete',"
            "'pace_recalibration','difficult_week')",
            name="ck_story_sessions_trigger_kind",
        ),
    )
    op.create_index("ix_story_sessions_athlete_completed",
                    "story_interview_sessions", ["athlete_id", "completed_at"])
    op.create_index("ix_story_sessions_athlete_story",
                    "story_interview_sessions", ["athlete_id", "story_id"])

    # 4. Create story_questions
    op.create_table("story_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(),
                  sa.ForeignKey("story_interview_sessions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("athlete_id", sa.Integer(), sa.ForeignKey("athletes.id"),
                  nullable=False, index=True),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("athlete_stories.id"), nullable=True),
        sa.Column("question_index", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options_json", sa.JSON(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("is_custom_answer", sa.Boolean(), nullable=True),
        sa.Column("asked_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("session_id", "question_index", name="uq_story_question_index"),
    )
    op.create_index("ix_story_questions_athlete_answered",
                    "story_questions", ["athlete_id", "answered_at"])

    # 5. Create story_images
    op.create_table("story_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story_id", sa.Integer(),
                  sa.ForeignKey("athlete_stories.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_story_images_story_sort",
                    "story_images", ["story_id", "sort_order"])


def downgrade():
    op.drop_table("story_images")
    op.drop_table("story_questions")
    op.drop_table("story_interview_sessions")
    op.drop_table("athlete_stories")
    op.drop_column("athletes", "story_intro_seen_at")
    op.drop_column("athletes", "story_opt_in")
```

---

## 9. New configuration (Pydantic settings)

| Setting | Default | Notes |
|---|---|---|
| `STORY_IMAGE_DIR` | `/data/story_images/` | Per-story subdirectory: `<dir>/<story_id>/`. On Railway, MUST be backed by a Railway Volume (same approach as `GARMIN_SESSION_DIR`). On local development, any directory writable by the process. |
