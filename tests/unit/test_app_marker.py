"""Tests for the [rca:{athlete_id}:{date}] app marker used to prevent duplicate Garmin workouts.

The marker is embedded in every uploaded workout's description. sync_week_to_garmin scans
the Garmin library for it and deletes stale entries before re-uploading, eliminating the
duplicate-workout problem caused by stale stored IDs.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


from running_coach_ai.garmin.workout_builder import APP_MARKER_RE, build_workout_json, sync_week_to_garmin


TODAY = date.today()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _workout(
    athlete_id: int = 1,
    scheduled_date: date = None,
    workout_type: str = "easy",
    description: str = None,
    target_distance_km: float = 8.0,
    target_pace_min_per_km: float = 6.0,
    garmin_workout_id: str = None,
    garmin_schedule_id: str = None,
    status: str = "planned",
):
    w = MagicMock()
    w.athlete_id = athlete_id
    w.scheduled_date = scheduled_date or (TODAY + timedelta(days=3))
    w.workout_type = workout_type
    w.description = description
    w.target_distance_km = target_distance_km
    w.target_pace_min_per_km = target_pace_min_per_km
    w.target_zones_json = None
    w.workout_name = None
    w.garmin_workout_id = garmin_workout_id
    w.garmin_schedule_id = garmin_schedule_id
    w.status = status
    return w


def _db_for_sync(athlete_id: int, workout_objs: list, week_start: date):
    """DB mock wired for sync_week_to_garmin.

    sync_week_to_garmin makes three kinds of db.query calls:
      1. query(PlannedWorkout).filter(...).all()         — the week's workouts
      2. query(PlannedWorkout.target_pace_min_per_km)... — fallback pace (per workout type)
      3. db_session.commit()
    """
    db = MagicMock()

    # Build a single chainable mock that handles both query patterns:
    # - .all() → workout_objs (week query)
    # - .scalar() → None  (fallback pace query)
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.limit.return_value = chain
    chain.all.return_value = workout_objs
    chain.scalar.return_value = None  # no fallback paces needed

    db.query.return_value = chain
    # db_session.commit() is a no-op
    db.commit.return_value = None
    return db


# ---------------------------------------------------------------------------
# build_workout_json: marker in description
# ---------------------------------------------------------------------------

class TestAppMarkerInDescription:

    def test_marker_is_present_in_description(self):
        """build_workout_json must include the [rca:...] marker in the description."""
        w = _workout(athlete_id=7, scheduled_date=TODAY + timedelta(days=5))
        result = build_workout_json(w)
        desc = result["description"]
        assert APP_MARKER_RE.search(desc), f"No marker found in description: {desc!r}"

    def test_marker_encodes_athlete_id(self):
        """Marker must contain the correct athlete_id."""
        w = _workout(athlete_id=42, scheduled_date=TODAY + timedelta(days=2))
        result = build_workout_json(w)
        m = APP_MARKER_RE.search(result["description"])
        assert m and int(m.group(1)) == 42

    def test_marker_encodes_scheduled_date(self):
        """Marker must contain the workout's scheduled_date in YYYY-MM-DD format."""
        target_date = TODAY + timedelta(days=10)
        w = _workout(athlete_id=1, scheduled_date=target_date)
        result = build_workout_json(w)
        m = APP_MARKER_RE.search(result["description"])
        assert m and m.group(2) == target_date.isoformat()

    def test_original_description_is_preserved(self):
        """When the workout has a description, it must appear after the marker."""
        w = _workout(description="Go easy, focus on form")
        result = build_workout_json(w)
        desc = result["description"]
        assert "Go easy, focus on form" in desc
        # Marker must come before the original description
        marker_end = APP_MARKER_RE.search(desc).end()
        assert desc.index("Go easy") > marker_end

    def test_no_description_gives_marker_only(self):
        """Workout with no description: description field is just the marker."""
        w = _workout(description=None)
        result = build_workout_json(w)
        desc = result["description"]
        m = APP_MARKER_RE.fullmatch(desc.strip())
        assert m, f"Expected only marker, got: {desc!r}"

    def test_workout_name_is_unchanged(self):
        """The marker must NOT appear in workoutName — only in description."""
        w = _workout(workout_type="easy")
        result = build_workout_json(w)
        assert "[rca:" not in result["workoutName"]

    def test_marker_format_matches_regex(self):
        """Every built workout's description must be parseable by APP_MARKER_RE."""
        for wtype in ("easy", "long_run", "tempo", "intervals"):
            w = _workout(workout_type=wtype, athlete_id=3, scheduled_date=TODAY + timedelta(days=1))
            result = build_workout_json(w)
            assert APP_MARKER_RE.search(result["description"]), f"No marker for {wtype}"


# ---------------------------------------------------------------------------
# sync_week_to_garmin: library scan deletes stale duplicates
# ---------------------------------------------------------------------------

class TestSyncLibraryScan:

    # Patch paths: functions are imported at module level in workout_builder.py,
    # so patches must target the workout_builder namespace, not garmin.client.
    _MOD = "running_coach_ai.garmin.workout_builder"

    def _run_sync(self, library_entries, workouts, upload_return=None):
        """Helper: run sync_week_to_garmin with mocked Garmin API."""
        athlete_id = 1
        week_start = TODAY - timedelta(days=TODAY.weekday())
        db = _db_for_sync(athlete_id, workouts, week_start)

        upload_return = upload_return or {"workoutId": 9999}
        sched_return = {"workoutScheduleId": 8888}

        with patch(f"{self._MOD}.get_garmin_client") as mock_auth, \
             patch(f"{self._MOD}.get_garmin_workout_library", return_value=library_entries) as mock_lib, \
             patch(f"{self._MOD}.remove_workout_schedule") as mock_rm_sched, \
             patch(f"{self._MOD}.delete_workout") as mock_del, \
             patch(f"{self._MOD}.upload_workout", return_value=upload_return) as mock_up, \
             patch(f"{self._MOD}.schedule_workout", return_value=sched_return):
            mock_auth.return_value = MagicMock()
            result = sync_week_to_garmin(
                athlete_id, "test@example.com", b"enc", week_start, db
            )

        return result, mock_lib, mock_rm_sched, mock_del, mock_up

    def test_library_is_fetched_once_per_sync(self):
        """sync_week_to_garmin must fetch the workout library exactly once."""
        w = _workout(scheduled_date=TODAY + timedelta(days=1))
        _, mock_lib, _, _, _ = self._run_sync([], [w])
        mock_lib.assert_called_once()

    def test_schedule_removed_via_stored_db_id(self):
        """When a workout has garmin_schedule_id set, remove_workout_schedule is called on re-sync."""
        target_date = TODAY + timedelta(days=1)
        w = _workout(scheduled_date=target_date, garmin_workout_id="500", garmin_schedule_id="600")
        _, _, mock_rm, _, _ = self._run_sync([], [w])
        removed_sids = [c[0][1] for c in mock_rm.call_args_list]
        assert 600 in removed_sids

    def test_stale_library_entry_with_marker_is_deleted(self):
        """A library entry with the matching marker must be deleted before re-upload."""
        target_date = TODAY + timedelta(days=2)
        stale_wid = 777
        marker = f"[rca:1:{target_date.isoformat()}]"
        library = [{"workoutId": stale_wid, "description": marker}]

        w = _workout(athlete_id=1, scheduled_date=target_date)
        _, _, _, mock_del, _ = self._run_sync(library, [w])

        mock_del.assert_any_call(mock_del.call_args_list[0][0][0], stale_wid)

    def test_stale_entry_with_schedule_gets_schedule_removed(self):
        """When the workout being replaced has a stored schedule ID, that schedule is removed."""
        target_date = TODAY + timedelta(days=2)
        stale_wid = 777
        stale_sid = 555
        marker = f"[rca:1:{target_date.isoformat()}]"
        library = [{"workoutId": stale_wid, "description": marker}]

        # Workout has the stored IDs that map to the stale library entry
        w = _workout(athlete_id=1, scheduled_date=target_date,
                     garmin_workout_id=str(stale_wid), garmin_schedule_id=str(stale_sid))
        _, _, mock_rm, mock_del, _ = self._run_sync(library, [w])

        removed_sids = [c[0][1] for c in mock_rm.call_args_list]
        assert stale_sid in removed_sids
        mock_del.assert_called()

    def test_library_entry_for_different_athlete_is_not_deleted(self):
        """Marker with a different athlete_id must not be deleted."""
        target_date = TODAY + timedelta(days=2)
        # athlete_id=99, but we're syncing for athlete_id=1
        library = [{"workoutId": 777, "description": f"[rca:99:{target_date.isoformat()}]"}]

        w = _workout(athlete_id=1, scheduled_date=target_date)
        _, _, mock_rm, mock_del, _ = self._run_sync(library, [w])

        # Only the fallback stored-ID attempt would be made; since w has no stored IDs,
        # neither delete nor rm should be called.
        mock_del.assert_not_called()
        mock_rm.assert_not_called()

    def test_library_entry_for_different_date_is_not_deleted(self):
        """Marker on a different date must not be deleted during this date's sync."""
        target_date = TODAY + timedelta(days=2)
        other_date = TODAY + timedelta(days=5)
        library = [{"workoutId": 777, "description": f"[rca:1:{other_date.isoformat()}]"}]

        w = _workout(athlete_id=1, scheduled_date=target_date)
        _, _, mock_rm, mock_del, _ = self._run_sync(library, [w])

        mock_del.assert_not_called()

    def test_library_entry_with_no_description_is_skipped(self):
        """Library entries without a description must be skipped gracefully."""
        target_date = TODAY + timedelta(days=2)
        library = [{"workoutId": 777}]  # no description key

        w = _workout(athlete_id=1, scheduled_date=target_date)
        # Should not raise
        _, _, mock_rm, mock_del, _ = self._run_sync(library, [w])
        mock_del.assert_not_called()

    def test_library_entry_with_none_description_is_skipped(self):
        """Library entries with description=None must be skipped gracefully."""
        target_date = TODAY + timedelta(days=2)
        library = [{"workoutId": 777, "description": None}]

        w = _workout(athlete_id=1, scheduled_date=target_date)
        _, _, _, mock_del, _ = self._run_sync(library, [w])
        mock_del.assert_not_called()

    def test_upload_proceeds_after_stale_delete(self):
        """After deleting the stale entry, the fresh workout must still be uploaded."""
        target_date = TODAY + timedelta(days=2)
        library = [{"workoutId": 777, "description": f"[rca:1:{target_date.isoformat()}]"}]

        w = _workout(athlete_id=1, scheduled_date=target_date)
        _, _, _, _, mock_up = self._run_sync(library, [w])

        mock_up.assert_called_once()

    def test_multiple_stale_entries_same_date_all_deleted(self):
        """Two stale entries for the same date (prior double-upload) must both be deleted."""
        target_date = TODAY + timedelta(days=2)
        marker = f"[rca:1:{target_date.isoformat()}]"
        library = [
            {"workoutId": 100, "description": marker},
            {"workoutId": 101, "description": marker},
        ]

        w = _workout(athlete_id=1, scheduled_date=target_date)
        _, _, _, mock_del, _ = self._run_sync(library, [w])

        deleted_ids = [c[0][1] for c in mock_del.call_args_list]
        assert 100 in deleted_ids
        assert 101 in deleted_ids

    def test_stale_delete_failure_does_not_abort_upload(self):
        """If deleting a stale entry fails, upload must still proceed."""
        target_date = TODAY + timedelta(days=2)
        library = [{"workoutId": 777, "description": f"[rca:1:{target_date.isoformat()}]"}]

        w = _workout(athlete_id=1, scheduled_date=target_date)
        athlete_id = 1
        week_start = TODAY - timedelta(days=TODAY.weekday())
        db = _db_for_sync(athlete_id, [w], week_start)

        _M = "running_coach_ai.garmin.workout_builder"
        with patch(f"{_M}.get_garmin_client") as mock_auth, \
             patch(f"{_M}.get_garmin_workout_library", return_value=library), \
             patch(f"{_M}.remove_workout_schedule"), \
             patch(f"{_M}.delete_workout", side_effect=Exception("500")), \
             patch(f"{_M}.upload_workout", return_value={"workoutId": 999}) as mock_up, \
             patch(f"{_M}.schedule_workout", return_value={"workoutScheduleId": 888}):
            mock_auth.return_value = MagicMock()
            uploaded, failed, _ = sync_week_to_garmin(
                athlete_id, "test@example.com", b"enc", week_start, db
            )

        mock_up.assert_called_once()
        assert uploaded == 1

    def test_legacy_stored_id_still_deleted_as_fallback(self):
        """Workout with stored garmin_workout_id (pre-marker) must also have delete attempted."""
        target_date = TODAY + timedelta(days=2)
        legacy_wid = 456
        # Library has no marker (legacy workout) but DB has the stored ID
        w = _workout(
            athlete_id=1,
            scheduled_date=target_date,
            garmin_workout_id=str(legacy_wid),
        )
        _, _, _, mock_del, _ = self._run_sync([], [w])

        deleted_ids = [c[0][1] for c in mock_del.call_args_list]
        assert legacy_wid in deleted_ids
