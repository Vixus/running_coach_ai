# Research: AI Running Coach

**Branch**: `001-ai-running-coach` | **Date**: 2026-03-31

---

## 1. Garmin Connect Integration (`python-garminconnect`)

### Decision
Use `python-garminconnect` backed by `garth` for Garmin SSO auth. Cache sessions per athlete to a directory. Poll health and activity data on schedule.

### Authentication & Session Caching

`python-garminconnect` uses the `garth` library for Garmin SSO under the hood. Auth flow:

1. `Garmin(email, password)` → triggers SSO login (may handle MFA interactively on first run).
2. On success, session tokens (OAuth) are stored via `garth` to a directory.
3. On subsequent runs: `Garmin(tokenstore=path)` loads cached tokens; `garmin.login()` refreshes if expired.

**Per-athlete session caching strategy:**
- Store each athlete's session at `{GARMIN_SESSION_DIR}/{athlete_id}/` (garth token directory format).
- Save: `garmin.garth.dump(f"{GARMIN_SESSION_DIR}/{athlete_id}")` after first login.
- Load: `garmin = Garmin(); garmin.garth.load(f"{GARMIN_SESSION_DIR}/{athlete_id}"); garmin.login()` — skips SSO if token is valid, refreshes if expired (access token ~1h, refresh token ~90 days).
- On each data fetch: load from cache → attempt fetch → on auth error, re-authenticate with stored (decrypted) credentials → re-cache.

**Failure modes:**
- Session expiry: `garth` auto-refreshes access tokens using refresh token. If refresh token also expired (>90 days idle), full re-login with stored credentials is needed.
- MFA prompt: Only TOTP-style codes work. App-push MFA cannot be automated. Document in onboarding: athletes must use TOTP or disable MFA.
- API schema changes: `python-garminconnect` is community-maintained and has had breaking changes when Garmin updated their backend (notably 2023–2024). Pin version and monitor releases.

### Health Data Methods

| Data | Method | Key response fields |
|------|--------|-------------------|
| Sleep | `get_sleep_data(date)` | `dailySleepDTO.sleepScores.overall.value`, sleep stages |
| HRV | `get_hrv_data(date)` | `hrvSummary.lastNight`, `hrvSummary.status` |
| Body Battery | `get_body_battery(startdate, enddate)` | `charged` (start), `drained` (end) values |
| Resting HR | `get_rhr_day(date)` | `allDayHR.restingHeartRate` |
| Stress | `get_stress_data(date)` | `avgStressLevel` |
| Steps | `get_steps_data(date)` | daily step count |
| SpO2 | `get_spo2_data(date)` | pulse ox readings (device-dependent) |

### Activity Data Methods

| Data | Method | Notes |
|------|--------|-------|
| Activity list | `get_activities_by_date(start, end, activitytype)` | Returns summary array |
| Activity detail | `get_activity_details(activity_id)` | Full metric summary |
| Telemetry streams | `get_activity_details(activity_id)` with `maxpolyline` param | Returns `metricDescriptors` + `activityDetailMetrics` arrays — raw samples |
| Lap splits | `get_activity_splits(activity_id)` | Per-lap averages |
| VO2 Max | Included in activity summary | `vO2MaxValue` field |

**Telemetry extraction:** The `activityDetailMetrics` field in the details response is an array of per-sample objects, each with a `metrics` list. The `metricDescriptors` array defines the meaning of each index position. The system must build a lookup of `metricsKey → column_index` to extract each stream.

**Downsampling caveat:** `get_activity_details(activity_id, maxchartsize=2000)` may return downsampled data for long activities. For full-resolution 1-second telemetry, download the original FIT file: `download_activity(activity_id, dl_fmt=ActivityDownloadFormat.ORIGINAL)` and parse it with `fitparse`. The JSON telemetry approach is simpler and sufficient for most analyses; the FIT download is a fallback for edge cases.

### Rate Limiting

No official documentation. Community consensus:
- 30-minute polling intervals for activity detection are safe.
- Daily health reads (once per day per athlete) are well within limits.
- Fetching full telemetry for each activity (1–2 calls) adds modest load.
- Per-athlete session tokens are independent — simultaneous calls for different athletes use different sessions and appear as different users.
- If rate-limited (HTTP 429 or auth error spike): back off exponentially, retry up to 3×, skip if time-window passed (per FR-027 / clarification Q5).

---

## 2. Garmin Workout Upload

### Decision
Use Garmin Connect's **workout scheduling JSON API** (via `python-garminconnect`) rather than `.fit` file upload for workout creation. Use `.fit` file upload only as a fallback for complex interval structures if the JSON API proves insufficient.

### Rationale
- Garmin Connect has a REST workout API that `python-garminconnect` partially exposes.
- JSON-based workout definitions are far simpler to generate than binary `.fit` files.
- The `add_workout` and `schedule_workout` methods support step types (warmup, interval, recovery, cooldown, rest) with pace, HR, or power targets.
- `.fit` file creation via `fit-tool` is feasible but adds binary format complexity for no clear gain if the JSON API covers all session types.

### Workout JSON API Pattern

`python-garminconnect` exposes `upload_workout(workout_json)` which POSTs to the Garmin Connect workout endpoint. The workout JSON structure:

```json
{
  "sportType": { "sportTypeId": 1, "sportTypeKey": "running" },
  "workoutName": "Easy Run 8km",
  "workoutSegments": [{
    "segmentOrder": 1,
    "sportType": { "sportTypeId": 1, "sportTypeKey": "running" },
    "workoutSteps": [
      {
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": { "stepTypeId": 1, "stepTypeKey": "warmup" },
        "durationType": { "durationTypeKey": "time" },
        "durationValue": 600,
        "targetType": { "targetTypeKey": "pace.zone" },
        "targetValueOne": 334,
        "targetValueTwo": 400
      },
      {
        "type": "RepeatGroupDTO",
        "stepOrder": 2,
        "numberOfIterations": 6,
        "workoutSteps": [
          { "type": "ExecutableStepDTO", "stepOrder": 1, "stepType": { "stepTypeKey": "interval" }, ... },
          { "type": "ExecutableStepDTO", "stepOrder": 2, "stepType": { "stepTypeKey": "recovery" }, ... }
        ]
      },
      { "type": "ExecutableStepDTO", "stepOrder": 3, "stepType": { "stepTypeKey": "cooldown" }, ... }
    ]
  }]
}
```

**Pace encoding:** `targetValueOne` / `targetValueTwo` are in **seconds per metre** (e.g., 5:00/km = 300s/1000m = 0.300 s/m → value `334` means 0.334 s/m = 4:58/km). Lower value = faster pace. `RepeatGroupDTO` is the native way to express interval repeats — no step-index arithmetic required.

After creating with `upload_workout`, schedule it to the calendar with `schedule_workout(workout_id, date)`.

To remove a workout from the calendar: `delete_workout(workout_id)` or `remove_workout_schedule(schedule_id)`.

### Alternatives Considered
- **`.fit` file via `fit-tool`**: Viable — `fit-tool` can create structured `WorkoutStepMessage` entries with pace targets (encoded as cm/s speed). However, binary `.fit` uploads via the activity endpoint are treated as completed activities, not reusable structured workouts. The JSON workout API is the correct mechanism for planned workouts with visible steps on the device.
- **`garmin-fit` PyPI package**: Does not exist as a meaningful library. Ignore.

---

## 3. Open-Meteo Weather API

### Decision
Use Open-Meteo free tier with hourly forecast parameters. No API key. Fetch once per athlete per morning check-in.

### API Details

**Endpoint**: `https://api.open-meteo.com/v1/forecast`

**Key parameters**:
```
latitude=<lat>
longitude=<lon>
hourly=temperature_2m,precipitation_probability,windspeed_10m,weathercode,relativehumidity_2m
forecast_days=7
timezone=auto
```

**Response structure**:
```json
{
  "hourly": {
    "time": ["2026-03-31T00:00", ...],
    "temperature_2m": [12.3, ...],          // °C
    "precipitation_probability": [5, ...],   // %
    "windspeed_10m": [8.2, ...],            // km/h
    "weathercode": [0, ...],                // WMO code
    "relativehumidity_2m": [72, ...]        // %
  }
}
```

**WMO Weather Code Mapping** (relevant subsets):

| Code | Condition | Coach Adjustment |
|------|-----------|-----------------|
| 0 | Clear sky | None (note positively if 10–18°C) |
| 1–3 | Mainly clear / partly cloudy | None |
| 51–67 | Drizzle / rain | Light: no change. Heavy (61–67) + wind >30 km/h: swap quality session for treadmill |
| 71–77 | Snow / ice | Move to rest or indoor cross-train |
| 80–82 | Rain showers | As per heavy rain rules |
| 95–99 | Thunderstorm | Move to rest or indoor cross-train |

**Adjustment Logic** (from spec):
- `temperature_2m > 28`: reduce pace targets 15–20 s/km, shorten if needed
- `precipitation_probability > 70` AND `windspeed_10m > 30` AND quality session: swap to treadmill
- `weathercode in {71,73,75,77,95,96,99}`: move to rest/cross-train
- All good (10–18°C, `weathercode ≤ 3`, wind < 15 km/h): note positively

**Rate limits**: 10,000 requests/day free tier — far exceeds our usage.

---

## 4. Slack Bolt Python — Socket Mode

### Decision
Use `slack-bolt` with `SocketModeHandler` and `slack-sdk`. Run Bolt in a daemon thread alongside APScheduler in the main thread.

### Initialisation

```python
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# NOTE: Do NOT pass signing_secret in socket mode — Slack signs WebSocket
# frames internally. signing_secret is only needed for HTTP mode.
app = App(token=SLACK_BOT_TOKEN)
handler = SocketModeHandler(app, SLACK_APP_TOKEN)

# .start() blocks. Use .connect() for non-blocking startup alongside APScheduler.
handler.connect()  # non-blocking; opens WebSocket in daemon thread
```

### Event Handling

**Inbound DMs** — listen for `message` events filtered to `im` channel type:
```python
@app.event("message")
def handle_message(event, client, say):
    # Guard: ignore bot's own messages (echo loop prevention)
    if event.get("bot_id"):
        return
    # Guard: ignore edited/deleted message subtypes
    if event.get("subtype"):
        return
    if event.get("channel_type") == "im":
        user_id = event["user"]
        text = event["text"]
        # → access control check → route to onboarding or conversation
```

**App mentions** — `@app.event("app_mention")` — route same as DM.

**Slash commands** (optional `/status`, `/plan`, `/skip-today`) — `@app.command("/status")`.

### Proactive DMs (morning check-in, feedback, weekly review)

To send a message to a user without an incoming event:
1. Open (or retrieve) the DM channel: `result = client.conversations_open(users=[slack_user_id])` → `channel_id = result["channel"]["id"]`
2. Post: `client.chat_postMessage(channel=channel_id, text=message)`

Cache the `channel_id` per athlete in the DB (`Athlete.slack_dm_channel_id`) after first open — `conversations_open` is idempotent but avoid calling on every scheduled job.

### Concurrency

Slack Bolt handles each incoming event in a separate thread from a thread pool. This is safe for concurrent athlete messages. The `say()` helper is event-scoped and thread-safe.

APScheduler jobs also run in threads. All shared state goes through the DB (SQLAlchemy sessions are thread-local).

### Running Bolt + APScheduler Together

Canonical pattern: `handler.connect()` (non-blocking, opens WebSocket in daemon thread) + `BlockingScheduler.start()` in main thread:

```python
# main.py pattern
from apscheduler.schedulers.blocking import BlockingScheduler

handler.connect()   # non-blocking; reconnects automatically on WebSocket drop

scheduler = BlockingScheduler()
# ... add jobs ...
scheduler.start()   # blocks main thread; keeps process alive
```

APScheduler jobs access Slack via `app.client` (thread-safe `WebClient`). Wrap all job functions in `try/except` and log — APScheduler silently swallows uncaught exceptions by default.

---

## 5. Claude API — Coach Intelligence

### Decision
Use the Anthropic Python SDK synchronously per-request. Assemble a rich system prompt per conversation turn. Use structured XML tags (`<plan>`, `<remember>`) for side-effect extraction.

### Rationale
- Synchronous calls are simpler and sufficient — the <10s latency target is achievable with `claude-sonnet-4-6` for typical prompt sizes.
- Streaming is not required for Slack (Slack shows a typing indicator via `chat_postMessage` which can be sent first, then the full reply).
- Structured output via XML tags in the response is more reliable than JSON mode for mixed natural-language + structured output.

### Prompt Assembly Pattern

Per conversation turn:
```
SYSTEM:
  [Coach persona + philosophy — static]
  [Athlete profile: name, age, goal, race date, target time, experience]
  [Current plan phase + this week's sessions]
  [Last 30 conversation messages (rolling window)]
  [Today's health snapshot: HRV, sleep, body battery, RHR]
  [Last 5 completed workouts with key metrics]
  [Weather: today + 3 days]
  [Active coach memories (all, injected verbatim)]

USER: [athlete message]
```

### Tag Extraction

- `<plan>{...}</plan>` — JSON plan mutation; apply to DB + Garmin in background after response is sent.
- `<remember>{...}</remember>` — Save as new `CoachMemory` row for this athlete.
- Natural language outside tags → post directly to Slack.

### Model Selection
`claude-sonnet-4-6` as specified. Token budget management: rolling 30-message window + last 5 workouts keeps context bounded. If prompt approaches token limits, truncate oldest messages first (not memories or profile).

---

## 6. APScheduler — Job Architecture

### Decision
Use `BackgroundScheduler` with `ThreadPoolExecutor`. Define three recurring jobs per-system (not per-athlete); each job iterates all onboarded athletes independently.

### Job Definitions

| Job | Schedule | Action |
|-----|----------|--------|
| `morning_checkin` | Daily 07:00 (athlete's local timezone) | Fetch health + weather → adapt → DM athlete |
| `activity_poll` | Every 30 min, 06:00–22:00 | Detect new activities → fetch telemetry → feedback |
| `weekly_review` | Sunday 20:00 (system timezone) | Aggregate week → summary → adapt next week → DM |

**Timezone handling**: Athletes' timezones are stored on the `Athlete` model. The morning check-in job runs globally at frequent intervals and checks per-athlete whether 07:00 has arrived in their local timezone. Alternatively, register one job per athlete at their local 07:00 (APScheduler supports timezone-aware cron triggers).

**Recommended approach**: Register per-athlete morning jobs at their local time using APScheduler's `CronTrigger` with `timezone=athlete.timezone`. Activity polling and weekly review run globally.

### Retry / Skip Logic
APScheduler's `misfire_grace_time` parameter determines how late a job can still run. For morning check-ins: set `misfire_grace_time=300` (5 minutes). For activity polling: `misfire_grace_time=60`. For weekly review: `misfire_grace_time=3600`.

---

## 7. SQLite + SQLAlchemy — Concurrency & JSON Columns

### Decision
Use SQLAlchemy 2.x with `JSON` column type (SQLite stores as text). Use `scoped_session` or per-request sessions for thread safety.

### JSON Columns
SQLAlchemy's `JSON` type works with SQLite — values serialised/deserialised automatically. Suitable for `plan_json`, `target_zones_json`, `laps_json`, `heart_rate_json`, etc.

### Thread Safety
Each thread (Bolt event handler, scheduler job) must use its own `Session`. Use `sessionmaker` factory and call `session = Session()` per operation. Commit and close in a `try/finally` block or use context manager.

### Migrations
Use Alembic for schema migrations. Initial migration creates all tables; future changes add columns or tables without recreating.

---

## 8. Fernet Encryption — Garmin Credential Storage

### Decision
Use `cryptography.fernet.Fernet` for symmetric encryption of Garmin passwords at rest in the DB. Key stored in `ENCRYPTION_KEY` env var.

### Key Management
- Generate key once: `Fernet.generate_key()` → store in `.env` as `ENCRYPTION_KEY`.
- Never commit `.env` to source control.
- On athlete creation: `encrypted = Fernet(key).encrypt(password.encode())` → store bytes (base64).
- On fetch: `Fernet(key).decrypt(encrypted).decode()`.

### Limitations
- Single symmetric key for all athletes. If key is compromised, all credentials must be rotated.
- Key rotation requires decrypting + re-encrypting all stored credentials. Acceptable for personal-scale deployment.

---

## Unresolved Items (Deferred to Implementation)

- **Garmin MFA**: Athletes with MFA on Garmin Connect must disable it or the first-time auth cannot be automated. This is a known limitation of the unofficial API. Document in onboarding flow.
- **Garmin workout JSON API exact field names**: Final field names for `workoutSteps` pace targets to be validated against live Garmin Connect API during implementation.
- **Athlete timezone storage**: `Athlete` model needs a `timezone` field (IANA timezone string, e.g. `"Europe/London"`). Collected during onboarding ("What city do you train in?" → infer or ask explicitly).
- **`slack_dm_channel_id` caching**: Add `slack_dm_channel_id` field to `Athlete` model to avoid repeated `conversations_open` calls.
