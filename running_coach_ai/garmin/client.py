"""Garmin Connect client — auth, session caching, data fetch, workout upload."""

import functools
import logging
import os
import time

from cryptography.fernet import Fernet
from garminconnect import Garmin

from running_coach_ai.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth error detection
# ---------------------------------------------------------------------------

def is_garmin_auth_error(exc: Exception) -> bool:
    """Return True if exc is a Garmin authentication failure (bad credentials)."""
    try:
        import garminconnect as _gc
        if isinstance(exc, _gc.GarminConnectAuthenticationError):
            return True
    except (ImportError, AttributeError):
        pass
    try:
        import garth.exc as _garth
        if isinstance(exc, _garth.GarthHTTPError) and "401" in str(exc):
            return True
    except (ImportError, AttributeError):
        pass
    err = str(exc).lower()
    return any(kw in err for kw in ("401", "unauthorized", "authentication failed", "invalid credentials"))


# ---------------------------------------------------------------------------
# Exponential backoff retry decorator
# ---------------------------------------------------------------------------

def retry_garmin(max_attempts: int = 3, base_delay: float = 2.0):
    """Decorator: retry a Garmin API call with exponential backoff.

    Retries on HTTP 4xx/5xx errors, rate limits, auth errors, and network errors.
    After max_attempts failures, re-raises the last exception.
    """
    import requests as _requests

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    error_str = str(e).lower()
                    # Retry on: rate limits, auth errors, HTTP errors (4xx/5xx),
                    # and transient network errors (connection reset, timeout, etc.)
                    is_retryable = (
                        isinstance(e, (_requests.exceptions.HTTPError,
                                       _requests.exceptions.ConnectionError,
                                       _requests.exceptions.Timeout))
                        or any(
                            kw in error_str
                            for kw in ("429", "rate limit", "too many requests",
                                       "unauthorized", "auth", "token",
                                       "500", "502", "503", "504",
                                       "connection", "timeout", "reset")
                        )
                    )
                    if not is_retryable or attempt == max_attempts - 1:
                        raise
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "Garmin API call %s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                        func.__name__, attempt + 1, max_attempts, e, delay,
                    )
                    time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Fernet encryption helpers
# ---------------------------------------------------------------------------

def encrypt_password(plaintext: str) -> bytes:
    """Encrypt a plaintext password using the configured Fernet key."""
    f = Fernet(settings.ENCRYPTION_KEY.encode())
    return f.encrypt(plaintext.encode())


def decrypt_password(ciphertext: bytes) -> str:
    """Decrypt a Fernet-encrypted password."""
    f = Fernet(settings.ENCRYPTION_KEY.encode())
    return f.decrypt(ciphertext).decode()


# ---------------------------------------------------------------------------
# Per-athlete Garmin client
# ---------------------------------------------------------------------------

def _token_dir(athlete_id: int) -> str:
    return os.path.join(settings.GARMIN_SESSION_DIR, str(athlete_id))


def _ensure_fresh_oauth2(garmin: Garmin, athlete_id: int, max_attempts: int = 4) -> None:
    """Refresh OAuth2 access token if expired, with retry on 429.

    Garmin's `oauth/exchange/user/2.0` endpoint rate-limits shared cloud IPs
    (Railway etc.). Many of those 429s are short-lived (per-minute windows),
    so we retry with exponential backoff before giving up. Total max wait
    is ~31s, keeping admin HTTP requests responsive.

    If the OAuth2 token is still valid, returns immediately without any
    network call. Raises on non-429 errors or after all retries exhausted.
    """
    from garth.auth_tokens import OAuth2Token as _OAuth2Token

    tok = getattr(garmin.garth, "oauth2_token", None)
    if isinstance(tok, _OAuth2Token) and not tok.expired:
        return  # already fresh

    delays = [3, 8, 20]
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            garmin.garth.refresh_oauth2()
            logger.info(
                "Garmin OAuth2 refreshed for athlete %s (attempt %d/%d)",
                athlete_id, attempt + 1, max_attempts,
            )
            return
        except Exception as e:
            last_exc = e
            err = str(e)
            is_429 = "429" in err or "too many requests" in err.lower()
            if not is_429 or attempt == max_attempts - 1:
                break
            wait = delays[attempt] if attempt < len(delays) else delays[-1]
            logger.warning(
                "Garmin oauth/exchange rate-limited (429) for athlete %s "
                "[attempt %d/%d] — retrying in %ds",
                athlete_id, attempt + 1, max_attempts, wait,
            )
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def _login_with_rate_limit_retry(garmin: Garmin, athlete_id: int, max_attempts: int = 2) -> None:
    """Call garmin.login() with a single short retry for transient failures.

    429 rate-limit responses from Garmin SSO are typically IP-based and last
    hours — retrying immediately doesn't help. We do one short retry (30s) for
    transient errors, then fail fast so the scheduler can retry naturally on
    the next 30-minute job tick.
    All other exceptions are re-raised immediately.
    """
    for attempt in range(max_attempts):
        try:
            garmin.login()
            return
        except Exception as e:
            is_rate_limited = "429" in str(e) or "too many requests" in str(e).lower()
            if is_rate_limited:
                # IP-based rate limits last hours — fail fast, let scheduler retry later
                logger.warning(
                    "Garmin SSO rate limited (429) for athlete %s — "
                    "Railway IP may be blocked. Skipping re-auth until next scheduler tick.",
                    athlete_id,
                )
                raise
            if attempt < max_attempts - 1:
                wait = 30
                logger.warning(
                    "Garmin login failed for athlete %s (attempt %d/%d): %s. Retrying in %ds...",
                    athlete_id, attempt + 1, max_attempts, e, wait,
                )
                time.sleep(wait)
            else:
                raise


def get_garmin_client(
    athlete_id: int,
    email: str,
    encrypted_password: bytes,
    db_session=None,
) -> Garmin:
    """Return an authenticated Garmin client for a specific athlete.

    Token resolution order:
    1. DB-stored tokens (Athlete.garmin_oauth_tokens) — tried first when db_session provided
    2. Filesystem token cache (garth oauth JSON files)
    3. Full SSO re-authentication — last resort, hits Railway's rate-limited IP

    After any successful load, saves current tokens back to DB so they stay
    fresh across Railway redeploys (which wipe the filesystem token cache).
    """
    from running_coach_ai.database.models import Athlete as _AthleteModel

    token_path = _token_dir(athlete_id)
    os.makedirs(token_path, exist_ok=True)

    password = decrypt_password(encrypted_password)
    garmin = Garmin(email, password)

    if hasattr(garmin, 'garth'):
        garmin.garth.configure(timeout=settings.GARMIN_TIMEOUT)

        def _save_tokens(g: Garmin) -> None:
            if db_session is None:
                return
            try:
                row = db_session.get(_AthleteModel, athlete_id)
                if row:
                    row.garmin_oauth_tokens = encrypt_password(g.garth.dumps()).decode()
                    db_session.flush()
                    logger.debug("Saved Garmin tokens to DB for athlete %s", athlete_id)
            except Exception as _e:
                logger.warning("Could not save Garmin tokens to DB for athlete %s: %s", athlete_id, _e)

        def _validate(g: Garmin) -> None:
            """Lightweight API call to confirm tokens are usable; sets display_name."""
            g.get_full_name()
            if g.garth.profile:
                g.display_name = g.garth.profile.get("displayName")

        def _is_rate_limited(e: Exception) -> bool:
            s = str(e)
            return "429" in s or "too many requests" in s.lower()

        # --- 1. DB tokens ---
        if db_session is not None:
            try:
                row = db_session.get(_AthleteModel, athlete_id)
                db_tokens = row.garmin_oauth_tokens if row else None
            except Exception:
                db_tokens = None

            if db_tokens:
                try:
                    garmin.garth.loads(decrypt_password(db_tokens.encode()))
                    _ensure_fresh_oauth2(garmin, athlete_id)
                    _validate(garmin)
                    _save_tokens(garmin)  # refresh in case garth auto-renewed the OAuth2 token
                    logger.info("Garmin session loaded from DB for athlete %s", athlete_id)
                    return garmin
                except Exception as e:
                    if _is_rate_limited(e):
                        logger.warning(
                            "Garmin API rate limited (429) for athlete %s during DB token validation — "
                            "OAuth2 refresh blocked, cannot proceed.",
                            athlete_id,
                        )
                        raise
                    logger.warning("DB tokens invalid for athlete %s (%s), trying filesystem", athlete_id, e)

        # --- 2. Filesystem tokens ---
        try:
            garmin.garth.load(token_path)
            _ensure_fresh_oauth2(garmin, athlete_id)
            _validate(garmin)
            _save_tokens(garmin)  # migrate filesystem tokens into DB
            logger.info("Garmin session loaded from cache for athlete %s", athlete_id)
            return garmin
        except Exception as e:
            if _is_rate_limited(e):
                logger.warning(
                    "Garmin API rate limited (429) for athlete %s during session validation — "
                    "OAuth2 refresh blocked, cannot proceed.",
                    athlete_id,
                )
                raise
            logger.warning("Cached session invalid for athlete %s (%s), re-authenticating", athlete_id, e)
            if os.path.exists(token_path):
                logger.info("Token directory %s contains: %s", token_path, os.listdir(token_path))
            else:
                logger.warning("Token directory %s does not exist", token_path)

        # --- 3. Full SSO re-authentication ---
        try:
            _login_with_rate_limit_retry(garmin, athlete_id)
            garmin.garth.dump(token_path)
            _save_tokens(garmin)
            logger.info("Garmin re-authenticated and session cached for athlete %s", athlete_id)
            return garmin
        except Exception as e:
            logger.error("Garmin authentication failed for athlete %s: %s", athlete_id, e)
            raise
    else:
        # Old garminconnect without garth — no token caching, direct SSO login.
        logger.warning("garminconnect without garth detected for athlete %s — upgrade recommended", athlete_id)
        try:
            _login_with_rate_limit_retry(garmin, athlete_id)
            logger.info("Garmin authenticated (no garth) for athlete %s", athlete_id)
            return garmin
        except Exception as e:
            logger.error("Garmin authentication failed for athlete %s: %s", athlete_id, e)
            raise


# ---------------------------------------------------------------------------
# Workout upload / schedule / delete
# ---------------------------------------------------------------------------

@retry_garmin()
def upload_workout(garmin: Garmin, workout_json: dict) -> dict:
    """Upload a workout to Garmin Connect and return the response containing workoutId."""
    result = garmin.upload_workout(workout_json)
    logger.info("Uploaded workout: %s", result.get("workoutId"))
    return result


@retry_garmin()
def schedule_workout(garmin: Garmin, workout_id: int, date_str: str) -> dict:
    """Schedule a workout on the Garmin calendar for a given date."""
    result = garmin.schedule_workout(workout_id, date_str)
    logger.info("Scheduled workout %s on %s", workout_id, date_str)
    return result


@retry_garmin()
def update_workout(garmin: Garmin, workout_id: int, workout_json: dict) -> dict:
    """Update an existing Garmin Connect workout definition in-place via PUT.

    Updates only the workout definition (name, steps, targets). The calendar
    schedule entry is unchanged — the workout stays on the same date.
    Returns the response dict (may be empty on success).
    """
    result = garmin.garth.request(
        "PUT", "connectapi", f"/workout-service/workout/{workout_id}",
        api=True, json=workout_json,
    )
    logger.info("Updated workout %s", workout_id)
    return result or {}


@retry_garmin()
def delete_workout(garmin: Garmin, workout_id: int) -> None:
    """Delete a workout from the Garmin Connect workout library."""
    garmin.garth.request(
        "DELETE", "connectapi", f"/workout-service/workout/{workout_id}", api=True
    )
    logger.info("Deleted workout %s", workout_id)


@retry_garmin()
def remove_workout_schedule(garmin: Garmin, schedule_id: int) -> None:
    """Remove a workout calendar entry from Garmin Connect."""
    garmin.garth.request(
        "DELETE", "connectapi", f"/workout-service/schedule/{schedule_id}", api=True
    )
    logger.info("Removed schedule entry %s", schedule_id)


@retry_garmin()
def schedule_existing_workout(garmin: Garmin, workout_id: int, date_str: str) -> str | None:
    """Schedule a workout that already exists in the Garmin library onto the calendar.

    Used to repair the (workout_id set, schedule_id NULL) partial-sync state without
    re-uploading. Returns the schedule_id string, or None if the response had none.
    """
    result = garmin.schedule_workout(workout_id, date_str)
    schedule_id = None
    if isinstance(result, dict):
        schedule_id = result.get("workoutScheduleId") or result.get("scheduleId")
    logger.info("Scheduled existing workout %s on %s (schedule_id=%s)", workout_id, date_str, schedule_id)
    return str(schedule_id) if schedule_id else None


def get_garmin_calendar(garmin: Garmin, start_date: str, end_date: str) -> list[dict]:
    """Fetch scheduled workout entries from the Garmin Connect calendar for a date range.

    Returns a list of schedule entries; each entry typically contains
    workoutId, scheduleId, workoutName, and date fields.
    """
    try:
        result = garmin.connectapi(f"/workout-service/schedule/{start_date}/{end_date}")
        if isinstance(result, list):
            return result
        return []
    except Exception as e:
        if "404" in str(e):
            # 404 = no workouts scheduled for this range — normal for empty/future weeks
            logger.debug("No Garmin calendar entries for %s → %s", start_date, end_date)
        else:
            logger.warning("Could not read Garmin calendar (%s → %s): %s", start_date, end_date, e)
        return []


def validate_workout_details(garmin: Garmin, workout) -> dict:
    """Validate a single workout's details against Garmin Connect.

    Fetches the workout from Garmin and compares key fields against the DB workout.
    Returns a dict with validation results:
    - 'exists': bool — whether workout exists on Garmin
    - 'matches': bool — whether key details match
    - 'mismatches': list of str — descriptions of mismatches
    - 'garmin_data': dict — the fetched Garmin workout data (or None)
    """
    result = {
        'exists': False,
        'matches': False,
        'mismatches': [],
        'garmin_data': None
    }

    if not workout.garmin_workout_id:
        return result

    try:
        # Fetch workout details from Garmin
        garmin_workout = garmin.garth.connectapi(f"/workout-service/workout/{workout.garmin_workout_id}")
        result['garmin_data'] = garmin_workout
        result['exists'] = True

        # Extract key fields from Garmin data
        garmin_distance_m = garmin_workout.get('estimatedDistanceInMeters', 0)
        garmin_distance_km = garmin_distance_m / 1000.0 if garmin_distance_m else 0

        # Extract pace from workout steps (assuming first executable step has pace targets)
        garmin_pace_min_per_km = None
        workout_steps = garmin_workout.get('workoutSegments', [{}])[0].get('workoutSteps', [])
        for step in workout_steps:
            if step.get('type') == 'ExecutableStepDTO' and step.get('targetType', {}).get('workoutTargetTypeKey') == 'pace.zone':
                # Pace zones are in m/s, convert to min/km
                tv1 = step.get('targetValueOne')  # faster bound in m/s
                tv2 = step.get('targetValueTwo')  # slower bound in m/s
                if tv1 and tv2:
                    # Use average of the zone
                    avg_pace_ms = (tv1 + tv2) / 2
                    if avg_pace_ms > 0:
                        garmin_pace_min_per_km = 16.6667 / avg_pace_ms  # convert m/s to min/km
                break

        # Compare with DB values
        mismatches = []

        # Distance comparison (allow small tolerance)
        if workout.target_distance_km:
            db_distance = workout.target_distance_km
            if abs(garmin_distance_km - db_distance) > 0.1:  # 100m tolerance
                mismatches.append(f"Distance: DB={db_distance:.2f}km, Garmin={garmin_distance_km:.2f}km")

        # Pace comparison (allow small tolerance)
        if workout.target_pace_min_per_km and garmin_pace_min_per_km:
            db_pace = workout.target_pace_min_per_km
            if abs(garmin_pace_min_per_km - db_pace) > 0.1:  # 6s tolerance
                mismatches.append(f"Pace: DB={db_pace:.2f} min/km, Garmin={garmin_pace_min_per_km:.2f} min/km")

        result['mismatches'] = mismatches
        result['matches'] = len(mismatches) == 0

    except Exception as e:
        if "404" in str(e):
            result['exists'] = False
        else:
            logger.warning("Failed to validate workout %s details: %s", workout.garmin_workout_id, e)

    return result


@retry_garmin()
def get_garmin_workout_library(garmin: Garmin, limit: int = 999) -> list[dict]:
    """Return all workouts from the Garmin Connect workout library."""
    try:
        return garmin.get_workouts(start=0, limit=limit)
    except Exception as e:
        logger.warning("Could not fetch Garmin workout library: %s", e)
        return []


# ---------------------------------------------------------------------------
# Health data reads
# ---------------------------------------------------------------------------

def _retry_health_call(func, *args, retries: int = 2, **kwargs):
    """Call a Garmin health endpoint with retry on transient errors.

    Each health metric is individually retried so a single network blip
    doesn't silently drop an entire metric for the day.
    """
    import requests as _requests

    last_exc = None
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt < retries:
                error_str = str(e).lower()
                is_retryable = (
                    isinstance(e, (_requests.exceptions.ConnectionError,
                                   _requests.exceptions.Timeout))
                    or any(kw in error_str for kw in (
                        "429", "rate limit", "500", "502", "503", "504",
                        "connection", "timeout", "reset",
                    ))
                )
                if is_retryable:
                    delay = 2.0 * (2 ** attempt)
                    logger.warning(
                        "Health call %s retry %d/%d after: %s (%.1fs delay)",
                        func.__name__, attempt + 1, retries, e, delay,
                    )
                    time.sleep(delay)
                    continue
            raise
    raise last_exc  # unreachable, but keeps type checkers happy

def _has_sleep_data(resp) -> bool:
    """Return True if the sleep response contains actual sleep records."""
    if not resp:
        return False
    if isinstance(resp, dict):
        dto = resp.get("dailySleepDTO") or {}
        return bool(dto.get("sleepTimeSeconds") or dto.get("deepSleepSeconds"))
    return False


def _has_hrv_data(resp) -> bool:
    """Return True if the HRV response contains a usable lastNight score."""
    if not resp:
        return False
    if isinstance(resp, dict):
        summary = resp.get("hrvSummary") or {}
        return (summary.get("lastNightAvg") or summary.get("lastNight")) is not None
    return False


def _has_body_battery_data(resp) -> bool:
    """Return True if the body battery response contains at least one readable level."""
    if not resp or not isinstance(resp, list) or not resp:
        return False
    vals = resp[0].get("bodyBatteryValuesArray") or []
    return any(
        isinstance(v, (list, tuple)) and len(v) >= 2 and v[1] is not None
        for v in vals
    )


def get_health_snapshot(garmin: Garmin, date_str: str) -> dict:
    """Fetch all available health metrics for a given date.

    All metrics are fetched for the requested date only — no fallback to
    yesterday. The morning check-in retries every 30 minutes until data is
    available, so stale fallback data is never surfaced.

    Each metric is fetched independently with per-call retry logic — a single
    transient failure no longer silently drops a metric for the entire day.
    Exceptions are caught per field so a single failing endpoint does not
    block the others.

    Returns a dict with raw response values (None if unavailable).
    """
    raw: dict = {}

    try:
        sleep = _retry_health_call(garmin.get_sleep_data, date_str)
        raw["sleep"] = sleep if _has_sleep_data(sleep) else None
    except Exception as e:
        logger.warning("Sleep data unavailable for %s: %s", date_str, e)
        raw["sleep"] = None

    try:
        hrv = _retry_health_call(garmin.get_hrv_data, date_str)
        raw["hrv"] = hrv if _has_hrv_data(hrv) else None
    except Exception as e:
        logger.warning("HRV data unavailable for %s: %s", date_str, e)
        raw["hrv"] = None

    try:
        rhr = _retry_health_call(garmin.get_rhr_day, date_str)
        raw["rhr"] = rhr
        logger.debug("RHR raw response for %s: %s", date_str, rhr)
    except Exception as e:
        logger.warning("RHR data unavailable for %s: %s", date_str, e)
        raw["rhr"] = None

    try:
        bb = _retry_health_call(garmin.get_body_battery, date_str, date_str)
        raw["body_battery"] = bb if _has_body_battery_data(bb) else None
    except Exception as e:
        logger.warning("Body battery unavailable for %s: %s", date_str, e)
        raw["body_battery"] = None

    try:
        stress = _retry_health_call(garmin.get_stress_data, date_str)
        raw["stress"] = stress
    except Exception as e:
        logger.warning("Stress data unavailable for %s: %s", date_str, e)
        raw["stress"] = None

    try:
        steps = _retry_health_call(garmin.get_steps_data, date_str)
        raw["steps"] = steps
        logger.debug("Steps raw type/sample for %s: type=%s, first=%s",
                     date_str, type(steps).__name__,
                     steps[0] if isinstance(steps, list) and steps else steps)
    except Exception as e:
        logger.warning("Steps data unavailable for %s: %s", date_str, e)
        raw["steps"] = None

    try:
        spo2 = _retry_health_call(garmin.get_spo2_data, date_str)
        raw["spo2"] = spo2
    except Exception as e:
        logger.warning("SpO2 data unavailable for %s: %s", date_str, e)
        raw["spo2"] = None

    try:
        raw["training_readiness"] = _retry_health_call(
            garmin.get_morning_training_readiness, date_str
        )
    except Exception as e:
        logger.warning("Training readiness unavailable for %s: %s", date_str, e)
        raw["training_readiness"] = None

    return raw


# ---------------------------------------------------------------------------
# Activity polling
# ---------------------------------------------------------------------------

def fetch_historical_activities(
    garmin: Garmin,
    athlete_id: int,
    start_date: str,
    end_date: str,
    activity_type: str = "running",
) -> list[dict]:
    """Fetch historical activities for a date range (used for onboarding fitness assessment).

    Returns a list of activity summary dicts as returned by the Garmin API.
    """
    try:
        activities = garmin.get_activities_by_date(start_date, end_date, activity_type)
        logger.info(
            "Fetched %d historical %s activities for athlete %d (%s → %s)",
            len(activities), activity_type, athlete_id, start_date, end_date,
        )
        return activities
    except Exception as e:
        logger.error("Failed to fetch historical activities for athlete %d: %s", athlete_id, e)
        return []


def fetch_athlete_lthr(garmin: Garmin, athlete_id: int) -> int | None:
    """Fetch the athlete's Lactate Threshold Heart Rate from their Garmin profile.

    Returns the LTHR in bpm, or None if the call fails or no value is set.
    """
    try:
        data = garmin.get_lactate_threshold()
        lthr = (data.get("speed_and_heart_rate") or {}).get("heartRate")
        if lthr:
            logger.info("Fetched LTHR=%d bpm for athlete %d", lthr, athlete_id)
            return int(lthr)
    except Exception as e:
        logger.warning("Could not fetch LTHR for athlete %d: %s", athlete_id, e)
    return None


def fetch_activity_hr_zones(garmin: Garmin, activity_id: str, athlete_id: int) -> list[dict] | None:
    """Fetch Garmin's pre-computed HR zone breakdown for an activity.

    Returns a list of dicts with keys: zoneNumber (1-5), secsInZone, zoneLowBoundary (bpm).
    These are the exact numbers displayed in the Garmin Connect app and are based on
    the athlete's configured LTHR (not raw % max HR). Returns None on failure.
    """
    try:
        zones = garmin.get_activity_hr_in_timezones(activity_id)
        if zones:
            logger.debug("Fetched HR zones for activity %s athlete %d", activity_id, athlete_id)
            return zones
    except Exception as e:
        logger.warning("Could not fetch HR zones for activity %s athlete %d: %s", activity_id, athlete_id, e)
    return None


def poll_new_activities(garmin: Garmin, athlete_id: int, db_session) -> list[str]:
    """Return list of Garmin activity IDs not yet ingested for this athlete.

    Polls all activity types (running, hiking, skiing, tennis, etc.) from
    yesterday and today. Activities shorter than 10 minutes are skipped to
    avoid noise from auto-detected or accidental recordings.
    """
    from datetime import date, timedelta
    from running_coach_ai.database.models import CompletedWorkout

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    today = date.today().isoformat()

    # Fetch all activity types — not just running — so cross-training (skiing,
    # hiking, tennis, etc.) is captured and can inform fatigue/fitness context.
    try:
        activities = garmin.get_activities_by_date(yesterday, today)
    except Exception as e:
        logger.error("Failed to poll activities for athlete %d: %s", athlete_id, e)
        return []

    # Filter to activities with a meaningful duration (≥10 min) to skip noise.
    MIN_DURATION_SECONDS = 600
    activity_ids = [
        str(a["activityId"])
        for a in activities
        if "activityId" in a
        and (a.get("duration") or 0) >= MIN_DURATION_SECONDS
    ]

    # Exclude activities already fully ingested (duration_seconds not null = complete record).
    existing = {
        row.garmin_activity_id
        for row in db_session.query(CompletedWorkout.garmin_activity_id)
        .filter(
            CompletedWorkout.athlete_id == athlete_id,
            CompletedWorkout.duration_seconds.isnot(None),  # exclude incomplete stubs
        )
        .all()
    }

    new_ids = [aid for aid in activity_ids if aid not in existing]
    logger.info("Found %d new activities for athlete %d", len(new_ids), athlete_id)
    return new_ids
