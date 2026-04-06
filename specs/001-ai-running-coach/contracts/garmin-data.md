# Contract: Garmin Data Ingestion

**Branch**: `001-ai-running-coach` | **Date**: 2026-03-31

Defines what data the system reads from Garmin Connect and how it is normalised into internal DB models.

---

## Authentication Contract

Per-athlete authentication. No global credentials.

```
Input:  athlete.garmin_email (plaintext in memory after decrypt)
        athlete.garmin_password_encrypted (Fernet-encrypted in DB)
        garmin_session_dir/{athlete_id}/  (garth token directory)

Flow:
  1. Attempt to load session from token directory
  2. If session valid: use directly
  3. If session expired: refresh using garth token refresh
  4. If refresh fails: re-authenticate with decrypted email/password
  5. Save updated tokens back to token directory
  6. On any auth failure after retry: log + raise; calling job skips and logs
```

**MFA limitation**: Garmin accounts with MFA enabled cannot be authenticated programmatically. Athletes must disable MFA or the system cannot ingest their data. Document this in onboarding flow.

---

## Daily Health Read Contract

Fetched once per athlete per morning (before morning check-in job).

| Garmin Method | Data read | Maps to HealthSnapshot field |
|--------------|-----------|------------------------------|
| `get_sleep_data(date)` | `dailySleepDTO.sleepScores.overall.value` | `sleep_score` |
| `get_sleep_data(date)` | `dailySleepDTO.sleepTimeSeconds` | `sleep_duration_seconds` |
| `get_hrv_data(date)` | `hrvSummary.lastNight` | `hrv_score` |
| `get_hrv_data(date)` | `hrvSummary.status` | `hrv_status` |
| `get_rhr_day(date)` | `allDayHR.restingHeartRate` | `resting_hr` |
| `get_body_battery(date, date)` | first entry `charged` | `body_battery_start` |
| `get_stress_data(date)` | `avgStressLevel` | `stress_avg` |
| `get_steps_data(date)` | daily step count | `steps` |
| `get_spo2_data(date)` | average SpO2 | `spo2_avg` |

**Missing data handling**: If any field is absent in the Garmin response (device didn't record it, API returned null, or method raises), the corresponding `HealthSnapshot` field is left NULL. The check-in job proceeds with whatever data is available. If the entire health read fails (API down), the morning check-in message notes data unavailability and proceeds without plan adjustment.

**Upsert**: `HealthSnapshot` is upserted on `(athlete_id, date)` — safe to re-run if job fires twice.

---

## Activity Detection Contract

Polled every 30 minutes during 06:00–22:00.

```
Method: get_activities_by_date(
  startdate = yesterday_date,
  enddate = today_date,
  activitytype = "running"
)
Returns: list of activity summary dicts
```

**New activity detection**:
1. Extract `activityId` from each returned activity.
2. Check if `garmin_activity_id` exists in `CompletedWorkout` for this athlete.
3. If not found: trigger full ingestion pipeline for this activity.
4. If found: skip (already processed).

**After active hours**: Activities detected outside 06:00–22:00 are queued for the next active-hours poll window. Feedback is not sent at night.

---

## Activity Detail Read Contract

Triggered for each newly detected activity.

### Step 1 — Summary Metrics

```
Method: get_activity_details(activity_id)

Fields extracted → CompletedWorkout columns:
  distance         → distance_km (convert from metres)
  duration         → duration_seconds
  averageHR        → avg_hr
  maxHR            → max_hr
  averageSpeed     → avg_pace_min_per_km (convert: 1000/speed_m_per_s / 60)
  maxSpeed         → max_pace_min_per_km
  averageRunningCadenceInStepsPerMinute → avg_cadence_spm
  maxRunningCadenceInStepsPerMinute     → max_cadence_spm
  avgStrideLength  → avg_stride_length_m
  avgGroundContactTime → avg_ground_contact_time_ms
  avgVerticalOscillation → avg_vertical_oscillation_cm
  avgVerticalRatio → avg_vertical_ratio_pct
  avgPower         → avg_power_w
  maxPower         → max_power_w
  elevationGain    → elevation_gain_m
  activityTrainingLoad → training_load
  aerobicTrainingEffect  → aerobic_training_effect
  anaerobicTrainingEffect → anaerobic_training_effect
  vO2MaxValue      → vo2max_estimate
  calories         → calories
```

### Step 2 — Time-Series Telemetry

```
Method: get_activity_details(activity_id)

Response structure:
  metricDescriptors: [{ metricsKey, unit, ... }, ...]
  activityDetailMetrics: [{ metrics: [val1, val2, ...] }, ...]

Extraction:
  Build index map: metricsKey → column_index
  For each recognised stream key, extract column as JSON array

Stream key → WorkoutTelemetry column mapping:
  directHeartRate              → heart_rate_json
  directSpeed (inverted)       → pace_json  (convert m/s → min/km)
  directRunCadence             → cadence_json
  directStrideLength           → stride_length_json
  directGroundContactTime      → ground_contact_time_json
  directVerticalOscillation    → vertical_oscillation_json
  directVerticalRatio          → vertical_ratio_json
  directPower                  → power_json
  directAltitude               → elevation_json
  directAirTemperature         → air_temperature_json
  directRespirationRate        → respiration_rate_json
  directPerformanceCondition   → performance_condition_json
```

**Graceful skip**: If a stream key is absent from `metricDescriptors`, that column is stored as NULL. The `telemetry_channels_json` column on `CompletedWorkout` records the list of keys that were actually present.

### Step 3 — Lap Splits

```
Method: get_activity_splits(activity_id)

For each lap: extract avg/max for all available metrics → laps_json array
```

---

## Biomechanical Analysis Pipeline (Post-Ingestion)

After telemetry is stored, `biomechanics.py` computes derived metrics:

| Metric | Source | Computation |
|--------|--------|-------------|
| HR drift % | `heart_rate_json` | `(avg_hr_last_quarter - avg_hr_first_quarter) / avg_hr_first_quarter × 100` |
| Aerobic decoupling | `heart_rate_json`, `pace_json` | HR:pace ratio in first half vs second half |
| Zone compliance | `heart_rate_json` | % of samples where HR ≤ zone 2 threshold (derived from athlete max HR) |
| Cadence consistency | `cadence_json` | Coefficient of variation; trend across laps |
| Effort distribution | `pace_json` | Classification: even / positive split / negative split |

**RunningProfile update**: After each computation, upsert `RunningProfile` for the athlete with recalculated 30-day rolling averages. Trend direction (`improving`/`stable`/`declining`) requires minimum 3 data points.

---

## Workout Upload Contract

See `contracts/coach-output.md` for the full upload lifecycle.

**Method signatures** (python-garminconnect):
```
upload_workout(workout_json: dict) → {"workoutId": int, ...}
schedule_workout(workout_id: int, date: str) → {"scheduleId": int, ...}
delete_workout(workout_id: int) → None
```

**Error handling**: If upload fails (network, auth, API error): log error, retain `PlannedWorkout` row with `garmin_workout_id=None`, mark for retry on next scheduler run.
