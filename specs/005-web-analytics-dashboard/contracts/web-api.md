# Web API Contract

**Base URL**: `http://<host>:<WEB_PORT>`  
**Auth**: All `/api/*` routes require a valid session cookie (set on `POST /auth/login`). Unauthenticated requests return `401 {"error": "Unauthorized"}`.  
**Format**: JSON request/response bodies. Dates as `YYYY-MM-DD`. Timestamps as ISO 8601 UTC.

---

## Auth

### `POST /auth/login`

Authenticate and create a session.

**Request body**:
```json
{ "username": "sarah", "password": "hunter2" }
```

**Response 200** (session cookie set):
```json
{ "athlete_id": 1, "name": "Sarah Chen", "is_admin": false }
```

**Response 401**:
```json
{ "error": "Invalid credentials" }
```

---

### `POST /auth/logout`

Invalidate the current session.

**Response 200**:
```json
{ "ok": true }
```

---

## Dashboard

### `GET /api/dashboard`

Returns all data needed to render the Dashboard screen.

**Response 200**:
```json
{
  "athlete": {
    "name": "Sarah Chen",
    "coach_key": "alex",
    "week_number": 14,
    "total_weeks": 24,
    "race_name": "Western States 100",
    "race_date": "2026-06-28",
    "days_to_race": 71
  },
  "health": {
    "hrv_score": 71,
    "hrv_trend": 4,
    "body_battery": 78,
    "body_battery_trend": 6,
    "sleep_hours": 7.4,
    "sleep_trend": 0.4,
    "resting_hr": 44,
    "resting_hr_trend": -2
  },
  "today_workout": {
    "id": 42,
    "type": "long_run",
    "name": "Long Run",
    "target_distance_mi": 22.0,
    "target_pace_min_per_mi": "8:10",
    "target_zones": "Z2-Z3",
    "estimated_duration": "2:59",
    "elevation_gain_ft": 3200,
    "rpe_target": "6-7",
    "garmin_synced": true,
    "coach_notes": "First 14 miles conversational..."
  },
  "coach_message": "Morning, Sarah. HRV is up 4 points...",
  "week_strip": [
    {
      "day": "Mon",
      "type": "easy",
      "label": "10 mi Easy",
      "color": "#5a8a62",
      "done": true,
      "is_today": false
    }
  ],
  "charts": {
    "weekly_volume_8w": [42, 51, 48, 62, 58, 71, 65, 68],
    "hrv_8w": [62, 65, 68, 64, 66, 70, 69, 71],
    "week_labels": ["W7", "W8", "W9", "W10", "W11", "W12", "W13", "W14"]
  }
}
```

---

## Activity Feed

### `GET /api/activities?limit=20&offset=0`

Returns paginated recent completed workouts with feedback.

**Response 200**:
```json
{
  "summary": {
    "week_miles": 30.0,
    "ytd_miles": 892.0,
    "avg_pace_7d": "7:58",
    "training_load": 82
  },
  "activities": [
    {
      "id": 101,
      "date": "2026-04-18",
      "label": "Tempo Run",
      "distance_mi": 12.1,
      "duration": "1:25:47",
      "avg_pace": "7:05",
      "avg_hr": 162,
      "post_run_hrv": 68,
      "hr_zones": [8, 12, 31, 38, 11],
      "biomechanics": {
        "cadence_spm": 184,
        "ground_contact_ms": 225,
        "vertical_oscillation_cm": 8.2,
        "power_w": 287
      },
      "coach_analysis": "Strong LT work...",   // from completed_workouts.coach_analysis; omitted if null
      "flags": [
        { "text": "Heel contact at mile 9 (+0.3 cm)", "type": "warning" },
        { "text": "L/R balance 49/51 — excellent", "type": "ok" }
      ],
      "feedback": {
        "feel_score": 3,
        "rpe": 8,
        "notes": "Felt strong on miles 3-7",
        "saved": true
      }
    }
  ],
  "total": 47
}
```

### `POST /api/activities/{id}/feedback`

Save or update athlete run feedback.

**Request body**:
```json
{ "feel_score": 3, "rpe": 8, "notes": "Felt strong on miles 3-7" }
```

**Validation**: `feel_score` 0–4, `rpe` 1–10, `notes` ≤ 1000 chars.

**Response 200**:
```json
{ "ok": true }
```

**Response 422**:
```json
{ "error": "rpe must be between 1 and 10" }
```

---

## Training Plan

### `GET /api/plan?month=2026-04`

Returns calendar data for the specified month.

**Response 200**:
```json
{
  "month_label": "April 2026",
  "subtitle": "Week 14–17 of 24",
  "active_week_index": 1,
  "today_col": 3,
  "week_labels": ["Mar 30", "Apr 14 · This week", "Apr 21", "Apr 28"],
  "weeks": [
    [
      {
        "type": "easy",
        "label": "Easy Run",
        "distance_mi": 8.0,
        "duration": "1:07",
        "target_pace": "8:20",
        "hr_zone": "Z2",
        "tss": 48,
        "intensity": 1,
        "done": true,
        "actual_pace": "8:19",
        "actual_zones": [2, 63, 33, 2, 0]
      },
      null
    ]
  ]
}
```

### `POST /api/plan/sync`

Trigger Garmin re-sync for all upcoming workouts.

**Response 200**:
```json
{ "ok": true, "synced_count": 18 }
```

**Response 500**:
```json
{ "ok": false, "error": "Garmin session expired — re-auth needed" }
```

---

## Coach Chat

### `GET /api/chat/history`

Returns the full conversation history (Slack + web, unified).

**Response 200**:
```json
{
  "messages": [
    {
      "role": "assistant",
      "content": "Morning, Sarah. HRV is up 4 points...",
      "timestamp": "2026-04-18T09:14:00Z",
      "source": "slack"
    },
    {
      "role": "user",
      "content": "Legs still feel heavy from Tuesday.",
      "timestamp": "2026-04-18T09:22:00Z",
      "source": "web"
    }
  ]
}
```

### `POST /api/chat/message`

Send a message and receive the coach response.

**Request body**:
```json
{ "message": "How should I pace the long run today?", "coach_key": "alex" }
```

`coach_key` is optional. If provided, the backend uses it as the persona for this message only (ephemeral — does not persist to `athlete.coach_key`). Valid values: `"alex"`, `"maya"`, `"jordan"`. If omitted or invalid, falls back to `athlete.coach_key`.

**Response 200**:
```json
{
  "response": "Run by HR today, not pace. If you drift into Z3...",
  "timestamp": "2026-04-18T09:35:00Z"
}
```

**Response 503** (Claude unavailable):
```json
{ "error": "Coach is unavailable — try again in a moment." }
```

---

## Weekly Review

### `GET /api/review`

Returns the most recent weekly review summary.

**Response 200**:
```json
{
  "week_start": "2026-04-14",
  "metrics": {
    "total_miles": 30.0,
    "elevation_ft": 4210,
    "avg_hrv": 69.5,
    "avg_hrv_trend": 3,
    "total_tss": 284
  },
  "narrative": "Strong week, Sarah. Tuesday's tempo was a breakthrough...",
  "daily_volume": [10, 12, 8, 0, 0, 0, 0],
  "body_battery_8w": [72, 68, 74, 71, 76, 73, 79, 78],
  "next_week": [
    { "day": "Mon", "type": "rest", "label": "Rest (mandatory)" },
    { "day": "Tue", "type": "easy", "label": "8 mi Easy" }
  ]
}
```

**Response 404** (no review generated yet):
```json
{ "error": "No weekly review available yet. Next generation: Sunday 20:00." }
```

---

## Admin

All `/api/admin/*` routes additionally require `is_admin=True` on the session athlete. Returns `403 {"error": "Forbidden"}` otherwise.

### `GET /api/admin/events?category=&severity=&limit=50&offset=0`

Returns paginated event log.

**Query params**: `category` (auth|garmin|claude|scheduler|http|all), `severity` (info|warn|error|all), `limit` (max 200), `offset`.

**Response 200**:
```json
{
  "events": [
    {
      "id": 5001,
      "timestamp": "2026-04-18T09:35:12Z",
      "severity": "error",
      "category": "garmin",
      "message": "Garmin sync failed for athlete 1: session expired",
      "athlete_name": "Sarah Chen",
      "details": { "error_type": "GarminConnectAuthenticationError" }
    }
  ],
  "total": 1240
}
```

---

## Static assets

### `GET /`

Redirects to `/app` if authenticated, else `/login`.

### `GET /app`

Serves `app.html` (React SPA). Requires auth — redirects to `/login` if no session.

### `GET /login`

Serves `login.html` (plain HTML form, no React dependency).
