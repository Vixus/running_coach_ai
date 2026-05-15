# Contract: `GET /api/today`

**Spec**: [../spec.md](../spec.md) FR-001 through FR-032b
**Phase**: 1 (design)
**Status**: Authoritative — implementation must produce responses matching every shape below.

---

## Endpoint

```
GET /api/today
```

- **Auth**: Required (`@login_required`). The endpoint reads `session["athlete_id"]`.
- **Method**: `GET` only. No body. No query params in v1.
- **Content-Type**: `application/json`
- **Success status**: `200 OK` for all six states (NO_PLAN, PRE_RUN, COMPLETED, REST_DAY, RACE_DAY, OFF_PLAN).
- **Error status**:
  - `401 Unauthorized` — no session athlete_id (handled by `@login_required` decorator).
  - `404 Not Found` — athlete row not found in DB (defensive; should not happen in normal operation).
  - `500 Internal Server Error` — uncaught exception. Frontend treats as "render last cached payload with stale indicator" per FR-028b.
- **Latency budget**: p95 < 100ms (FR-004).

---

## Response envelope (common to all states)

```json
{
  "state":        "PRE_RUN" | "COMPLETED" | "REST_DAY" | "RACE_DAY" | "NO_PLAN" | "OFF_PLAN",
  "today_iso":    "2026-05-15",
  "today_pretty": "Friday, May 15, 2026",
  "athlete":      { "id": 1, "name": "Vixus" },
  "headline":     { ... per state below ... },
  "rationale":    { ... per state below ... },
  "modifiers":    { ... per state below ... },
  "cover_lines":  [ ... per state below ... ] | null,
  "actions":      { ... per state below ... }
}
```

- `today_iso` and `today_pretty` are computed from `athlete.timezone` (FR-002), not server UTC.
- `athlete.id` and `athlete.name` always present.

---

## State-specific shapes

### State: `PRE_RUN`

```json
{
  "state": "PRE_RUN",
  "today_iso": "2026-05-15",
  "today_pretty": "Friday, May 15, 2026",
  "athlete": { "id": 1, "name": "Vixus" },

  "headline": {
    "eyebrow":  "Tempo Tuesday",
    "ribbon":   "Tempo Tuesday",
    "title":    "6 MI @ 7:45",
    "subtitle": "3 mi warm-up · 3 mi tempo · cool-down"
  },

  "rationale": {
    "text":   "This is the threshold work that locks in your goal race pace before next week's volume jump — we don't want to soften it. HRV's up 2 and you slept solid, so you're green-lit; just run the tempo segment even and don't get greedy in mile 4.",
    "source": "morning_checkin" | "placeholder" | "rule_based",
    "coach":  "Coach Alex",
    "accent_color": "#b8ff4f"
  },

  "modifiers": {
    "on_watch": true,
    "is_bonus": false
  },

  "cover_lines": [
    { "label": "HRV ms",   "value": 62,    "drill_to": "morning", "is_stale": false },
    { "label": "Body Bat", "value": 78,    "drill_to": "morning", "is_stale": false },
    { "label": "Sleep h",  "value": 7.4,   "drill_to": "morning", "is_stale": false },
    { "label": "RHR",      "value": 48,    "drill_to": "morning", "is_stale": false }
  ],

  "actions": {
    "headline_chat_prompt":  "Tell me about today's workout.",
    "rationale_chat_prompt": "I have a question about today's plan.",
    "cta": null
  }
}
```

**Fallback values**:
- If today's HealthSnapshot is missing, `cover_lines[].value` may be `"—"` (string) and `is_stale=true`. Up to 3 days of fallback per FR-013.
- If `rationale.source == "placeholder"`, `rationale.text == "Coach is checking in soon — pull this up after 7am for today's call."`

### State: `COMPLETED`

```json
{
  "state": "COMPLETED",
  "today_iso": "2026-05-15",
  "today_pretty": "Friday, May 15, 2026",
  "athlete": { "id": 1, "name": "Vixus" },

  "headline": {
    "eyebrow":  "Tempo Tuesday",
    "ribbon":   "Completed",
    "title":    "6.1 mi @ 7:42",
    "subtitle": "Tempo Tuesday ✓"
  },

  "rationale": {
    "text":   "You held 7:42 average through the tempo blocks with HR sitting right where I wanted it — that's a clean threshold session. The mile-4 negative split tells me your aerobic ceiling is still climbing. Long run Saturday gets the same easy-effort discipline.",
    "source": "coach_analysis" | "rule_based",
    "coach":  "Coach Alex",
    "accent_color": "#b8ff4f"
  },

  "modifiers": {
    "on_watch": false,
    "is_bonus": false
  },

  "cover_lines": [
    { "label": "Distance",      "value": "6.1 mi",  "drill_to": "last_run", "is_stale": false },
    { "label": "Pace",          "value": "7:42",    "drill_to": "last_run", "is_stale": false },
    { "label": "Avg HR",        "value": 158,       "drill_to": "last_run", "is_stale": false },
    { "label": "Training Load", "value": 87,        "drill_to": "last_run", "is_stale": false }
  ],

  "actions": {
    "headline_chat_prompt":  "How did today's run go?",
    "rationale_chat_prompt": "I have a follow-up on this run.",
    "cta": null
  }
}
```

**Bonus-run variant**: When `modifiers.is_bonus == true`, `headline.ribbon = "Bonus Run — not on plan"` and `headline.eyebrow = "Bonus Run"`. Everything else identical.

### State: `REST_DAY`

```json
{
  "state": "REST_DAY",
  "today_iso": "2026-05-15",
  "today_pretty": "Friday, May 15, 2026",
  "athlete": { "id": 1, "name": "Vixus" },

  "headline": {
    "eyebrow":  "Rest Day",
    "ribbon":   "Rest Day",
    "title":    "Recovery is the workout",
    "subtitle": null
  },

  "rationale": {
    "text":   "You logged 28 miles last week and the long run on Saturday was a good one. Today's rest is the work — sleep, eat, walk if you want. HRV is sitting at 62, body battery 78; that means the system is absorbing the load, not fighting it. Tomorrow's intervals will need every bit of this recovery.",
    "source": "morning_checkin" | "placeholder" | "rule_based",
    "coach":  "Coach Alex",
    "accent_color": "#b8ff4f"
  },

  "modifiers": {
    "on_watch": false,
    "is_bonus": false
  },

  "cover_lines": [
    { "label": "HRV ms",   "value": 62,  "drill_to": "morning", "is_stale": false },
    { "label": "Body Bat", "value": 78,  "drill_to": "morning", "is_stale": false },
    { "label": "Sleep h",  "value": 7.4, "drill_to": "morning", "is_stale": false },
    { "label": "RHR",      "value": 48,  "drill_to": "morning", "is_stale": false }
  ],

  "actions": {
    "headline_chat_prompt":  "How should I make the most of today's recovery?",
    "rationale_chat_prompt": "I have a question about today's plan.",
    "cta": null
  }
}
```

### State: `RACE_DAY`

```json
{
  "state": "RACE_DAY",
  "today_iso": "2026-09-12",
  "today_pretty": "Saturday, September 12, 2026",
  "athlete": { "id": 1, "name": "Vixus" },

  "headline": {
    "eyebrow":  "Race Day",
    "ribbon":   "Race Day",
    "title":    "Berlin Marathon",
    "subtitle": "Start time: 09:15"
  },

  "rationale": {
    "text":   "Trust the work. Execute the plan, mile by mile. The fitness is real — go prove it.",
    "source": "persona_static",
    "coach":  "Coach Alex",
    "accent_color": "#b8ff4f"
  },

  "modifiers": {
    "on_watch": true,
    "is_bonus": false
  },

  "cover_lines": [
    { "label": "Distance",  "value": "26.2 mi", "drill_to": null, "is_stale": false },
    { "label": "Goal Pace", "value": "7:30",    "drill_to": null, "is_stale": false },
    { "label": "HR Cap",    "value": 168,       "drill_to": null, "is_stale": false },
    { "label": "Weather",   "value": "—",       "drill_to": null, "is_stale": false }
  ],

  "actions": {
    "headline_chat_prompt":  "Walk me through race execution.",
    "rationale_chat_prompt": "I have a question about today's plan.",
    "cta": null
  }
}
```

- `cover_lines[].drill_to` is `null` for all entries (read-only; no scroll-to-section).
- Weather sourcing is out-of-scope v1; the value is `"—"` placeholder per FR-015.

### State: `NO_PLAN`

```json
{
  "state": "NO_PLAN",
  "today_iso": "2026-05-15",
  "today_pretty": "Friday, May 15, 2026",
  "athlete": { "id": 1, "name": "Vixus" },

  "headline": {
    "eyebrow":  null,
    "ribbon":   null,
    "title":    "Ready to train for something?",
    "subtitle": null
  },

  "rationale": {
    "text":   "Ready to train for something? Pick a race and your coach will build a plan.",
    "source": "persona_static",
    "coach":  "Coach Alex",
    "accent_color": "#b8ff4f"
  },

  "modifiers": {
    "on_watch": false,
    "is_bonus": false
  },

  "cover_lines": null,

  "actions": {
    "headline_chat_prompt":  null,
    "rationale_chat_prompt": null,
    "cta": {
      "label":       "Pick a race",
      "chat_prompt": "I want to train for…"
    }
  }
}
```

- `cover_lines` is `null` (FR-012). Frontend hides the cover-lines region.
- `actions.cta` is the sole tap target. Frontend renders the button in `accent_color`.

### State: `OFF_PLAN`

```json
{
  "state": "OFF_PLAN",
  "today_iso": "2026-05-15",
  "today_pretty": "Friday, May 15, 2026",
  "athlete": { "id": 1, "name": "Vixus" },

  "headline": {
    "eyebrow":  null,
    "ribbon":   null,
    "title":    "Your plan needs attention",
    "subtitle": null
  },

  "rationale": {
    "text":   "Your plan doesn't have a workout scheduled for today. This usually means you're between training blocks or the plan needs a refresh — let's talk.",
    "source": "persona_static",
    "coach":  "Coach Alex",
    "accent_color": "#b8ff4f"
  },

  "modifiers": {
    "on_watch": false,
    "is_bonus": false
  },

  "cover_lines": null,

  "actions": {
    "headline_chat_prompt":  null,
    "rationale_chat_prompt": null,
    "cta": {
      "label":       "Review my plan",
      "chat_prompt": "I'm between training blocks."
    }
  }
}
```

---

## Side effect: `WebEvent` emission

When the resolved `state` for the requesting athlete differs from the most recent `today.state_transition` WebEvent's `extra.to_state` for that athlete (or no prior event exists), the endpoint MUST emit a new WebEvent row with the shape defined in data-model.md §3.

This MUST NOT block the HTTP response (FR-032b). Implementation choices:

1. After-request hook (Flask `@after_request`) — recommended; ensures response flush precedes DB insert.
2. Background thread with the DB session (acceptable if scoped properly).
3. Synchronous insert inside the handler — acceptable only if the INSERT is sub-10ms; the indexed `web_events.athlete_id + kind + created_at` lookup and INSERT typically fit this budget.

---

## Frontend-facing contract

The frontend (`magazine.js`) consumes this endpoint and provides the following observable behaviors per spec:

| Behavior | Spec ref | Contract requirement |
|---|---|---|
| Mobile-first 375px layout | FR-020 | Cover lines stack 2×2 on width < 768px; flatten to 4-column on width ≥ 768px |
| Initial fetch within 500ms of first paint | FR-028a | Cached payload renders synchronously; background fetch in `requestIdleCallback` or `setTimeout(0)` |
| 60s polling while tab visible | FR-026 | Use existing `visibilitychange` pattern from notification polling |
| Re-fetch on persona switch | FR-026 | Bind to the existing `POST /api/coach` success handler |
| Re-fetch on bell notification | FR-026 | Re-fetch when `morning_checkin` or `post_run_feedback` notifications arrive |
| Tap-outside chat dismissal | FR-025 | Add backdrop element + click handler + Escape keydown handler (research.md Decision 3) |
| Stale indicator on failure | FR-028b | Render `cover_lines[].value` as cached + small grey dot icon |
| Skeleton on first-load failure | FR-028c | Render eyebrow/headline/rationale/cover-line shaped placeholders |
| State-transition animation | FR-027 | Brief ribbon-swap slide on state change, no animation on data-only refresh |
| Keyboard tab order | FR-028 | Headline → rationale → cover-lines L-to-R; Enter activates |

---

## Versioning

This is v1. Future evolutions (e.g., live mid-run state, multi-workout days, weather widget) are introduced as additive response fields with documented defaults so the frontend can ignore unknown fields and continue to render.

The contract does NOT version the URL path (`/api/today` stays). Field additions are backward-compatible; field removals or shape changes would be a major version bump and would coexist via a new path like `/api/today/v2`.
