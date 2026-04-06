# Contract: Coach (Claude) Output Format

**Branch**: `001-ai-running-coach` | **Date**: 2026-03-31

Claude is the coach brain. Every response from Claude is parsed before delivery to the athlete. This contract defines what the system expects in Claude's output and how side-effects are applied.

---

## Response Structure

Claude's raw output may contain zero or more structured tags embedded in natural language. Tags are parsed using simple string extraction — they do not nest and are not XML-escaped.

```
[Natural language text visible to athlete]

<plan>
{ JSON plan mutation }
</plan>

[More natural language text]

<remember>
Fact to store in coach memory
</remember>
```

The natural language text (everything outside tags) is what gets posted to Slack.

---

## `<plan>` Tag — Plan Mutation

Signals that the coach is modifying the training plan. Applied silently after the Slack message is sent.

### Schema

```json
{
  "action": "modify_session | skip_session | reschedule_session | regenerate_week",
  "sessions": [
    {
      "date": "YYYY-MM-DD",
      "workout_type": "easy | long_run | tempo | intervals | strides | rest | cross_train",
      "description": "Coach description of the session",
      "target_distance_km": 8.0,
      "target_pace_min_per_km": 5.5,
      "target_zones_json": { ... },
      "status": "planned | skipped | modified",
      "reason": "Why this change was made"
    }
  ]
}
```

### Processing Rules

1. Extract all `<plan>...</plan>` blocks from the Claude response.
2. Parse JSON within each block. If invalid JSON: log error, skip silently (do not crash).
3. For each session in `sessions`:
   - Find the matching `PlannedWorkout` row by `(athlete_id, date)`.
   - Update fields: `workout_type`, `description`, `target_distance_km`, `target_pace_min_per_km`, `target_zones_json`, `status`, `modified_reason`.
   - If the session had a `garmin_workout_id`: delete old Garmin workout, re-upload revised version.
   - If no matching row: create a new `PlannedWorkout`.
4. Strip all `<plan>...</plan>` blocks from text before sending to Slack.

### `action` Values

| Action | Behaviour |
|--------|-----------|
| `modify_session` | Update one or more sessions with new parameters |
| `skip_session` | Set `status=skipped` on sessions; delete from Garmin calendar |
| `reschedule_session` | Move session to new date; update DB + Garmin |
| `regenerate_week` | Replace all remaining sessions in current week |

---

## `<remember>` Tag — Coach Memory

Signals a fact the coach wants to retain beyond the rolling conversation window.

### Schema

Plain text content only. No nested JSON required.

```
<remember>
Athlete has a history of left Achilles tendinopathy. Flares up on consecutive high-mileage days.
</remember>
```

### Processing Rules

1. Extract all `<remember>...</remember>` blocks.
2. For each block, create a new `CoachMemory` row:
   - `athlete_id`: from context
   - `content`: stripped text content of the block
   - `category`: infer from content keywords (injury → `injury`, prefers → `preference`, etc.); default `performance_flag`
   - `source`: `conversation`
   - `active`: True
3. Strip all `<remember>...</remember>` blocks from text before sending to Slack.
4. Duplicate detection: if a memory with identical `content` already exists for this athlete, skip insertion.

---

## System Prompt Assembly

Assembled fresh on every conversation turn. Order of sections:

```
1. Coach persona and philosophy (static, from persona.py)
2. Athlete profile block:
   - Name, age, goal(s), race date(s), target time(s), experience level
3. Current plan context:
   - Active plan phase
   - This week's sessions (date, type, distance, description)
4. Health snapshot (today):
   - HRV, sleep score, body battery, resting HR, stress
   - Note if data unavailable: "No Garmin data available for today"
5. Recent workout history (last 5 completed):
   - Date, type, distance, avg pace, avg HR, brief summary
6. Weather (today + 3 days):
   - Temperature, conditions, wind, precipitation probability
7. Active coach memories (all active=True rows):
   - Injected as bullet list under "Important facts about this athlete:"
8. Conversation history:
   - Last 30 messages in chronological order (role: user/assistant)
```

### Token Budget Management

If the assembled prompt approaches the model's context limit (estimated by character count heuristic):
1. Reduce conversation history window first (from 30 down to 20, then 15).
2. Reduce recent workout history (from 5 down to 3, then 2).
3. Never truncate: persona, athlete profile, current plan context, coach memories, or today's health snapshot.

---

## Garmin Workout Upload Contract

When a `PlannedWorkout` is created or modified, the system uploads it to Garmin Connect.

### Workout JSON sent to Garmin API

```json
{
  "workoutName": "{type} — {date} — {description_truncated_to_50_chars}",
  "sport": {
    "sportType": { "sportTypeKey": "running" }
  },
  "estimatedDurationInSecs": <estimated>,
  "workoutSegments": [
    {
      "segmentOrder": 1,
      "sport": { "sportType": { "sportTypeKey": "running" } },
      "workoutSteps": [ <steps from target_zones_json> ]
    }
  ]
}
```

For simple sessions (easy, long_run) with no `target_zones_json`: single step covering full distance with pace target.

For structured sessions (tempo, intervals) with `target_zones_json.steps`: generate one Garmin step per entry.

### Upload / Update / Delete Lifecycle

| Event | Action |
|-------|--------|
| New `PlannedWorkout` created | `upload_workout(json)` → `schedule_workout(id, date)` → store IDs |
| Session modified | `delete_workout(garmin_workout_id)` → `add_workout(new_json)` → `schedule_workout(id, date)` → update stored IDs |
| Session skipped/deleted | `delete_workout(garmin_workout_id)` → clear `garmin_workout_id` from row |
