# Feature: Athlete Timezone Auto-Detection

## Background

The morning check-in job fires at 07:00 in the athlete's local timezone, configured at
registration using `athletes.timezone`. Currently this field is never populated — it
defaults to `NULL`, which the scheduler treats as UTC. For athletes outside UTC this
causes the check-in to fire at the wrong local time (e.g. 3 AM for a US Eastern athlete).

Two separate problems need solving:
1. Timezone is never set during onboarding.
2. Timezone is static — a traveling athlete (e.g. racing in Korea for a week) will
   receive their check-in at their home timezone, not local time.

---

## Proposed Changes

### 1. Derive timezone at onboarding

**When:** `_complete_onboarding()` is called after the athlete confirms their profile.

**How:** The onboarding flow already collects `home_lat` / `home_lon` for weather. Use
`timezonefinder` to convert those coordinates to an IANA timezone string (e.g.
`America/New_York`). Write the result to `athletes.timezone` before registering the
morning cron job, so the job is always registered with the correct timezone from day one.

**No new onboarding question is needed.** The athlete's location already provides this.

**Fallback:** If `home_lat` / `home_lon` are not available or `timezonefinder` fails,
fall back to `"UTC"` and log a warning.

---

### 2. Auto-update timezone when athlete travels

**When:** A new activity is ingested in `_ingest_and_feedback()` (i.e. after every run).

**How:**
1. Extract GPS coordinates from the activity detail response (starting position or first
   lap fix).
2. Run `timezonefinder` on those coordinates to get the IANA timezone.
3. Compare to `athletes.timezone` stored in the DB.
4. If different, update `athletes.timezone` and re-register the athlete's morning cron
   job on the live scheduler with the new timezone — no restart required.
5. Log the timezone change (old → new) at INFO level.

**Effect:** An athlete who flies to Korea and runs there will have their morning check-in
automatically shift to Korean local time from the following morning. When they return
home and run again, it shifts back.

**Fallback:** If the activity has no GPS data, skip the timezone update silently.

---

### 3. Fix activity poll active-hours gate

**Current behaviour:** The `_run_activity_poll` job skips execution if
`datetime.now().hour` is outside 06:00–22:00. This check uses **server local time**,
meaning athletes whose server runs in a different timezone get skipped at the wrong
hours — broken for a global app.

**Fix:** Replace the single server-time gate with a per-athlete check inside the polling
loop. For each athlete, compare the current UTC time against their stored timezone to
determine their local hour. Only poll athletes whose local time falls within 06:00–22:00.
Athletes in active hours are polled; others are skipped. The outer time gate is removed.

**Alternative (simpler):** Remove the gate entirely. The Garmin API only returns
activities that actually exist — empty polls are cheap and harmless. This eliminates the
timezone complexity in the poller at the cost of ~16 extra no-op API calls per athlete
per day.

The preferred approach is the simpler removal of the gate, unless Garmin rate limiting
becomes a concern.

---

## New Dependency

- `timezonefinder` — pure-Python, no external API, derives IANA timezone from lat/lon.
  Add to `requirements.txt`.

---

## Data Changes

- No schema changes required. `athletes.timezone` already exists as a `Text` column.
- `athletes.timezone` should be treated as a mutable field going forward (currently
  effectively write-once).

---

## Out of Scope

- Prompting the athlete to confirm their timezone.
- Storing GPS coordinates on `CompletedWorkout` (only needed transiently for this feature).
- Handling cases where the athlete has no GPS watch or runs on a treadmill (no GPS = no
  update, existing timezone retained).
- Garmin API-based timezone extraction (offset-only, not reliable for IANA names).
