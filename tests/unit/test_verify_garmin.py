"""Tests for !admin verify-garmin and --verify flag on resync.

_verify_garmin reads the live Garmin library and calendar and compares them
against upcoming DB planned workouts. It reports:
  ✅ in sync (in library + on calendar for the right date)
  ⚠️  in library but NOT on calendar
  ❌  missing from Garmin entirely
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


from running_coach_ai.slack.admin import _resync_garmin, _verify_garmin, handle_admin_command

TODAY = date.today()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _athlete(athlete_id: int = 1, slack_user_id: str = "U123"):
    a = MagicMock()
    a.id = athlete_id
    a.slack_user_id = slack_user_id
    a.name = "Alice"
    a.garmin_email = "alice@example.com"
    a.garmin_password_encrypted = b"enc"
    return a


def _workout(scheduled_date: date, workout_type: str = "easy", athlete_id: int = 1,
             garmin_schedule_id: str = None):
    w = MagicMock()
    w.athlete_id = athlete_id
    w.scheduled_date = scheduled_date
    w.workout_type = workout_type
    w.status = "planned"
    w.garmin_schedule_id = garmin_schedule_id  # explicitly set so truthiness is correct
    return w


def _db(athlete, workouts):
    from running_coach_ai.database.models import Athlete

    db = MagicMock()
    athlete_q = MagicMock()
    athlete_q.filter.return_value.first.return_value = athlete

    workout_q = MagicMock()
    workout_q.filter.return_value.order_by.return_value.all.return_value = workouts

    db.query.side_effect = lambda m: athlete_q if m is Athlete else workout_q
    return db


def _library_entry(athlete_id: int, scheduled_date: date, workout_id: int) -> dict:
    return {
        "workoutId": workout_id,
        "description": f"[rca:{athlete_id}:{scheduled_date.isoformat()}]",
    }


def _calendar_entry(workout_id: int, scheduled_date: date) -> dict:
    return {
        "workoutId": workout_id,
        "workoutScheduleId": workout_id + 1000,
        "date": scheduled_date.isoformat(),
    }


def _run_verify(athlete, workouts, library, calendar):
    db = _db(athlete, workouts)
    with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
         patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=library), \
         patch("running_coach_ai.garmin.client.get_garmin_calendar", return_value=calendar):
        mock_auth.return_value = MagicMock()
        result = _verify_garmin("U123", None, db)
    return result


# ---------------------------------------------------------------------------
# _verify_garmin: result classification
# ---------------------------------------------------------------------------

class TestVerifyGarminClassification:

    def test_workout_in_library_and_calendar_is_synced(self):
        """Workout in library + garmin_schedule_id in DB = ✅ in sync."""
        d = TODAY + timedelta(days=3)
        athlete = _athlete()
        workouts = [_workout(d, garmin_schedule_id="1100")]
        library = [_library_entry(1, d, 100)]
        calendar = [_calendar_entry(100, d)]

        result = _run_verify(athlete, workouts, library, calendar)

        assert "✅" in result
        assert "1" in result  # 1 in sync

    def test_workout_in_library_but_not_calendar_is_warned(self):
        """Workout uploaded to library but not scheduled on calendar = ⚠️."""
        d = TODAY + timedelta(days=3)
        athlete = _athlete()
        workouts = [_workout(d)]
        library = [_library_entry(1, d, 100)]
        calendar = []  # nothing scheduled

        result = _run_verify(athlete, workouts, library, calendar)

        assert "⚠️" in result
        assert d.isoformat() in result or str(d) in result

    def test_workout_missing_from_garmin_entirely_is_flagged(self):
        """Workout not in library at all = ❌ missing."""
        d = TODAY + timedelta(days=3)
        athlete = _athlete()
        workouts = [_workout(d)]
        library = []  # nothing uploaded
        calendar = []

        result = _run_verify(athlete, workouts, library, calendar)

        assert "❌" in result
        assert d.isoformat() in result or str(d) in result

    def test_mixed_statuses_reported_correctly(self):
        """Three workouts: one synced, one library-only, one missing."""
        d1 = TODAY + timedelta(days=2)
        d2 = TODAY + timedelta(days=5)
        d3 = TODAY + timedelta(days=9)
        athlete = _athlete()
        workouts = [
            _workout(d1, garmin_schedule_id="1100"),  # in library + scheduled
            _workout(d2),                              # in library but garmin_schedule_id=None
            _workout(d3),                              # not in library at all
        ]
        library = [
            _library_entry(1, d1, 100),
            _library_entry(1, d2, 200),
            # d3 not in library at all
        ]
        calendar = []  # calendar no longer used for verification

        result = _run_verify(athlete, workouts, library, calendar)

        assert "✅" in result and "1" in result   # 1 synced
        assert "⚠️" in result                    # library only
        assert "❌" in result                    # missing

    def test_all_in_sync_message_when_clean(self):
        """When everything matches (library + garmin_schedule_id set), report all-in-sync."""
        dates = [TODAY + timedelta(days=i + 1) for i in range(3)]
        athlete = _athlete()
        workouts = [_workout(d, garmin_schedule_id=str(100 + i + 1000)) for i, d in enumerate(dates)]
        library = [_library_entry(1, d, 100 + i) for i, d in enumerate(dates)]
        calendar = [_calendar_entry(100 + i, d) for i, d in enumerate(dates)]

        result = _run_verify(athlete, workouts, library, calendar)

        assert "✅" in result
        assert "❌" not in result
        assert "⚠️" not in result
        assert "in sync" in result.lower()

    def test_library_entry_wrong_athlete_id_ignored(self):
        """Library entries with a different athlete_id must not count."""
        d = TODAY + timedelta(days=3)
        athlete = _athlete(athlete_id=1)
        workouts = [_workout(d, athlete_id=1)]
        # Entry belongs to athlete 99, not 1
        library = [{"workoutId": 100, "description": f"[rca:99:{d.isoformat()}]"}]
        calendar = [_calendar_entry(100, d)]

        result = _run_verify(athlete, workouts, library, calendar)

        assert "❌" in result  # not found (wrong athlete marker)

    def test_no_upcoming_workouts_returns_early(self):
        """No upcoming workouts in DB: return early with informative message."""
        athlete = _athlete()
        db = _db(athlete, [])
        with patch("running_coach_ai.garmin.client.get_garmin_client"):
            result = _verify_garmin("U123", None, db)
        assert "no upcoming" in result.lower()

    def test_no_garmin_credentials_returns_error(self):
        """Athlete with no Garmin credentials: return error without calling API."""
        athlete = _athlete()
        athlete.garmin_email = None
        athlete.garmin_password_encrypted = None
        db = _db(athlete, [_workout(TODAY + timedelta(days=2))])

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth:
            result = _verify_garmin("U123", None, db)
        mock_auth.assert_not_called()
        assert "credentials" in result.lower() or "no garmin" in result.lower()

    def test_mismatch_suggests_resync_command(self):
        """When mismatches found, the response must suggest the fix command."""
        d = TODAY + timedelta(days=3)
        athlete = _athlete()
        workouts = [_workout(d)]
        result = _run_verify(athlete, workouts, [], [])  # missing from Garmin

        assert "resync-garmin" in result

    def test_db_schedule_id_determines_sync_status(self):
        """Sync status is determined by garmin_schedule_id in DB, not calendar API.
        A workout in the library with garmin_schedule_id set = ✅;
        without garmin_schedule_id = ⚠️."""
        d1 = TODAY + timedelta(days=2)
        d2 = TODAY + timedelta(days=9)
        athlete = _athlete()
        workouts = [
            _workout(d1, garmin_schedule_id="1100"),  # in library + schedule ID → ✅
            _workout(d2),                              # in library, no schedule ID → ⚠️
        ]
        library = [
            _library_entry(1, d1, 100),
            _library_entry(1, d2, 200),
        ]
        db = _db(athlete, workouts)
        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=library):
            mock_auth.return_value = MagicMock()
            result = _verify_garmin("U123", None, db)

        assert "✅" in result
        assert "⚠️" in result
        assert "❌" not in result

    def test_large_plan_workouts_correctly_verified(self):
        """19-week plan (102 workouts): all must be ✅ when library entries exist and
        garmin_schedule_id is set in the DB."""
        dates = [TODAY + timedelta(days=i) for i in range(1, 134) if i % 7 != 0]  # 6 days/week, 19 weeks
        dates = dates[:102]  # exactly 102 workouts
        athlete = _athlete(athlete_id=1)
        workouts = [_workout(d, athlete_id=1, garmin_schedule_id=str(100 + i + 1000))
                    for i, d in enumerate(dates)]
        library = [_library_entry(1, d, 100 + i) for i, d in enumerate(dates)]

        db = _db(athlete, workouts)
        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_workout_library", return_value=library):
            mock_auth.return_value = MagicMock()
            result = _verify_garmin("U123", None, db)

        assert "✅" in result
        assert "⚠️" not in result
        assert "❌" not in result
        assert "in sync" in result.lower()


# ---------------------------------------------------------------------------
# --verify flag on _resync_garmin
# ---------------------------------------------------------------------------

class TestResyncWithVerify:

    def _resync_db(self, athlete, workouts):
        """DB mock tailored for _resync_garmin's query patterns.

        _resync_garmin queries:
          1. Athlete via .filter().first()
          2. PlannedWorkout.scheduled_date via .filter().distinct().all()
          3. PlannedWorkout rows via .filter().filter()...all()  (bulk update)
        """
        from running_coach_ai.database.models import Athlete

        db = MagicMock()

        athlete_q = MagicMock()
        athlete_q.filter.return_value.first.return_value = athlete

        # date rows — _resync_garmin uses .distinct().all()
        date_rows = [MagicMock(scheduled_date=w.scheduled_date) for w in workouts]
        workout_q = MagicMock()
        terminal = MagicMock()
        terminal.all.return_value = date_rows
        terminal.distinct.return_value = terminal
        terminal.filter.return_value = terminal
        terminal.update.return_value = len(workouts)
        workout_q.filter.return_value = terminal
        terminal.filter.return_value = terminal

        db.query.side_effect = lambda m: athlete_q if m is Athlete else workout_q
        db.commit.return_value = None
        return db

    def _run_resync_verify(self, workouts):
        athlete = _athlete()
        db = self._resync_db(athlete, workouts)

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_calendar", return_value=[]), \
             patch("running_coach_ai.garmin.client.remove_workout_schedule"), \
             patch("running_coach_ai.garmin.client.delete_workout"), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", return_value=(len(workouts), 0)), \
             patch("running_coach_ai.slack.admin._verify_garmin", return_value="✅ Verify result") as mock_verify:
            mock_auth.return_value = MagicMock()
            result = _resync_garmin("U123", None, db, confirmed=True, verify=True)

        return result, mock_verify

    def test_verify_called_after_resync(self):
        """With --verify flag, _verify_garmin must be called after sync completes."""
        w = _workout(TODAY + timedelta(days=3))
        _, mock_verify = self._run_resync_verify([w])
        mock_verify.assert_called_once()

    def test_verify_result_appended_to_resync_output(self):
        """The verify output must appear in the combined response."""
        w = _workout(TODAY + timedelta(days=3))
        result, _ = self._run_resync_verify([w])
        assert "✅ Verify result" in result

    def test_no_verify_flag_skips_verify(self):
        """Without --verify, _verify_garmin must NOT be called."""
        w = _workout(TODAY + timedelta(days=3))
        athlete = _athlete()
        db = self._resync_db(athlete, [w])

        with patch("running_coach_ai.garmin.client.get_garmin_client") as mock_auth, \
             patch("running_coach_ai.garmin.client.get_garmin_calendar", return_value=[]), \
             patch("running_coach_ai.garmin.client.remove_workout_schedule"), \
             patch("running_coach_ai.garmin.client.delete_workout"), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin", return_value=(1, 0, [])), \
             patch("running_coach_ai.slack.admin._verify_garmin") as mock_verify:
            mock_auth.return_value = MagicMock()
            _resync_garmin("U123", None, db, confirmed=True, verify=False)

        mock_verify.assert_not_called()


# ---------------------------------------------------------------------------
# handle_admin_command routing
# ---------------------------------------------------------------------------

class TestAdminCommandRouting:

    def _settings_patch(self):
        return patch("running_coach_ai.slack.admin.settings")

    def test_verify_garmin_command_routes_to_verify(self):
        """`!admin verify-garmin` must call _verify_garmin."""
        db = MagicMock()
        with self._settings_patch() as mock_settings, \
             patch("running_coach_ai.slack.admin._verify_garmin", return_value="verified") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            result = handle_admin_command("ADMIN1", "!admin verify-garmin", db)
        mock_fn.assert_called_once()

    def test_resync_confirm_verify_passes_verify_true(self):
        """`!admin resync-garmin --confirm --verify` must pass verify=True."""
        db = MagicMock()
        with self._settings_patch() as mock_settings, \
             patch("running_coach_ai.slack.admin._resync_garmin", return_value="done") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin resync-garmin --confirm --verify", db)
        mock_fn.assert_called_once()
        assert mock_fn.call_args.kwargs.get("verify") is True
        assert mock_fn.call_args.kwargs.get("confirmed") is True

    def test_resync_confirm_no_verify_passes_verify_false(self):
        """`!admin resync-garmin --confirm` (no --verify) must pass verify=False."""
        db = MagicMock()
        with self._settings_patch() as mock_settings, \
             patch("running_coach_ai.slack.admin._resync_garmin", return_value="done") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin resync-garmin --confirm", db)
        assert mock_fn.call_args.kwargs.get("verify") is False

    def test_verify_garmin_with_user_id(self):
        """`!admin verify-garmin <@U456>` passes the target user_id."""
        db = MagicMock()
        with self._settings_patch() as mock_settings, \
             patch("running_coach_ai.slack.admin._verify_garmin", return_value="ok") as mock_fn:
            mock_settings.ADMIN_SLACK_USER_ID = "ADMIN1"
            handle_admin_command("ADMIN1", "!admin verify-garmin <@U456>", db)
        # second positional arg is target_user_id
        assert mock_fn.call_args[0][1] == "U456"
