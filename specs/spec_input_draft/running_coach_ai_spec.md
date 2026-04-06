# AI Running Coach — Application Specification

## Overview

An AI-powered personal running coach that monitors health and performance data from Garmin Connect, builds and maintains adaptive training plans, and communicates with athletes via Slack. Supports multiple independent athletes — each with their own Garmin account, training plan, conversation history, goals, and coach memory — all served by a single hosted instance.

The coach persona is a 30-year veteran of endurance sport, knowledgeable in modern training methodologies (polarized training, 80/20 rule, periodization, block training, HRV-based load management). Its primary directive is to help each athlete achieve their goal without excessive stress or injury risk.

Hosted on a Synology NAS using Docker Compose.

---

## Core Goals

- Ingest Garmin health and workout data daily
- Build a personalized marathon training plan based on athlete goals
- Adapt the plan dynamically based on performance, fatigue, health signals, weather, and schedule changes
- Communicate with the athlete naturally via Slack
- Upload structured workouts (easy runs, speed sessions, long runs) back to Garmin
- Provide post-run feedback and weekly progress summaries

---

## Tech Stack

| Layer     | Choice                                           | Reason                                        |
| --------- | ------------------------------------------------ | --------------------------------------------- |
| Language  | Python 3.11+                                     | Best library support for all integrations     |
| AI        | Claude API (claude-sonnet-4-6)                   | Coach intelligence, plan generation, feedback |
| Garmin    | `python-garminconnect` (unofficial consumer API) | No developer account needed                   |
| Slack     | Slack Bolt SDK (free workspace)                  | Bot + Events API for bidirectional chat       |
| Weather   | Open-Meteo API (free, no key required)           | Forecast by lat/lon                           |
| Scheduler | APScheduler                                      | Daily jobs, cron-style tasks                  |
| Database  | SQLite via SQLAlchemy                            | Lightweight, runs on Synology                 |
| Hosting   | Docker Compose on Synology NAS                   | Self-hosted, always-on                        |
| Config    | `.env` file + Pydantic Settings                  | Secrets management                            |

---

## Integrations

### Garmin Connect (via `python-garminconnect`)

- **Auth:** Each athlete's Garmin email/password stored encrypted in the `Athlete` DB row. SSO session token cached per athlete to `/data/garmin_sessions/{athlete_id}.json`, refreshed on expiry. No global Garmin credentials in env.
- **Write:** Upload structured workouts (`.fit` file format via `fit-tool` or `garmin-fit`) with target pace zones, intervals, and descriptions

#### Daily health reads

Sleep score, HRV status, resting HR, body battery (start/end), stress average, steps, SpO2 (if available).

#### Activity summary reads (per completed workout)

Distance, duration, average/max HR, average/max pace, elevation gain, training load, aerobic/anaerobic training effect, VO2 max estimate, calories, average/max cadence, average stride length, average ground contact time, average vertical oscillation, average vertical ratio, average power (if Garmin Running Power available).

#### Full time-series telemetry reads (per completed workout)

Fetched via the Garmin Connect activity details endpoint. Every data stream that the device recorded, sampled at the native device rate (typically 1-second intervals):

| Stream                  | Description                                       |
| ----------------------- | ------------------------------------------------- |
| `heart_rate`            | BPM over time                                     |
| `pace`                  | min/km over time (inverted from speed)            |
| `cadence`               | steps per minute over time                        |
| `stride_length`         | metres per stride over time                       |
| `ground_contact_time`   | milliseconds per step over time                   |
| `vertical_oscillation`  | centimetres of vertical bounce per stride         |
| `vertical_ratio`        | vertical oscillation / stride length (%)          |
| `power`                 | watts over time (Garmin Running Power)            |
| `elevation`             | metres above sea level over time                  |
| `air_temperature`       | °C over time (if recorded)                        |
| `respiration_rate`      | breaths per minute over time (if device supports) |
| `performance_condition` | real-time fitness estimate over time              |

All streams stored as JSON arrays in `WorkoutTelemetry`. Lap splits also stored separately — each lap's avg/max for every metric above.

Not all devices record all streams. Parser must gracefully skip missing streams and record which were available in `telemetry_channels_json`.

### Slack

- **Bot scopes:** `chat:write`, `im:history`, `im:write`, `app_mentions:read`
- **Events:** `message.im` (DMs to bot), `app_mention`
- **Slash commands (optional):** `/status`, `/plan`, `/skip-today`
- The bot operates primarily in a 1:1 DM channel with the athlete

### Access Control

- **Invite-only by default.** A comma-separated `ALLOWED_SLACK_USER_IDS` env var seeds the initial allowlist. Unknown Slack user IDs are declined: "This coaching service is private — ask the admin to add you."
- **Per-athlete `allowed` flag**: Each `Athlete` row has an `allowed` boolean field. Admin can grant/revoke access at runtime without redeploying.
- **Admin commands**: `ADMIN_SLACK_USER_ID` can DM the bot:
  - `!admin add <uid>` — create or re-enable `Athlete` row
  - `!admin remove <uid>` — set `athlete.allowed = False`
  - `!admin list` — list all athletes and onboarding status
  - `!admin resync-garmin [<uid>]` — re-upload all upcoming workouts
  - `!admin clean-garmin [<uid>]` — wipe Garmin library, clear IDs, re-sync fresh
- **Data isolation**: Every query, API call, and context assembly is scoped to `athlete_id`. No cross-athlete data leakage.

### Weather (Open-Meteo)

- Fetch 7-day forecast for athlete's home coordinates
- Factors considered: temperature, precipitation probability, wind speed, humidity
- Used to adjust session type, effort, or timing recommendations

### Claude API

- Model: `claude-sonnet-4-6`
- Role: Coach brain — plan generation, adaptation logic, Slack response drafting, feedback analysis
- System prompt encodes the veteran coach persona, training philosophy, and athlete profile
- Structured JSON outputs for plan mutations; natural language for Slack messages

---

## Application Architecture

```
running_coach_ai/
├── main.py                   # Entry point, starts scheduler + Slack bot
├── config.py                 # Pydantic settings from .env
├── database/
│   ├── models.py             # SQLAlchemy models
│   └── session.py            # DB session factory
├── garmin/
│   ├── client.py             # Garmin auth + data fetch (summary + full telemetry streams)
│   ├── parser.py             # Normalize raw Garmin responses into DB model fields
│   ├── telemetry.py          # Extract, align, and store time-series streams + lap splits
│   └── workout_builder.py    # Build .fit workout files for upload
├── coach/
│   ├── persona.py            # System prompt and coach personality
│   ├── planner.py            # Initial plan generation via Claude
│   ├── adapter.py            # Daily plan adaptation logic
│   ├── feedback.py           # Post-run analysis and recommendations
│   └── biomechanics.py       # Telemetry analysis: cadence, form, HR drift, decoupling; updates RunningProfile
├── weather/
│   └── client.py             # Open-Meteo fetch + summarize
├── slack/
│   ├── bot.py                # Slack Bolt app, event handlers
│   ├── conversation.py       # Multi-turn state, system prompt assembly, CoachMemory extraction
│   ├── onboarding.py         # New athlete intake flow (per-user)
│   └── admin.py              # Admin commands: add/remove allowed users
├── scheduler/
│   └── jobs.py               # APScheduler job definitions
├── docker-compose.yml
├── Dockerfile
└── .env.example
```

---

## Database Models

### `Athlete`

- `id`, `slack_user_id` (unique), `name`, `age`
- `slack_dm_channel_id` (cached to avoid opening DM repeatedly)
- `home_lat`, `home_lon` (for weather)
- `timezone` (athlete's local timezone for scheduling)
- `garmin_email`, `garmin_password_encrypted` (Fernet-encrypted at rest)
- `onboarding_complete` (bool)
- `onboarding_step` (int, tracks position in conversational flow for resume)
- `allowed` (bool, per-athlete access control flag)
- `created_at` (datetime)

### `Goal`

- `id`, `athlete_id`, `race_type` (e.g., `marathon`, `half_marathon`, `10k`, `5k`)
- `race_name` (optional athlete-friendly name)
- `race_date`, `target_time_seconds`
- `current_weekly_mileage_km`, `training_days_per_week` (collected during onboarding)
- `experience_level` (`beginner`, `intermediate`, `advanced`)
- `active` (bool, multiple goals possible per athlete)
- `created_at`

### `TrainingPlan`

- `id`, `athlete_id`, `goal_id`
- `generated_at`, `valid_from`, `valid_to`
- `plan_json` (full structured plan as JSON)
- `current_phase` (text: `base`, `build`, `peak`, `taper`)
- `active` (bool — only one active plan per athlete)

### `PlannedWorkout`

- `id`, `plan_id`, `athlete_id`, `scheduled_date`
- `workout_type` (`easy`, `tempo`, `intervals`, `long_run`, `strides`, `cross_train`, `rest`)
- `description` (human-readable instructions), `target_distance_km`, `target_pace_min_per_km`
- `garmin_workout_id` (Garmin workout definition ID, set after upload)
- `garmin_schedule_id` (Garmin calendar schedule ID, set after upload)
- `status` (`planned`, `modified`, `skipped`, `cancelled`)
- `modified_reason` (text explaining why coach adapted plan)

### `CompletedWorkout`

- `id`, `athlete_id`, `planned_workout_id` (nullable)
- `garmin_activity_id` (unique)
- `date`, `distance_km`, `duration_seconds`
- `avg_hr`, `max_hr`, `avg_pace_min_per_km`, `max_pace_min_per_km`
- `avg_cadence_spm`, `max_cadence_spm`
- `avg_stride_length_m`
- `avg_ground_contact_time_ms`
- `avg_vertical_oscillation_cm`
- `avg_vertical_ratio_pct`
- `avg_power_w`, `max_power_w`
- `elevation_gain_m`, `training_load`
- `aerobic_training_effect`, `anaerobic_training_effect`
- `vo2max_estimate`
- `feedback_given` (bool)
- `created_at` (timestamp)

### `WorkoutTelemetry`

Stores the full time-series data for a completed workout. One row per workout, not one row per data point (JSON columns for efficiency).

- `id`, `athlete_id`, `completed_workout_id`
- `sample_interval_seconds` (native device recording rate, typically 1)
- `heart_rate_json` (array of BPM values)
- `pace_json` (array of min/km values)
- `cadence_json` (array of SPM values)
- `stride_length_json` (array of metres)
- `ground_contact_time_json` (array of milliseconds)
- `vertical_oscillation_json` (array of centimetres)
- `vertical_ratio_json` (array of percentages)
- `power_json` (array of watts, null if not recorded)
- `elevation_json` (array of metres)
- `air_temperature_json` (array of °C values, null if not recorded)
- `respiration_rate_json` (array of breaths/min, null if not recorded)
- `performance_condition_json` (array, null if not recorded)
- `laps_json` (array of lap objects, each with avg/max for every available metric, lap distance, and lap duration)
- `recorded_at` (timestamp of fetch)

Not all devices record all streams. The parser gracefully skips missing streams.

### `RunningProfile`

A derived model updated after each workout. Captures the athlete's biomechanical fingerprint so the coach can track trends and give targeted advice. Recalculated by the analytics module after every new `WorkoutTelemetry` row.

- `id`, `athlete_id`, `updated_at`
- `typical_cadence_easy_spm` (average cadence on easy runs, 30-day rolling)
- `typical_cadence_hard_spm` (average cadence on tempo/interval runs)
- `typical_ground_contact_easy_ms`
- `typical_ground_contact_hard_ms`
- `typical_vertical_oscillation_cm`
- `typical_vertical_ratio_pct`
- `cadence_trend` (`improving`, `stable`, `declining` — requires ≥3 data points)
- `hr_drift_pct` (average cardiac drift % — (Q4 HR − Q1 HR) / Q1 HR across run quarters)
- `hr_pace_decoupling` (aerobic decoupling — (H2 HR:pace ratio − H1 ratio) / H1 ratio)
- `easy_hr_zone_compliance_pct` (% of easy run time in zone 2, HR ≤ 70% max HR)
- `notes_json` (coach-written observations, e.g. "cadence drops below 160 spm when fatigued")

### `HealthSnapshot`

- `id`, `athlete_id`, `date` (unique per athlete per day)
- `hrv_score`, `hrv_status` (e.g. "balanced", "unbalanced", "low")
- `resting_hr`, `sleep_score`, `sleep_duration_seconds`
- `body_battery_start`, `body_battery_end` (Garmin Body Battery range)
- `stress_avg`, `spo2_avg` (SpO₂ if recorded)
- `steps`

### `ConversationMessage`

- `id`, `athlete_id`, `slack_ts`
- `role` (`user` | `assistant`)
- `content`, `created_at`

### `CoachMemory`

Long-term facts the coach should always remember, persisted across the rolling conversation window and included in system prompt on every turn.

- `id`, `athlete_id`, `category` (`injury`, `preference`, `goal_note`, `personal`, `performance_flag`)
- `content` (text: e.g. "Athlete has a history of left Achilles tendinopathy", "Prefers not to run on Sundays")
- `created_at`, `active` (bool — coach can deactivate stale memories)
- `source` (where memory came from: `onboarding`, `conversation`)

Coach memory is appended to the system prompt on every conversation turn. When Claude's response contains a `<remember>` tag, the content is extracted and saved as a new `CoachMemory` row automatically. This lets the coach accumulate knowledge about the athlete that outlasts the 30-message rolling window.

---

## User Journey

### 1. First Run — Onboarding

1. Athlete installs the Slack app and sends any message to the bot DM.
2. Bot greets them as the coach and begins a guided intake conversation:
   - "What's your name and how old are you?"
   - "What's your target race? (distance, date)"
   - "What finish time are you aiming for?"
   - "How many km/miles are you running per week currently?"
   - "How many days per week can you train?"
   - "Any injuries or health conditions I should know about?"
   - "What city do you train in? (for weather)"
   - "Can you share your Garmin Connect login so I can read your data?" (handled securely via DM, stored encrypted)
3. Coach confirms understanding: "Great — here's what I know about you…" and asks for confirmation.
4. Garmin credentials are encrypted and stored; the Slack message containing them is deleted immediately.
5. Up to 1 year of historical Garmin activities is imported in the background — gives Claude fitness context before plan generation.
6. Coach generates a 16–20 week training plan using Claude, structured week by week (two-phase: Claude skeleton → Python expansion).
7. Coach sends a summary of week 1 to Slack.
8. Coach uploads week 1 workouts to Garmin Connect.
9. Athlete's daily morning check-in job is registered on the live scheduler — no restart needed.

### 2. Daily Morning Check-in (Automated)

_Runs every morning at 07:00 local time, once per onboarded athlete. Scheduler iterates all active athletes and runs each check-in independently._

1. Fetch last night's sleep, HRV, body battery, resting HR from that athlete's Garmin account.
2. Fetch 24h weather forecast for that athlete's home location.
3. Check today's planned workout for that athlete.
4. Coach (Claude) evaluates: is today's session appropriate given fatigue signals and weather?
5. If adjustment needed: modify workout or swap with rest, update `PlannedWorkout` record.
6. Send Slack DM to that athlete: "Good morning [Name]! Here's your session for today…" with adjusted plan if changed, weather note, and motivational cue in coach voice.

### 3. Post-Run Feedback (Automated)

_Polls Garmin every 30 minutes from 06:00–22:00 for each onboarded athlete independently._

1. New activity detected on an athlete's Garmin account → fetch summary metrics AND full time-series telemetry streams.
2. Store summary in `CompletedWorkout`, store all telemetry streams in `WorkoutTelemetry`.
3. `biomechanics.py` analyses the telemetry: cadence consistency, HR drift, aerobic decoupling, ground contact trends, vertical oscillation, effort distribution across laps, zone compliance. Updates `RunningProfile`.
4. Match to that athlete's `PlannedWorkout` for today.
5. Coach (Claude) receives: planned vs actual summary, key biomechanical findings from this run, and the athlete's `RunningProfile` trends. Generates feedback in coach voice — not just pace/HR but form, pacing strategy, and specific observations from the telemetry (e.g. "Your cadence dropped from 172 to 161 in the last 3km — that's a sign of fatigue setting in early").
6. Post to that athlete's Slack DM within 10 minutes of activity sync.
7. If a biomechanical pattern warrants a `CoachMemory` entry (e.g. consistent cadence drop under fatigue), coach includes a `<remember>` tag in its response to persist it.

### 4. Weekly Review (Automated)

_Runs every Sunday 20:00 UTC for each onboarded athlete independently._

1. Aggregate all completed workouts from that athlete's week (completion %, actual vs planned volume, quality sessions, training load, HRV/sleep averages).
2. Coach generates a weekly summary: one key positive, one focus for next week, preview of the week ahead.
3. Adapt the following week's plan based on completion rate:
   - < 70% completion → reduce next week's load 10–15%, swap one quality session to easy
   - 100% completion + good HRV/sleep → can increase easy distances up to 10%
   - Taper weeks: never increase load
4. Sync next week's workouts to that athlete's Garmin account.
5. Post weekly review DM to athlete.

### 5. Free-Form Coaching Conversation (Core Feature)

The bot is first and foremost a real coach you can talk to. Every DM to the bot is a conversation, not a command. There is no intent classification, no rigid menu of options. The athlete types naturally and the coach responds naturally — exactly as if texting a coach who knows you and your training history.

#### How it works

1. Message arrives via Slack Events API (DM or `@mention`).
2. System prompt is assembled fresh each time with:
   - Coach persona and philosophy
   - Current date (explicit day/week boundaries — critical for calendar context)
   - Full athlete profile (age, active goals with race dates and targets)
   - Current training phase (`Base`, `Build`, `Peak`, or `Taper`) and this week's sessions
   - Today's Garmin health snapshot (HRV, sleep, body battery, resting HR, stress, steps, SpO2) including 7-day HRV trend if available
   - Upcoming calendar — next 4 weeks of workouts with Garmin sync status (`✓Garmin` or `✗not on Garmin`)
   - Today's and next 3 days' weather forecast with coaching adjustment recommendation
   - Active coach memories (long-term facts about the athlete)
   - Last 30 conversation messages (rolling window; trimmed to 20 or 15 if prompt exceeds 150k/180k characters)
3. Claude generates a reply in coach voice — no template, no canned responses.
4. Reply posted to Slack. Message pair saved to `ConversationMessage`.
5. If the reply contains a plan mutation (detected via `<plan>` JSON tag), the plan update is applied to DB and Garmin silently. If it contains a `<garmin_sync/>` tag (explicit athlete request), all upcoming workouts are re-pushed to Garmin. If it contains a `<remember>` tag, the content is saved as a `CoachMemory` row. The athlete only sees the natural language response.

#### What the athlete can talk about freely

The coach handles any running or training topic without the athlete needing to phrase things a special way:

- **Logistics:** "Can we move my long run to Saturday this week?" / "I'm travelling Thursday and Friday"
- **Health & fatigue:** "My legs are really heavy today" / "I've had a headache since yesterday, should I still run?" / "I didn't sleep well"
- **Injury concerns:** "My left knee has been niggly on long runs" / "I've got some Achilles tightness" — coach asks clarifying questions, advises cautiously, flags if rest is needed
- **Race strategy:** "How should I pace my marathon?" / "Should I negative split?" / "What do I eat the night before?"
- **Training curiosity:** "Why are we doing so many easy runs?" / "What's the point of strides?" / "Why is my HR so high lately?"
- **Performance reflection:** "That run felt way harder than it should have" / "I crushed my tempo today, what does that mean?"
- **Mental / motivation:** "I'm dreading the long run this weekend" / "I feel like I'm not improving" / "Is my goal realistic?"
- **General running knowledge:** "What should my easy pace be?" / "How does periodization work?" / "What's the 10% rule?"
- **Goal changes:** "I'm thinking of changing my target time" / "I found a race two weeks earlier, can we adjust?"

#### Conversation qualities

- **Remembers context within a session** — no need to repeat yourself mid-conversation
- **Refers back to recent runs** — "After that tempo on Tuesday where you averaged 4:52/km, your body is probably still absorbing that load…"
- **Asks follow-up questions when needed** — if the athlete says "I don't feel great", the coach asks what's going on rather than assuming
- **Proactive but not pushy** — coach may note something it noticed ("Your HRV has been dropping this week — how are you feeling overall?") but doesn't flood the athlete
- **Gives opinions, not just options** — a real coach says "I'd skip the intervals today and do an easy 40 min instead" not "here are three options"
- **Admits uncertainty appropriately** — "I can't diagnose what's happening with your knee, but here's what I'd watch for and when to see a physio"

#### Automatic side effects of conversation

When the coach decides to change the plan during conversation, it does so without making a big deal of it:

- DB updated silently
- Garmin workout updated/deleted/re-uploaded in background
- Coach mentions the change naturally: "I've moved your intervals to Friday and made today an easy 50 minutes — Garmin is updated."

### 6. Plan Modification

1. Coach detects need to modify plan (via daily check, post-run, or athlete message).
2. Claude generates a modified `plan_json` diff.
3. DB updated: affected `PlannedWorkout` rows updated.
4. If Garmin workouts already uploaded: delete old → re-upload new.
5. Athlete notified via Slack with explanation.

---

## Coach Persona — System Prompt Summary

The Claude system prompt should establish:

- **Identity:** 30-year veteran running coach, worked with everyone from first-timers to Boston qualifiers. Evidence-based, warm but direct. Has a keen eye for running form and biomechanics — can spot a cadence problem or pacing error from raw numbers the way a track coach spots it in person.
- **Philosophy:** 80/20 polarized training (80% easy, 20% quality). Respects recovery. Believes fitness is built in weeks, not days. Treats running economy (form efficiency) as equally important as fitness.
- **Biomechanical awareness:** The coach reads telemetry and notices patterns. It knows that:
  - Cadence below 170 spm on easy runs often signals overstriding
  - Ground contact time increasing over a run indicates neuromuscular fatigue
  - High vertical oscillation relative to stride length (vertical ratio > 9%) wastes energy
  - HR drift > 5% on an easy run signals either too-high effort or inadequate aerobic base
  - Aerobic decoupling > 5% on long runs suggests the athlete went out too fast or needs more base mileage
  - The coach references these numbers by name in feedback — not as lectures, but as natural observations a good coach would make
- **Tone:** Encouraging, occasionally uses running vernacular, never condescending. Adjusts formality to match athlete's energy.
- **Constraints:** Never prescribe dangerous training loads. Flag injury warning signs. When uncertain, err on the side of rest.
- **Output format:** Three structured side-effect tags may appear in any response:
  - `<plan>{json}</plan>` — mutates `PlannedWorkout` rows and re-syncs to Garmin if previously uploaded. Schema: `{"sessions": [{"date": "YYYY-MM-DD", "status": "...", "workout_type": "...", ...}]}`. Distances in km, paces in min/km.
  - `<garmin_sync/>` — pushes next 4 weeks of future workouts to Garmin Connect. Only triggered by explicit athlete request ("sync my Garmin", "update my watch").
  - `<remember>text</remember>` — creates a `CoachMemory` row. Natural language outside these tags.

---

## Training Plan Logic

### Initial Plan Generation (Two-Phase)

**Phase 1 — Skeleton via Claude**: Obtain a compact week-by-week outline (phase, weekly volume, day types) in JSON array format.

Input:

- Athlete profile (age, current mileage, experience, training days/week)
- Goal (race type, target time, race date)
- Current fitness (from Garmin if available)

Output: JSON array with `{"w":1,"phase":"base","mi":25,"days":["easy","easy","tempo","easy","long_run"]}`

**Phase 2 — Expansion in Python**:

- Assign workouts to actual calendar dates
- Day-of-week slots by training days/week:
  - 3 days: Tue, Thu, Sun
  - 4 days: Mon, Wed, Fri, Sun
  - 5 days: Mon, Tue, Thu, Sat, Sun
  - 6 days: Mon–Thu, Sat, Sun
  - 7 days: all days
- Pacing: easy = race pace + 1.2 min/km; long run = race pace + 1.0 min/km
- Volume distribution: long run 30%, tempo 15%, intervals 13%, remainder → easy
- Tempo structure: broken (2 reps + rest) in base phase; continuous 20–45 min in build/peak; 20 min in taper
- Interval progression: base (400–800m repeats), build (800–1200m), peak (1000–1600m), taper (400m)
- Create `PlannedWorkout` DB rows

**Plan phases**:

- **Base** — easy volume building, introduce strides
- **Build** — add tempo, long runs, and interval work
- **Peak** — race-specific sessions, max long run 32–35 km
- **Taper** — 3 weeks out for marathon, 1–2 for shorter races

### Workout Types

| Type          | Description                                                                                          |
| ------------- | ---------------------------------------------------------------------------------------------------- |
| `easy`        | Conversational pace, HR zone 1–2, time-based prescription (e.g. "45 min easy")                       |
| `long_run`    | Easy pace, prescribed by time or distance with optional progressive effort last 20%                  |
| `tempo`       | Comfortably hard, ~marathon pace or faster, usually 4–8 km sustained                                 |
| `intervals`   | Track repeats in 200 m increments (400 m, 800 m, 1200 m, 1600 m) with clean rest (60 s, 90 s, 2 min) |
| `strides`     | 6–8 short accelerations (80–100 m each) after easy run, full recovery between                        |
| `rest`        | Full rest day or optional easy walk/yoga                                                             |
| `cross_train` | Bike, swim, strength — 45–60 min, no running impact load                                             |

---

## Garmin Workout Upload

- **Simple runs** (easy, long) uploaded as workout definitions with pace targets and descriptions
- **Structured sessions** (tempo, intervals, strides) built with step types: warmup (1), cooldown (2), interval (3), recovery (4), rest (5)
- **Pace encoding**: Garmin `pace.zone` targets use **speed in m/s as floats**. Formula: `speed_ms = 1000.0 / (pace_min_per_km * 60)`. ±5% window: `tv1 = speed_ms * 1.05` (faster bound), `tv2 = speed_ms * 0.95` (slower bound). Example: 5.905 min/km → 2.824 m/s → tv1=2.965, tv2=2.683.
- **Interval distances**: 200m increments only (200, 400, 600, 800, 1000, 1200, 1600, 2000m)
- **Sync scope**: Only workouts with `scheduled_date >= today` are uploaded. Past dates never written to Garmin.
- **Week batching**: Garmin syncs happen in weekly batches to avoid rate limit hits on bulk changes
- **Deletion**: `delete_workout` and `remove_workout_schedule` use `garmin.garth.request("DELETE", ...)` directly — the `garminconnect` library does not expose a delete method
- **Sync validation**: Before each coaching response, the next 7 days of workouts are validated against live Garmin; stale IDs are cleared on 404

---

## Weather Adjustment Rules

Weather data is sourced from Open-Meteo (WMO weather codes) and summarized as a daily coaching recommendation passed to Claude.

| Condition                             | Adjustment tag                                              |
| ------------------------------------- | ----------------------------------------------------------- |
| > 28°C                                | `reduce_pace_targets_15_20s_per_km`                        |
| Heavy rain + wind > 30 km/h           | `swap_quality_for_treadmill`                               |
| Snow, thunderstorm, hail              | `move_to_rest_or_indoor`                                   |
| Perfect conditions (10–18°C, calm)    | `optimal_conditions` — noted positively in morning message |
| Otherwise                             | `no_change`                                                |

---

## Environment Variables (`.env`)

```
ANTHROPIC_API_KEY=
SLACK_BOT_TOKEN=
SLACK_APP_TOKEN=              # For Socket Mode (avoids needing public URL)
SLACK_SIGNING_SECRET=         # Defined in config but not used — Socket Mode doesn't need it
DB_PATH=/data/coach.db
GARMIN_SESSION_DIR=/data/garmin_sessions/   # Per-athlete OAuth token cache
ENCRYPTION_KEY=               # Fernet key for encrypting Garmin passwords at rest
ALLOWED_SLACK_USER_IDS=       # Comma-separated Slack user IDs, e.g. U012AB3CD,U098ZY7WX
ADMIN_SLACK_USER_ID=          # Single admin user who can add/remove athletes at runtime
LOG_LEVEL=INFO
```

Note: Garmin credentials are collected per-athlete during onboarding and stored encrypted in the DB. There are no global Garmin credentials. Losing `ENCRYPTION_KEY` makes all stored Garmin passwords unreadable.

---

## Docker Compose (Synology)

```yaml
version: "3.9"
services:
  coach:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/data
    ports: [] # No inbound port needed — Slack Socket Mode
```

**Slack Socket Mode** is used so no public URL or reverse proxy is needed on the Synology — the bot connects outbound only.

---

## Non-Functional Requirements

- **Database schema management**: `main.py` calls `Base.metadata.create_all(engine)` at startup — idempotent, creates missing tables automatically. No migration files needed for new schemas; Alembic can be adopted for future complex migrations on existing deployments.
- **No public inbound ports** — Slack Socket Mode only
- **Garmin session persistence** — cache OAuth tokens (via `garth`) per athlete to `GARMIN_SESSION_DIR/{athlete_id}/`, refresh on expiry; all athletes handled independently
- **Full data isolation** — every DB query, Garmin call, Claude context, and Slack message is scoped to a specific `athlete_id`; no data bleeds between users
- **Graceful degradation** — if Garmin API is down, log and skip; do not crash
- **Conversation history** — keep last 30 messages per athlete in rolling context window; long-term facts persisted separately in `CoachMemory` and always included in system prompt
- **Secrets** — Garmin password stored encrypted at rest (Fernet symmetric encryption)
- **Logging** — structured logs to stdout, captured by Docker

---

## Implementation Notes

### Unit Conversions

**Internal storage**: All distances and paces stored in **km** and **min/km** for consistency with Garmin's metric backend.

**Display layer**: Helpers in `coach/persona.py` convert to **miles** and **min/mile** for athlete-facing output:

- `km_to_mi(km)` — 1 km = 0.621371 miles
- `format_miles(km)` — rounds <8 mi to nearest 0.5, ≥8 mi to nearest whole
- `format_pace_mi(min_per_km)` — converts to mm:ss/mi

**Plan JSON tags**: `<plan>` JSON blocks must use **km** and **min/km** (internal format). Claude is instructed to convert from athlete paces.

### Garmin Password Encryption

- Generated once per deployment: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- Stored in `ENCRYPTION_KEY` env var
- Athlete credentials encrypted with Fernet before DB storage, decrypted when authenticating to Garmin
- Loss of encryption key makes all stored passwords unreadable — athletes must re-enter on next auth attempt

## Out of Scope (v1)

- Web UI or mobile app
- Strava integration
- Nutrition tracking or fueling advice
- Paid Garmin developer API
- Multi-device synchronization
