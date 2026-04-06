"""Integration test: Garmin Connect workout create / update / delete lifecycle.

Tests real API calls against the Garmin Connect service. All workouts are
scheduled far enough in the future to avoid conflicts with active training plans,
and are cleaned up after each test.

Required environment variables:
  GARMIN_TEST_EMAIL      — Garmin account email
  ENCRYPTION_KEY         — Fernet key (loaded automatically from .env)
  GARMIN_SESSION_DIR     — directory for garth OAuth token cache (loaded from .env)

Optional:
  GARMIN_TEST_ATHLETE_ID — numeric athlete ID for the token directory (default: 1)
  GARMIN_TEST_PASSWORD   — plaintext password; only needed when the cached session
                           has expired and a full re-auth is required

Run with:
  GARMIN_TEST_EMAIL=you@example.com pytest tests/integration/test_garmin_integration.py -v -s

Notes:
  - MFA must be disabled on the Garmin account (Garmin Connect limitation).
  - Tests are automatically skipped when GARMIN_TEST_EMAIL is not set.
  - get_garmin_client uses the cached garth OAuth tokens first; the password is
    only needed when those tokens have expired.
  - Each test uses a distinct future date (+60–63 days) to avoid conflicts.
"""

import os
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

# Skip the entire module unless the test account is configured
pytestmark = pytest.mark.skipif(
    not os.getenv("GARMIN_TEST_EMAIL"),
    reason="GARMIN_TEST_EMAIL not set — skipping Garmin integration tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def athlete_id():
    return int(os.getenv("GARMIN_TEST_ATHLETE_ID", "1"))


@pytest.fixture(scope="module")
def garmin_client(athlete_id):
    """Return an authenticated Garmin client.

    Uses the cached garth OAuth tokens for the given athlete_id first.
    Falls back to full re-auth if the cache is expired — requires
    GARMIN_TEST_PASSWORD to be set in that case.
    """
    from running_coach_ai.garmin.client import encrypt_password, get_garmin_client

    email = os.environ["GARMIN_TEST_EMAIL"]

    # Password is only used when the cached session has expired.
    # Encrypt a dummy value so get_garmin_client can always be called;
    # it will only decrypt and use it on a cache miss.
    raw_password = os.getenv("GARMIN_TEST_PASSWORD", "unused_cache_hit")
    encrypted = encrypt_password(raw_password)

    return get_garmin_client(athlete_id, email, encrypted)


def _test_planned_workout(athlete_id: int, scheduled_date: date) -> MagicMock:
    """Build a minimal PlannedWorkout mock for use with build_workout_json."""
    pw = MagicMock()
    pw.athlete_id = athlete_id
    pw.scheduled_date = scheduled_date
    pw.workout_type = "easy"
    pw.description = "Integration test — safe to delete"
    pw.target_distance_km = 5.0
    pw.target_pace_min_per_km = 6.5
    pw.target_zones_json = None
    pw.workout_name = None
    return pw


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGarminWorkoutLifecycle:
    """Create / update / delete lifecycle against real Garmin Connect."""

    def test_upload_workout_appears_in_library(self, garmin_client, athlete_id):
        """Upload a workout and verify it appears in the library with the app marker."""
        from running_coach_ai.garmin.client import (
            delete_workout,
            get_garmin_workout_library,
            upload_workout,
        )
        from running_coach_ai.garmin.workout_builder import APP_MARKER_RE, build_workout_json

        test_date = date.today() + timedelta(days=60)
        pw = _test_planned_workout(athlete_id, test_date)
        workout_json = build_workout_json(pw)

        result = upload_workout(garmin_client, workout_json)
        workout_id = result.get("workoutId")
        assert workout_id, f"Expected workoutId in response: {result}"

        try:
            library = get_garmin_workout_library(garmin_client)
            found = [e for e in library if e.get("workoutId") == workout_id]
            assert found, f"Workout {workout_id} not found in library after upload"

            desc = found[0].get("description", "")
            assert APP_MARKER_RE.search(desc), (
                f"App marker [rca:...] missing from description: {desc!r}"
            )
        finally:
            delete_workout(garmin_client, workout_id)

    def test_update_workout_preserves_library_entry(self, garmin_client, athlete_id):
        """Upload a workout, update its definition in-place, verify it still exists."""
        from running_coach_ai.garmin.client import (
            delete_workout,
            get_garmin_workout_library,
            update_workout,
            upload_workout,
        )
        from running_coach_ai.garmin.workout_builder import build_workout_json

        test_date = date.today() + timedelta(days=61)
        pw = _test_planned_workout(athlete_id, test_date)
        workout_json = build_workout_json(pw)

        result = upload_workout(garmin_client, workout_json)
        workout_id = result.get("workoutId")
        assert workout_id

        try:
            # Update: change description and distance
            pw.target_distance_km = 8.0
            pw.description = "Updated integration test description"
            updated_json = build_workout_json(pw)

            # update_workout must not raise
            update_workout(garmin_client, workout_id, updated_json)

            # The workout must still exist in the library (not deleted by update)
            library = get_garmin_workout_library(garmin_client)
            found = [e for e in library if e.get("workoutId") == workout_id]
            assert found, (
                f"Workout {workout_id} disappeared from library after update — "
                "update_workout must not delete the entry"
            )
        finally:
            delete_workout(garmin_client, workout_id)

    def test_delete_workout_removes_from_library(self, garmin_client, athlete_id):
        """Upload a workout, delete it, confirm it no longer appears in the library."""
        from running_coach_ai.garmin.client import (
            delete_workout,
            get_garmin_workout_library,
            upload_workout,
        )
        from running_coach_ai.garmin.workout_builder import build_workout_json

        test_date = date.today() + timedelta(days=62)
        pw = _test_planned_workout(athlete_id, test_date)
        workout_json = build_workout_json(pw)

        result = upload_workout(garmin_client, workout_json)
        workout_id = result.get("workoutId")
        assert workout_id

        delete_workout(garmin_client, workout_id)

        library = get_garmin_workout_library(garmin_client)
        found = [e for e in library if e.get("workoutId") == workout_id]
        assert not found, (
            f"Workout {workout_id} still in library after delete"
        )

    def test_schedule_and_unschedule_workout(self, garmin_client, athlete_id):
        """Upload, schedule, verify via schedule ID, unschedule, verify removed."""
        from running_coach_ai.garmin.client import (
            delete_workout,
            remove_workout_schedule,
            schedule_workout,
            upload_workout,
        )
        from running_coach_ai.garmin.workout_builder import build_workout_json

        test_date = date.today() + timedelta(days=63)
        pw = _test_planned_workout(athlete_id, test_date)
        workout_json = build_workout_json(pw)

        result = upload_workout(garmin_client, workout_json)
        workout_id = result.get("workoutId")
        assert workout_id

        schedule_id = None
        try:
            sched_result = schedule_workout(garmin_client, workout_id, test_date.isoformat())
            schedule_id = (
                sched_result.get("workoutScheduleId") or sched_result.get("scheduleId")
                if isinstance(sched_result, dict) else None
            )
            assert schedule_id, f"No schedule_id in response: {sched_result}"

            # Verify the schedule entry exists by its ID
            scheduled = garmin_client.get_scheduled_workout_by_id(schedule_id)
            assert scheduled, f"Schedule entry {schedule_id} not found after scheduling"

            # Unschedule
            remove_workout_schedule(garmin_client, schedule_id)

            # Verify the schedule entry no longer exists (expect 404)
            try:
                still_there = garmin_client.get_scheduled_workout_by_id(schedule_id)
                assert not still_there, (
                    f"Schedule entry {schedule_id} still returned after remove_workout_schedule"
                )
            except Exception as e:
                assert "404" in str(e), (
                    f"Expected 404 after unschedule, got unexpected error: {e}"
                )
        finally:
            delete_workout(garmin_client, workout_id)
