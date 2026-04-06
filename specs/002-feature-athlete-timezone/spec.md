# Feature Specification: Athlete Timezone Auto-Detection

**Feature Branch**: `002-feature-athlete-timezone`  
**Created**: 2026-04-04  
**Status**: Draft  
**Input**: User description provided in triggering message.

## Clarifications

### Session 2026-04-04

- Q: Should the timezone update immediately after a single activity in a new location? → A: Update immediately after the first activity in a new timezone.
- Q: Should the athlete be notified when their morning check-in time shifts due to a detected timezone change? → A: Silently update (only logs).
- Q: Should we perform a one-time migration for existing athletes' timezones? → A: Update naturally on next activity.
- Q: If timezonefinder fails to resolve valid GPS coordinates, should it still fall back to "UTC" or should it retain the previously stored timezone? → A: Retain the previously stored timezone.
- Q: Should we add a manual "Sync Now" button or command for athletes who want to force a timezone update? → A: No manual trigger; rely on automated polling.
- Q: Once the 06:00–22:00 server-time gate is removed from `_run_activity_poll`, what replaces it? → A: Remove gate entirely — poll all athletes unconditionally 24/7 (no per-athlete local-hour gate either).
- Q: What Garmin API field path(s) should be used to extract GPS start coordinates from an activity detail payload? → A: `summaryDTO.startLatitude`/`summaryDTO.startLongitude` on the activity object (primary); fall back to `lapDTOs[0].startLatitude`/`lapDTOs[0].startLongitude` if absent. _(Initial answer referenced `beginningLatitude`/`beginningLongitude`; superseded by research phase — see plan.md note and `contracts/gps-timezone-extraction.md`.)_
- Q: How should a detected timezone change trigger re-registration of the athlete’s morning cron job? → A: `_run_activity_poll` calls `register_athlete_morning_job(scheduler, athlete, slack_client)` directly after ingestion; the scheduler is passed down the call chain from `_run_activity_poll`.
- Q: Should `timezonefinder` also replace the city lookup during onboarding, or does the hardcoded city table stay as-is? → A: Hybrid — keep the hardcoded table as the primary path; call `timezonefinder` on the lat/lon returned by the table only when the city is NOT found in the table.
- Q: Does this feature require any new database schema changes? → A: No — `athletes.timezone` already exists as a nullable Text column; no migration is needed.

## User Scenarios & Testing _(mandatory)_

### User Story 1 - Automatic Timezone Setup (Priority: P1)

As a new athlete completing registration, I want my morning check-in to be automatically scheduled in my local time without having to manually select a timezone, so that I receive my daily plan at the correct hour from day one.

**Why this priority**: Core fix for the current issue where `athletes.timezone` remains `NULL` and defaults to UTC, causing check-ins at incorrect local hours for most users.

**Independent Test**: Register a new athlete with coordinates in a non-UTC timezone (e.g., US Eastern) and verify that the `timezone` field in the database is correctly set to "America/New_York" and the morning job is registered for 07:00 EST.

**Acceptance Scenarios**:

1. **Given** an athlete provides home coordinates during onboarding, **When** `_complete_onboarding()` is called, **Then** the system MUST derive the IANA timezone and save it to `athletes.timezone`.
2. **Given** home coordinates are unavailable or invalid, **When** onboarding completes, **Then** the system MUST fall back to "UTC" and log a warning.

---

### User Story 2 - Travel Auto-Adjustment (Priority: P2)

As a traveling athlete, I want my morning check-in time to automatically adjust to my current location after I record a run, so that I don't get woken up too early or receive my plan too late while away from home.

**Why this priority**: Fixes the "static timezone" problem for global users or travelers.

**Independent Test**: Take an athlete with a home timezone of "America/New_York", ingest a new activity with GPS coordinates in "Asia/Seoul", and verify that `athletes.timezone` updates to "Asia/Seoul" and the scheduler job is re-registered for the new local time.

**Acceptance Scenarios**:

1. **Given** a new activity is ingested with GPS coordinates, **When** the location maps to a different timezone than the one stored, **Then** the system MUST update `athletes.timezone` and re-register the morning cron job.
2. **Given** a new activity is ingested without GPS data (e.g., treadmill run), **When** processing completion, **Then** the system MUST NOT change the existing timezone.

---

### User Story 3 - Global Activity Polling (Priority: P2)

As an athlete in any timezone, I want the system to poll my Garmin activities during my active hours regardless of where the server is located, so that my runs are synced promptly.

**Why this priority**: Necessary to support a global user base where the "server local time" gate (06:00-22:00) currently blocks sync for athletes in significantly different timezones.

**Independent Test**: Set server time to 03:00 and verify that activities for an athlete whose local time is 10:00 are still successfully polled and ingested.

**Acceptance Scenarios**:

1. **Given** the activity poller is running, **When** evaluating whether to poll an athlete, **Then** the system MUST NOT skip polling based on server-local hour.

---

### Edge Cases

- **Treadmill/Indoor Runs**: Activity detail contains no GPS points. The system must retain the previous timezone.
- **Location on Timezone Border**: `timezonefinder` should use its standard resolution to determine the most likely IANA timezone.
- **Unresolvable GPS Coordinates**: If `timezonefinder` fails to resolve valid GPS coordinates to an IANA timezone string, the system MUST retain the previously stored timezone and log a warning.
- **Scheduler Failures**: If the live scheduler fails to re-register a job, the system should log an error but keep the updated timezone in the database so it can be recovered on next restart.

## Requirements _(mandatory)_

### Functional Requirements

- **FR-001**: System MUST use the `timezonefinder` library to derive IANA timezone strings from latitude/longitude coordinates.
- **FR-002**: System MUST populate `athletes.timezone` during the onboarding completion flow using the following priority: (1) look up the city name in the hardcoded table (`_parse_city_to_coords`); if found, use the bundled IANA timezone directly. (2) If the city is NOT in the table but valid `home_lat`/`home_lon` are available, derive the timezone via `timezonefinder`. **Note**: In the current implementation `home_lat`/`home_lon` are only ever set by `_parse_city_to_coords`, so tier 2 is a forward-compatible path that cannot fire today — an unlisted city will always reach tier 3. Tier 2 becomes active if a geocoder is added in the future. (3) If both fail, fall back to "UTC" and log a warning.
- **FR-003**: Existing athletes with `NULL` or incorrect timezones MUST be updated naturally upon ingestion of their first activity after this feature is deployed.
- **FR-004**: System MUST extract GPS coordinates from the Garmin activity detail payload using this priority order: (1) `activityDetail.activity.summaryDTO.startLatitude` / `summaryDTO.startLongitude`; (2) `activityDetail.activity.lapDTOs[0].startLatitude` / `lapDTOs[0].startLongitude`. If neither path yields non-null, non-zero values the activity is treated as having no GPS data (e.g., treadmill) and the timezone is unchanged. (Field paths are authoritative per `contracts/gps-timezone-extraction.md`.)
- **FR-005**: System MUST update `athletes.timezone` immediately if the timezone derived from the first activity in a new location differs from the stored value.
- **FR-006**: System MUST trigger a dynamic re-registration of the athlete's morning check-in cron job upon any timezone change.
- **FR-007**: System MUST remove the `if not (6 <= now.hour < 22)` server-time execution gate in `_run_activity_poll` unconditionally — no replacement per-athlete hour gate. Polling runs 24/7. The existing 16-hour stale-window guard inside `_ingest_and_feedback` remains as the sole backstop against redundant feedback.
- **FR-008**: System MUST log timezone transitions at INFO level (e.g., "Timezone updated for Athlete X: America/New_York -> Asia/Seoul") and MUST NOT send a user-facing notification.
- **FR-009**: When a timezone change is detected during activity ingestion, `_run_activity_poll` MUST call `register_athlete_morning_job(scheduler, athlete, slack_client)` directly, passing the scheduler instance already held by `_run_activity_poll`. The scheduler MUST be threaded down the call chain from `_run_activity_poll` → `_ingest_and_feedback` so it is available at the point of re-registration.

### Key Entities

- **Athlete**: Represents the user. Key attributes involved: `home_lat`, `home_lon`, and `timezone`.
- **Activity**: The Garmin run data. Attributes involved: GPS starting/lap coordinates.

## Success Criteria _(mandatory)_

### Measurable Outcomes

- **SC-001**: 100% of newly registered athletes with valid home coordinates have a valid IANA timezone string assigned immediately upon onboarding completion.
- **SC-002**: Timezone updates occur within 60 seconds of a travel activity being successfully ingested.
- **SC-003**: Morning check-in jobs fire within ±5 minutes of 07:00 in the athlete's _current_ local timezone, as verified by log timestamps.
- **SC-004**: Activities recorded by athletes in any global timezone are ingested within the standard polling interval (e.g., every 15-30 mins) regardless of server time.

## Assumptions

- **Scheduler Capability**: It is assumed the existing scheduler allows for dynamic updating or re-registration of specific athlete jobs without requiring a full service restart.
- **Poller Load**: Removing the poller time gate entirely (24/7 polling) is assumed to be safe regarding Garmin API rate limits, even with the ~16 extra no-op iterations per athlete per day.
- **Data Availability**: The `home_lat` and `home_lon` collected during onboarding are assumed to be accurate enough for timezone derivation.
- **No Schema Migration Required**: `athletes.timezone` (`Text, nullable=True`) already exists in the ORM model and deployed schema. This feature only populates and updates the column — no Alembic migration is needed.
