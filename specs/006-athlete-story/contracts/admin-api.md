# Admin-Facing API Contract

All endpoints extend the existing `running_coach_ai/web/api/admin.py` blueprint. All require `@admin_required` (login + `is_admin=True`).

---

## `POST /api/admin/athletes/<athlete_id>/start-interview`

Manually open a `StoryInterviewSession` for an athlete with a chosen trigger kind. Bypasses real-trigger detection AND the `story_intro_seen_at` requirement so QA can test sessions for any athlete (FR-S037).

**Query string**: `?trigger=<kind>` where `<kind>` ∈ {`pr_set`, `race_upcoming`, `race_complete`, `pace_recalibration`, `difficult_week`}.

**Request body**: `{}` (optional `trigger_context_json` override)

**Response 200**:
```json
{
  "session_id": 42,
  "first_question": {
    "question_id": 100,
    "question": "I'd like to capture something for your story — can I ask a few questions about your race?",
    "options": ["Yes, go ahead", "Maybe later", "Skip"]
  }
}
```

**Response 400 cases**:
- `{"error": "Unknown trigger kind: <kind>", "valid": [...]}`
- `{"error": "Athlete has an active session (id=N); close it before starting another"}`

**Response 404**: athlete not found.

**Side effects**: writes a new `StoryInterviewSession` row, generates the consent prompt as the first question, posts it to the athlete's chat thread.

---

## `POST /api/admin/athletes/<athlete_id>/generate-story`

Trigger story generation for an athlete without a real milestone event (FR-S016/S017).

**Query string**: `?milestone=<type>` where `<type>` ∈ {`race_complete`}. (Reserved for future expansion — v1 accepts only `race_complete`.)

**Request body**: `{}`

**Response 200**:
```json
{
  "story_id": 7,
  "preview_url": "/story/abc123XYZ?preview=1"
}
```

Generation is synchronous to keep admin tooling simple (target ≤30 s per SC-S001 + a 60 s timeout). Uses **all** the athlete's most recent answered `StoryQuestion` rows (whether or not they were tied to a real milestone).

**Response 400**: `{"error": "Unknown milestone type: <type>", "valid": ["race_complete"]}`

**Response 404**: athlete not found.

**Response 504**: Claude call timed out (60 s). The story row is still created in a `generation_failed` state with a `story_failed` notification written to the athlete's inbox.

---

## `POST /api/admin/stories/<story_id>/render`

Re-render an existing story in any registered template (FR-S038). Admin override; does not lock against future auto-selection.

**Query string**: `?template=<key>`

**Request body**: `{}`

**Response 200**:
```json
{
  "ok": true,
  "story_id": 7,
  "template_key": "outside",
  "preview_url": "/story/abc123XYZ?preview=1"
}
```

**Response 400**: `{"error": "Unknown template key: <key>", "available": ["vogue", "runners_world", "times_long_read", "outside", "gq_profile"]}`

**Response 404**: story not found.

**Side effects**: `AthleteStory.template_key = <key>`, `template_locked_by_athlete = False` (admin override is not the athlete's choice). No Claude call.

---

## `DELETE /api/admin/stories/<story_id>?hard=1`

Hard-delete a story (FR-S024). Removes the `AthleteStory` row, every `StoryImage` row + its file under `STORY_IMAGE_DIR/<story_id>/`, and any `StoryInterviewSession` rows whose `story_id` matches. Without `?hard=1` the endpoint returns 400 to prevent accidental destruction.

**Response 200**:
```json
{
  "ok": true,
  "story_id": 7,
  "deleted": {
    "story_rows": 1,
    "image_rows": 3,
    "image_files": 3,
    "session_rows": 1,
    "question_rows": 8
  }
}
```

**Response 400**: `{"error": "Hard-delete requires ?hard=1 query parameter"}` if the flag is missing.

**Response 404**: story not found.

---

## `GET /api/admin/stories/templates`

List the registered magazine templates. Used by the admin panel's template-render dropdown.

**Response 200**:
```json
{
  "templates": [
    {
      "key": "vogue",
      "display_name": "Editorial Profile",
      "voice_description": "Glossy, aspirational, present-tense scene-setting...",
      "trigger_affinities": ["race_complete", "pr_set"],
      "thumbnail": "/static/story_templates/vogue/thumbnail.jpg"
    },
    ...
  ]
}
```
