# Athlete-Facing API Contract

All endpoints in `running_coach_ai/web/api/stories.py`. All require login (`@login_required`). All queries scoped to `session["athlete_id"]` per Constitution I.

---

## Opt-in & intro modal

### `POST /api/stories/opt-in`

Toggle the athlete's story-feature opt-in flag.

**Request body**: `{"opted_in": true | false}`

**Response 200**: `{"ok": true, "story_opt_in": <bool>}`

**Side effects**:
- If `opted_in=true` and `story_intro_seen_at IS NULL`, the next `/api/magazine` fetch will return a flag instructing the front-end to show the intro modal before any session can fire.
- If `opted_in=false`, any active `StoryInterviewSession` for this athlete is closed (`completed_at = now()`, `skipped = True`).

---

### `POST /api/stories/intro-acknowledged`

Marks the multi-page intro modal as seen. Called from the modal's final "I'm ready" button.

**Request body**: `{}`

**Response 200**: `{"ok": true, "story_intro_seen_at": "<ISO timestamp>"}`

**Side effects**: sets `Athlete.story_intro_seen_at = now()`. Subsequent eligible triggers may fire interview sessions for this athlete.

---

## Story collection — list, current, single

### `GET /api/stories`

List athlete's stories (excluding soft-deleted), most recent first.

**Response 200**:
```json
{
  "stories": [
    {
      "id": 1,
      "title": "Simon · Brooklyn Half 2026",
      "milestone_type": "race_complete",
      "template_key": "vogue",
      "template_locked_by_athlete": true,
      "created_at": "2026-05-07T15:32:18Z",
      "published_at": "2026-05-07T16:02:11Z",
      "preview_url": "/story/abc123XYZ?preview=1",
      "share_url": "/story/abc123XYZ",
      "regeneration_count": 1,
      "image_count": 3
    }
  ]
}
```

---

### `GET /api/stories/current`

Return the most recent in-progress (draft, unpublished, or published) story for the magazine "My Story" section. Returns `{"story": null}` if none exist.

**Response 200** (with story):
```json
{
  "story": {
    "id": 1,
    "title": "Simon · Brooklyn Half 2026",
    "milestone_type": "race_complete",
    "editorial_body": "...",
    "template_key": "vogue",
    "template_locked_by_athlete": false,
    "available_templates": [
      {"key": "vogue", "display_name": "Editorial Profile", "thumbnail": "/static/story_templates/vogue/thumbnail.jpg"},
      {"key": "runners_world", "display_name": "The Build-Up", "thumbnail": "..."},
      {"key": "times_long_read", "display_name": "The Long Read", "thumbnail": "..."},
      {"key": "outside", "display_name": "The Field Notes", "thumbnail": "..."},
      {"key": "gq_profile", "display_name": "The Profile", "thumbnail": "..."}
    ],
    "images": [
      {"id": 11, "url": "/api/stories/images/11/raw", "caption": "Mile 12, Prospect Park", "sort_order": 0}
    ],
    "share_token": "abc123XYZ",
    "preview_url": "/story/abc123XYZ?preview=1",
    "share_url": "/story/abc123XYZ",
    "published_at": null,
    "created_at": "2026-05-07T15:32:18Z",
    "regeneration_count": 0,
    "can_regenerate_at": "2026-05-07T15:32:18Z"
  }
}
```

`can_regenerate_at` is `last_regenerated_at + 1h` per FR-S021; the front-end uses it to enable/disable the regenerate button.

---

### `POST /api/stories/<story_id>/template`

Swap the active magazine template for a story (athlete-initiated, no Claude call).

**Request body**: `{"template_key": "outside"}`

**Response 200**: `{"ok": true, "preview_url": "/story/<token>?preview=1", "template_key": "outside"}`

**Response 400**: `{"error": "Unknown template key: <key>", "available": ["vogue", "runners_world", ...]}` if the key isn't in the registry.

**Side effects**: `template_key` updated, `template_locked_by_athlete = true`.

---

### `POST /api/stories/<story_id>/regenerate`

Regenerate the editorial body via Claude. Athlete-initiated; subject to a 1 h cooldown.

**Request body**: `{}`

**Response 200**: `{"ok": true, "story_id": <id>}` — generation completes synchronously (target SC-S001 30 s).

**Response 429**: `{"error": "Regeneration cooldown active", "retry_after": <seconds_remaining>}` if `last_regenerated_at + 1h > now()`.

**Side effects**: `editorial_body` overwritten; `regeneration_count += 1`; `last_regenerated_at = now()`. `share_token`, `created_at`, `published_at` preserved (FR-S021).

---

### `POST /api/stories/<story_id>/publish`

Set `published_at = now()` so the public URL becomes accessible.

**Response 200**: `{"ok": true, "share_url": "/story/<token>", "published_at": "<ISO>"}`

---

### `POST /api/stories/<story_id>/unpublish`

Set `published_at = NULL`. The draft and `share_token` are retained (FR-S022).

**Response 200**: `{"ok": true, "story_id": <id>}`

---

### `DELETE /api/stories/<story_id>`

Soft-delete: sets `deleted_at = now()`. Story is removed from `/api/stories`, the "My Story" section, and the public route (FR-S023). Image files are NOT removed from disk (admin hard-delete handles that).

**Response 200**: `{"ok": true, "story_id": <id>}`

---

## Image upload / management

### `POST /api/stories/current/images`

Upload an image to the current in-progress story. Multipart form upload.

**Form fields**: `file` (required), `caption` (optional)

**Response 201**:
```json
{
  "image": {
    "id": 11,
    "url": "/api/stories/images/11/raw",
    "caption": "Mile 12, Prospect Park",
    "sort_order": 0
  }
}
```

**Response 400 cases**:
- `{"error": "No story in progress"}` — no current story
- `{"error": "Maximum 5 photos per story"}` — FR-S013 cap reached
- `{"error": "File exceeds 10 MB"}` — FR-S019
- `{"error": "Unsupported file type"}` — not jpg/png/webp
- `{"error": "Could not decode image"}` — Pillow rejects file

---

### `GET /api/stories/images/<image_id>/raw`

Serve the raw image file. Athlete-only and owner-scoped (returns 404 if not owned).

**Response 200**: image bytes with appropriate `Content-Type`.

---

### `PATCH /api/stories/images/<image_id>`

Update caption or sort_order.

**Request body**: `{"caption": "...", "sort_order": 2}` (either field optional)

**Response 200**: `{"ok": true, "image": {...}}`

---

### `DELETE /api/stories/images/<image_id>`

Remove an image. Deletes the file on disk and the row.

**Response 200**: `{"ok": true}`

---

## Interview session interactions

### `POST /api/stories/sessions/<session_id>/respond`

Submit an answer to the most recent unanswered question in a session. Used by the front-end when the athlete clicks a multiple-choice option button. (Free-text answers come through the existing chat route per R-008 in research.md.)

**Request body**: `{"question_id": <id>, "option": "<chosen option string>"}`

**Response 200**:
```json
{
  "ok": true,
  "session_complete": false,
  "next_question": {
    "question_id": 14,
    "question": "What was going through your head at mile 20?",
    "options": ["I was just trying to survive", "I felt strong", "I was thinking about the finish", "Something else"]
  }
}
```

If `session_complete=true`, `next_question` is `null` and the session's `completed_at` is set.

**Response 400**: `{"error": "Question not unanswered or not in this session"}`.

---

### `POST /api/stories/sessions/<session_id>/decline`

Athlete clicks "Skip" on the consent prompt. Session closes immediately with `skipped=True, completed_at=now()`.

**Response 200**: `{"ok": true}`

---

## Magazine bundle additions

### `GET /api/magazine` — extended response

The existing magazine endpoint adds these fields when `Athlete.story_opt_in=True`:

```json
{
  "story": {
    "feature_enabled": true,
    "needs_intro_modal": true,        // true when opt_in=true and intro_seen_at is null
    "current_story": { ... }          // same shape as /api/stories/current.story or null
  }
}
```

When `story_opt_in=False`, the response includes `{"story": {"feature_enabled": false}}`.
