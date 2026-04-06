# What I Learned: AI Running Coach

**Feature**: Self-hosted, multi-athlete AI running coach powered by Claude, Garmin Connect, Slack, and APScheduler.
**Generated**: 2026-04-04
**Scope**: Full feature — Phases 1–9 (T001–T052) + Reconciliation (T053–T075) + Iteration (T076–T080)
**Implementation status**: 80/80 tasks completed

---

## Key Decisions

### 1. Interval Polling Instead of a One-Shot Morning Cron

**What we did**: Replaced `CronTrigger(hour=7)` with `IntervalTrigger(minutes=30, start_date=_next_checkin_start(tz))` per athlete, plus a health-data gate that returns early without sending if Garmin hasn't posted sleep/HRV/body battery yet.

**Why**: Garmin finalises overnight sleep and HRV analysis between 8–9 am, not at midnight. A 7 am fire always arrived before the data existed, producing N/A health scores in every morning message. The root problem wasn't bad data handling — it was firing at the wrong time. Polling lets the job keep trying until the data lands, using the same job function and zero extra infrastructure.

**Alternatives considered**:
| Approach | Why it wasn't chosen |
|----------|---------------------|
| Delay cron to 9 am | Fixes the average case but misses athletes whose data arrives at 9:30 am; those athletes silently get no message |
| Fetch yesterday's stored snapshot | Already existed as a fallback, but defeats the purpose of a _daily_ health-aware check-in |
| Separate "health-ready" webhook from Garmin | Garmin Connect has no push notification for data availability; would require polling anyway |

**When you'd choose differently**: If the external API had a reliable webhook or guaranteed SLA (e.g., "data available by 08:00"), a single cron is simpler. Polling belongs here because Garmin's data pipeline is non-deterministic.

---

### 2. XML Side-Effect Tags as the Claude Interface

**What we did**: Claude's response text is post-processed for three XML tags before the message is shown to the athlete: `<plan>{json}</plan>` mutates `PlannedWorkout` rows and re-syncs Garmin; `<remember>text</remember>` persists a `CoachMemory` row; `<garmin_sync/>` triggers a full 4-week Garmin push.

**Why**: Claude is generating two things simultaneously — natural-language prose for the athlete and structured mutations for the system. Encoding mutations in the prose as XML means a single API call handles both, with no separate action-calling step. The tags are stripped before the athlete sees the message, so the interface stays conversational.

**Alternatives considered**:
| Approach | Why it wasn't chosen |
|----------|---------------------|
| Anthropic tool_use (function calling) | Requires a two-turn loop (tool invocation + tool result), doubling API latency and complexity |
| Structured JSON response format | Forces the entire response into a schema; harder for Claude to produce natural prose alongside |
| Separate Claude calls for mutation intent | Two API calls per turn; doubles latency and cost |

**When you'd choose differently**: If mutations become complex (multi-step, conditional branching), tool_use provides cleaner error handling per action. For this feature's mutation surface (update workout, save memory), tag extraction is sufficient and faster.

---

### 3. SQLite Instead of PostgreSQL

**What we did**: Used a single SQLite file (`/data/coach.db`) with SQLAlchemy ORM and Alembic migrations.

**Why**: The scale is fixed — a handful of athletes, single self-hosted instance, no horizontal scaling. SQLite eliminates a separate database server, simplifies backup (copy one file), and fits the Synology NAS resource envelope. The threading concern (SQLite's GIL-adjacent writer model) is mitigated by using per-operation sessions: Bolt handlers and scheduler jobs never share a session.

**Alternatives considered**:
| Approach | Why it wasn't chosen |
|----------|---------------------|
| PostgreSQL | Adds a server process, network socket, auth config, and Docker service — all unnecessary for one instance |
| In-memory SQLite | State would vanish on restart; useless for a long-running coach |

**When you'd choose differently**: Multiple concurrent writers, read replicas, multi-instance deployment, or need for Postgres-specific features (JSONB, full-text search, window functions). For any team-facing or SaaS deployment, reach for Postgres immediately.

---

### 4. Garmin JSON Workout API Instead of FIT File Upload

**What we did**: Used Garmin Connect's REST workout API (`upload_workout()` + `schedule_workout()`) to create structured planned workouts on the athlete's device calendar.

**Why**: FIT file uploads via the activity endpoint are treated as _completed_ activities — they appear in the athlete's activity history, not on the future calendar. The JSON workout API is the correct mechanism for planned structured workouts with device-visible step-by-step guidance and pace targets.

**Alternatives considered**:
| Approach | Why it wasn't chosen |
|----------|---------------------|
| Binary FIT via `fit-tool` | Wrong endpoint semantics (activity, not planned workout); binary format complexity for no gain |
| ICS/calendar export | Only exports dates, not structured workout steps; athlete has to manually enter paces |

**When you'd choose differently**: If you need full 1-Hz telemetry for completed activities (diagnostics, analysis), download the original FIT file — the JSON telemetry endpoint downsamples for long runs.

---

### 5. Pace Encoded as m/s in Garmin's API (Not sec/km)

**What we did**: Implemented `_pace_to_speed_ms()` in `workout_builder.py` to convert internal min/km pace values to m/s floats for Garmin's `pace.zone` target type. The "faster bound" target value (`tv1`) is `speed_ms * 1.05`; the "slower bound" (`tv2`) is `speed_ms * 0.95`.

**Why**: Garmin's API field names (`targetValueOne`, `targetValueTwo`) and the historic research note saying "seconds per metre" are both misleading. The actual accepted unit is **speed in m/s as a float**. Getting this wrong produces wildly incorrect pace zones on the device. The 5% band approximates a ±15 sec/km tolerance, which is enough to keep athletes in the right zone without being rigid.

**Alternatives considered**:
| Approach | Why it wasn't chosen |
|----------|---------------------|
| Seconds per metre (original research note) | Incorrect — produces out-of-range values that Garmin rejects or misinterprets |
| Heart rate zone targets | Valid alternative, but we have pace data from the AI; HR requires athlete max HR calibration |

**When you'd choose differently**: If the athlete trains primarily by HR (e.g., cardiac rehab, no GPS watch), HR zone targets are more relevant. The encoder can switch target type per step.

---

### 6. App Marker in Garmin Workout Descriptions for O(1) Sync Validation

**What we did**: Embedded a structured marker `[rca:{athlete_id}:{YYYY-MM-DD}]` in every workout description on upload. Sync validation fetches the entire Garmin library once, scans for app markers, and builds a date-indexed map in memory. Repair decisions are then made locally without further API calls.

**Why**: The alternative — calling the Garmin schedule endpoint per workout to verify each one — scales as O(n) API calls where n is the upcoming workout count. For 4 weeks of daily workouts, that's 28 API calls per validation pass. A single library scan is O(1) API calls regardless of plan length, and Garmin's rate-limit risk stays flat.

**Alternatives considered**:
| Approach | Why it wasn't chosen |
|----------|---------------------|
| Per-workout ID verification | 28+ API calls per pass; brittle if Garmin rate-limits or the IDs go stale |
| Trust stored `garmin_workout_id` / `garmin_schedule_id` columns | IDs can go stale if the athlete manually deletes a workout on device; marker-based scan catches this |

**When you'd choose differently**: If Garmin ever rate-limits library fetches aggressively, fall back to per-ID spot-checks for only the workouts flagged as potentially stale.

---

### 7. Fernet Symmetric Encryption for Garmin Credentials

**What we did**: Stored Garmin email and Fernet-encrypted password in the `Athlete` table. The `ENCRYPTION_KEY` lives in `.env` and is never stored in the database. Decryption happens only at the moment of Garmin re-authentication.

**Why**: Garmin's automation API requires a username and password — OAuth is not available for third-party automation. The password must be stored somewhere accessible to the running process. Fernet (AES-128-CBC with HMAC-SHA256) provides authenticated encryption: a tampered ciphertext is rejected before decryption. Symmetric is appropriate here because the same process both encrypts (onboarding) and decrypts (Garmin auth refresh).

**Alternatives considered**:
| Approach | Why it wasn't chosen |
|----------|---------------------|
| Plaintext in DB | Obvious non-starter — credential exposure on any DB dump |
| System keychain / Vault | Adds an external dependency; overkill for a single self-hosted node |
| Asymmetric (RSA) | No benefit when encryption and decryption run in the same process; adds key management overhead |

**When you'd choose differently**: If credentials need to be accessed by multiple services with different trust levels, use an HSM or Vault with asymmetric key separation.

---

### 8. Slack Socket Mode Instead of HTTP Webhook

**What we did**: Used `SocketModeHandler` with a `SLACK_APP_TOKEN`, which opens an outbound WebSocket from the service to Slack's servers. No inbound ports or public URL required.

**Why**: The service runs on a Synology NAS behind a home NAT router. Opening inbound ports, managing TLS certificates, and configuring port forwarding adds operational complexity for zero functional gain. Socket mode flips the connection direction — the service calls Slack, not the other way around.

**Alternatives considered**:
| Approach | Why it wasn't chosen |
|----------|---------------------|
| HTTP webhook mode | Requires a public IP or ngrok-style tunnel, TLS termination, and inbound firewall rules |
| Polling Slack API for new events | Slack deprecated RTM (Real Time Messaging) API; polling is not the modern pattern |

**When you'd choose differently**: If you need to verify Slack request signatures for security auditing, HTTP mode is required (socket mode authenticates via the app token instead). For public-facing enterprise deployments, HTTP mode is standard.

---

## Concepts to Know

### Health-Data Gate with Idempotent Dedup Guard

**What it is**: Two cooperating guards in `run_morning_checkin()`. The health-data gate checks whether Garmin's key metrics (`sleep_score`, `hrv_score`, `body_battery_start`) are populated — if not, it returns early without sending. The dedup guard checks `athlete.last_morning_checkin_date == today` at function entry — if true, the function returns immediately. The dedup field is set to today only after a successful DM send.

**Where we used it**: `running_coach_ai/coach/adapter.py` — `_HEALTH_KEY_FIELDS` constant, dedup guard block, health-data gate (two branches: silent return before 10 am, logged skip at/after 10 am), and `last_morning_checkin_date = today` + `db_session.commit()` on success.

**Why it matters**: Without the dedup guard, a 30-minute polling job would send multiple messages on days when health data arrives between two ticks. Without the two-branch gate (silent vs. logged), ops would either get noisy pre-10am "no data yet" logs drowning signal, or miss genuine data-unavailability events after 10am.

---

### `scoped_query()` as the Data Isolation Boundary

**What it is**: A thin helper in `database/session.py` that wraps `db_session.query(Model).filter(Model.athlete_id == athlete_id)`. Every athlete-scoped read in the codebase goes through this helper. It makes `athlete_id` a required argument at the call site, preventing the most common cross-athlete data leak: forgetting to add a filter.

**Where we used it**: Throughout `scheduler/jobs.py`, `coach/adapter.py`, `slack/conversation.py`, and anywhere a model with an `athlete_id` FK is queried. The reconciliation phase (T067) added tests that assert no new non-scoped queries are introduced.

**Why it matters**: In a multi-athlete system, a single missing `.filter(athlete_id == ...)` exposes one athlete's health data, workout history, or coach memories to another's check-in. The helper makes the safety invariant visible at every query site rather than relying on convention.

---

### XML Side-Effect Tags as a Bidirectional Claude Interface

**What it is**: Claude returns a single text response containing natural-language prose for the athlete plus structured machine instructions encoded as XML tags. The application layer strips the XML before display. Claude is instructed via system prompt about the tag formats and when to emit them.

**Where we used it**: `slack/conversation.py` — `extract_and_apply_plan()`, `extract_and_save_memories()`, `extract_and_apply_garmin_sync()`. The onboarding handler also uses this pattern for `<onboarding_complete>{json}</onboarding_complete>`.

**Why it matters**: This approach keeps the LLM call count at 1 per turn regardless of how many structured mutations are needed. The tradeoff is that prompt engineering must be reliable — if Claude forgets to emit a `<plan>` tag or emits malformed JSON inside one, the mutation silently fails. Robust tag extraction (regex, not XML parsing, to tolerate Claude's occasional prose inside tags) and per-mutation error handling are essential.

---

### Session-Per-Operation Pattern (No Shared DB Sessions)

**What it is**: Every Slack Bolt event handler and every APScheduler job creates its own DB session at entry and closes it at exit using a `with get_session() as db_session:` context manager. Sessions are never stored in module-level variables or passed across thread boundaries.

**Where we used it**: `scheduler/jobs.py` — each job function opens a fresh session. `slack/bot.py` — each event handler opens a session. `slack/onboarding.py`, `slack/conversation.py` — session passed as argument from the handler layer, not created inside these modules.

**Why it matters**: SQLite's threading model makes shared sessions across threads unsafe. APScheduler jobs and Bolt handlers run on different threads. If a session were shared, a half-written transaction in one thread could be read or corrupted by another. Per-operation sessions also keep transactions short, reducing lock contention.

---

### Daemon Thread + BlockingScheduler: The Single-Process Dual-Loop

**What it is**: `main.py` starts Slack's `SocketModeHandler` with `.connect()` (non-blocking — spawns a daemon WebSocket thread), then starts APScheduler's `BlockingScheduler` with `.start()` (blocks the main thread). The process stays alive because the scheduler is blocking. The Slack handler exits automatically if the main thread dies.

**Where we used it**: `main.py` — the two-line startup sequence. The daemon thread property of the Bolt handler is critical: if APScheduler raises an unhandled exception and the main thread exits, Python won't hang waiting for the Slack WebSocket to close.

**Why it matters**: The alternative is `threading.Thread(target=scheduler.start).start()` with `handler.start()` blocking instead. Either works, but the chosen order makes the scheduler the "owner" of the main thread, which is more idiomatic for a daemon service: if the scheduler crashes, the process dies cleanly rather than hanging as a zombie with only the Slack connection alive.

---

## Architecture Overview

The service has three parallel execution contexts that all share state through a single SQLite file:

1. **Slack Bolt daemon thread** — reacts to athlete messages in real time; routes to onboarding or conversation handler.
2. **APScheduler main thread** — fires three recurring jobs: 30-min morning check-in poll per athlete, 10-min activity poll, and Sunday weekly review.
3. **Admin DM intercept** — inside the Bolt handler; special-cased before the allow-list check.

```
 Slack WebSocket (daemon thread)
   └── bot.py → onboarding.py or conversation.py
                     │                │
              Claude API        Garmin Connect
                     └── mutations ──▶ SQLite ◀── APScheduler (main thread)
                                                         │
                                                   Garmin Connect
                                                   Open-Meteo
                                                   Claude API
```

Domain packages are cleanly separated: `garmin/` (auth, fetch, upload), `coach/` (prompt assembly, Claude calls, plan mutation), `slack/` (Bolt events, conversation, onboarding, admin), `scheduler/` (job registration and execution), `weather/` (Open-Meteo fetch and adjustment rules). Cross-domain calls always go through explicit function imports — no circular dependencies.

---

## Glossary

| Term                  | Meaning                                                                                                                                                                                                              |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `garth`               | OAuth token library that backs `python-garminconnect`. Handles Garmin SSO token storage, refresh, and expiry automatically.                                                                                          |
| App marker            | String `[rca:{athlete_id}:{YYYY-MM-DD}]` embedded in Garmin workout descriptions to identify and locate app-created workouts without relying on stored IDs.                                                          |
| `scoped_query`        | Helper in `database/session.py` that always adds `.filter(Model.athlete_id == id)` — the data isolation boundary for all athlete-scoped reads.                                                                       |
| Health-data gate      | Guard in `run_morning_checkin()` that returns early if Garmin has not yet posted sleep score, HRV, or body battery for today; enables the 30-min polling loop to retry.                                              |
| `_next_checkin_start` | Pure helper that returns the next 07:00 in an athlete's timezone as a timezone-aware datetime; used to anchor the `IntervalTrigger.start_date` so polling never starts before 7 am.                                  |
| Socket mode           | Slack's WebSocket-based event delivery that requires no inbound ports — the service opens an outbound connection to Slack's servers.                                                                                 |
| Dedup guard           | Check at `run_morning_checkin()` entry that returns immediately if `athlete.last_morning_checkin_date == today`, preventing duplicate DMs when the 30-min poller fires after data has already arrived and been sent. |
| `BlockingScheduler`   | APScheduler scheduler variant that blocks the calling thread — used on the main thread to keep the process alive alongside the Slack daemon thread.                                                                  |
