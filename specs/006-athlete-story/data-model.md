# Data Model: Athlete Story

## New Tables (one Alembic migration)

### `athlete_stories`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | |
| `athlete_id` | Integer | FK → `athletes.id`, NOT NULL | Always scoped |
| `milestone_type` | Text | NOT NULL | `"race_complete"` or `"block_complete"` |
| `title` | Text | NOT NULL | e.g. `"Simon · Brooklyn Half 2026"` |
| `editorial_body` | Text | nullable | Claude-generated, 400–600 words |
| `cover_image_path` | Text | nullable | Absolute path to first image on disk |
| `created_at` | DateTime | NOT NULL, default `utcnow` | |
| `published_at` | DateTime | nullable | Set on first "Share" click |
| `share_token` | Text | UNIQUE, NOT NULL | 12-char URL-safe random string |

### `story_questions`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | |
| `athlete_id` | Integer | FK → `athletes.id`, NOT NULL | Denormalised for easy scoped queries |
| `story_id` | Integer | FK → `athlete_stories.id`, nullable | Set when generation links Q&A to a story |
| `question` | Text | NOT NULL | From `INTERVIEW_QUESTIONS` list |
| `answer` | Text | nullable | Athlete's chat reply |
| `asked_at` | DateTime | NOT NULL, default `utcnow` | |
| `answered_at` | DateTime | nullable | Set when answer captured |

### `story_images`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | |
| `story_id` | Integer | FK → `athlete_stories.id`, NOT NULL | |
| `filename` | Text | NOT NULL | Stored filename on disk (UUID-based) |
| `caption` | Text | nullable | Athlete-supplied caption |
| `sort_order` | Integer | NOT NULL, default 0 | Lower = earlier in grid |
| `uploaded_at` | DateTime | NOT NULL, default `utcnow` | |

## New Config

| Setting | Default | Notes |
|---|---|---|
| `STORY_IMAGE_DIR` | `/data/story_images/` | Per-story subdirectory: `<dir>/<story_id>/` |

## Athlete Model Changes

No changes to `Athlete` — all story data is in the new tables.

## Relationships

```
Athlete ──< AthleteStory ──< StoryImage
                        ──< StoryQuestion (via story_id, nullable during interview)
Athlete ──< StoryQuestion (direct, for pre-milestone questions)
```

## Index Recommendations

- `story_questions`: index on `(athlete_id, answered_at)` — for scheduler "has unanswered question?" check
- `athlete_stories`: index on `(athlete_id, created_at DESC)` — for "most recent story" query
- `athlete_stories`: unique index on `share_token`
