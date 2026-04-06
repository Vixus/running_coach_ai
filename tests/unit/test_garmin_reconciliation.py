"""Tests for the daily Garmin reconciliation job (_run_garmin_reconciliation).

The reconciliation job scans all athletes for future workouts missing Garmin IDs
and calls sync_week_to_garmin for each affected week. It is the self-healing
guarantee that any gap left by a failed event-driven sync is repaired within 24h.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


from running_coach_ai.scheduler.jobs import _run_garmin_reconciliation

TODAY = date.today()
_JOBS = "running_coach_ai.scheduler.jobs"
_SESSION = "running_coach_ai.database.session.get_session"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _athlete(athlete_id: int = 1, has_garmin: bool = True):
    a = MagicMock()
    a.id = athlete_id
    a.name = f"Athlete{athlete_id}"
    a.garmin_email = f"athlete{athlete_id}@example.com" if has_garmin else None
    a.garmin_password_encrypted = b"enc" if has_garmin else None
    a.allowed = True
    a.onboarding_complete = True
    return a


def _workout(athlete_id: int, days_ahead: int, garmin_workout_id=None, garmin_schedule_id=None):
    w = MagicMock()
    w.athlete_id = athlete_id
    w.scheduled_date = TODAY + timedelta(days=days_ahead)
    w.garmin_workout_id = garmin_workout_id
    w.garmin_schedule_id = garmin_schedule_id
    return w


def _make_db(athletes, unsynced_by_athlete):
    """Build a DB mock that returns the right athletes and unsynced workouts per athlete."""
    from running_coach_ai.database.models import Athlete

    db = MagicMock()
    athlete_q = MagicMock()
    athlete_q.filter.return_value.all.return_value = athletes

    def _workout_q_for(athlete_id):
        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = unsynced_by_athlete.get(athlete_id, [])
        return q

    call_count = [0]

    def _side(model):
        if model is Athlete:
            return athlete_q
        # PlannedWorkout queries — route by call order to athlete_id
        call_count[0] += 1
        for athlete in athletes:
            if call_count[0] <= athletes.index(athlete) + 1:
                return _workout_q_for(athlete.id)
        return _workout_q_for(athletes[-1].id)

    db.query.side_effect = _side
    db.commit.return_value = None
    return db


def _run(athletes, unsynced_by_athlete, sync_return=(1, 0, [])):
    """Run reconciliation with mocked DB and sync functions."""
    db = MagicMock()

    from running_coach_ai.database.models import Athlete

    athlete_q = MagicMock()
    athlete_q.filter.return_value.all.return_value = athletes

    # For each athlete, build an unsynced workout query
    athlete_index = [0]
    workout_calls = []

    def _side(model):
        if model is Athlete:
            return athlete_q
        q = MagicMock()
        q.filter.return_value = q
        idx = athlete_index[0]
        if idx < len(athletes):
            q.all.return_value = unsynced_by_athlete.get(athletes[idx].id, [])
            athlete_index[0] += 1
        else:
            q.all.return_value = []
        return q

    db.query.side_effect = _side
    db.commit.return_value = None

    with patch(_SESSION) as mock_ctx, \
         patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
               return_value=sync_return) as mock_sync:
        mock_ctx.return_value.__enter__ = lambda s: db
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        _run_garmin_reconciliation()

    return mock_sync


# ---------------------------------------------------------------------------
# No unsynced workouts
# ---------------------------------------------------------------------------

class TestReconciliationNoWork:

    def test_no_athletes_no_sync_calls(self):
        """No athletes → sync_week_to_garmin never called."""
        mock_sync = _run([], {})
        mock_sync.assert_not_called()

    def test_athlete_with_no_garmin_credentials_skipped(self):
        """Athlete without Garmin credentials is skipped entirely."""
        athlete = _athlete(has_garmin=False)
        mock_sync = _run([athlete], {athlete.id: []})
        mock_sync.assert_not_called()

    def test_fully_synced_athlete_no_sync_calls(self):
        """Athlete with all workouts synced → no sync calls."""
        athlete = _athlete()
        # No unsynced workouts
        mock_sync = _run([athlete], {athlete.id: []})
        mock_sync.assert_not_called()


# ---------------------------------------------------------------------------
# Unsynced workouts trigger sync
# ---------------------------------------------------------------------------

class TestReconciliationSyncsUnsynced:

    def test_single_unsynced_workout_triggers_sync(self):
        """One unsynced workout → sync_week_to_garmin called once for its week."""
        athlete = _athlete()
        unsynced = [_workout(athlete.id, days_ahead=3)]
        mock_sync = _run([athlete], {athlete.id: unsynced})
        assert mock_sync.call_count == 1

    def test_unsynced_workouts_in_same_week_trigger_one_sync_call(self):
        """Multiple unsynced workouts in the same week → one sync call (not one per workout)."""
        athlete = _athlete()
        monday = TODAY + timedelta(days=(7 - TODAY.weekday()))  # next Monday
        unsynced = [
            _workout(athlete.id, days_ahead=(monday - TODAY).days),
            _workout(athlete.id, days_ahead=(monday - TODAY).days + 2),
            _workout(athlete.id, days_ahead=(monday - TODAY).days + 4),
        ]
        mock_sync = _run([athlete], {athlete.id: unsynced})
        assert mock_sync.call_count == 1

    def test_unsynced_workouts_in_different_weeks_trigger_one_call_per_week(self):
        """Unsynced workouts in 3 different weeks → sync called 3 times."""
        athlete = _athlete()
        unsynced = [
            _workout(athlete.id, days_ahead=2),   # this week
            _workout(athlete.id, days_ahead=9),   # next week
            _workout(athlete.id, days_ahead=16),  # week after
        ]
        mock_sync = _run([athlete], {athlete.id: unsynced})
        assert mock_sync.call_count == 3

    def test_workout_with_workout_id_but_no_schedule_id_triggers_sync(self):
        """Library-only orphan (workout_id set, schedule_id NULL) is also picked up."""
        athlete = _athlete()
        unsynced = [_workout(athlete.id, days_ahead=3, garmin_workout_id="999")]
        mock_sync = _run([athlete], {athlete.id: unsynced})
        assert mock_sync.call_count == 1

    def test_correct_athlete_credentials_passed_to_sync(self):
        """sync_week_to_garmin must be called with this athlete's ID and credentials."""
        athlete = _athlete(athlete_id=42)
        unsynced = [_workout(42, days_ahead=4)]
        mock_sync = _run([athlete], {42: unsynced})

        call_args = mock_sync.call_args
        assert call_args[0][0] == 42
        assert call_args[0][1] == athlete.garmin_email
        assert call_args[0][2] == athlete.garmin_password_encrypted


# ---------------------------------------------------------------------------
# Multi-athlete isolation
# ---------------------------------------------------------------------------

class TestReconciliationMultiAthlete:

    def test_two_athletes_each_synced_independently(self):
        """Two athletes with different unsynced workouts → sync called for each."""
        a1 = _athlete(athlete_id=1)
        a2 = _athlete(athlete_id=2)
        unsynced1 = [_workout(1, days_ahead=3)]
        unsynced2 = [_workout(2, days_ahead=5)]

        db = MagicMock()
        from running_coach_ai.database.models import Athlete

        athlete_q = MagicMock()
        athlete_q.filter.return_value.all.return_value = [a1, a2]

        iter_state = {"call": 0}

        def _side(model):
            if model is Athlete:
                return athlete_q
            q = MagicMock()
            q.filter.return_value = q
            call_n = iter_state["call"]
            iter_state["call"] += 1
            if call_n == 0:
                q.all.return_value = unsynced1
            else:
                q.all.return_value = unsynced2
            return q

        db.query.side_effect = _side
        db.commit.return_value = None

        with patch(_SESSION) as mock_ctx, \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   return_value=(1, 0, [])) as mock_sync:
            mock_ctx.return_value.__enter__ = lambda s: db
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            _run_garmin_reconciliation()

        assert mock_sync.call_count == 2
        synced_athlete_ids = {c[0][0] for c in mock_sync.call_args_list}
        assert 1 in synced_athlete_ids
        assert 2 in synced_athlete_ids


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------

class TestReconciliationErrorResilience:

    def test_sync_failure_does_not_abort_other_weeks(self):
        """If sync fails for one week, the job continues for other weeks."""
        athlete = _athlete()
        unsynced = [
            _workout(athlete.id, days_ahead=2),
            _workout(athlete.id, days_ahead=9),
            _workout(athlete.id, days_ahead=16),
        ]

        db = MagicMock()
        from running_coach_ai.database.models import Athlete

        athlete_q = MagicMock()
        athlete_q.filter.return_value.all.return_value = [athlete]

        workout_q = MagicMock()
        workout_q.filter.return_value = workout_q
        workout_q.all.return_value = unsynced

        db.query.side_effect = lambda m: athlete_q if m is Athlete else workout_q
        db.commit.return_value = None

        call_count = [0]

        def _flaky_sync(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("Garmin 500")
            return (1, 0, [])

        with patch(_SESSION) as mock_ctx, \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   side_effect=_flaky_sync) as mock_sync:
            mock_ctx.return_value.__enter__ = lambda s: db
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            _run_garmin_reconciliation()  # must not raise

        # All 3 weeks attempted despite week 2 failure
        assert mock_sync.call_count == 3

    def test_athlete_error_does_not_abort_other_athletes(self):
        """Error processing one athlete must not stop reconciliation for others."""
        a1 = _athlete(athlete_id=1)
        a2 = _athlete(athlete_id=2)

        db = MagicMock()
        from running_coach_ai.database.models import Athlete

        athlete_q = MagicMock()
        athlete_q.filter.return_value.all.return_value = [a1, a2]

        iter_state = {"call": 0}

        def _side(model):
            if model is Athlete:
                return athlete_q
            q = MagicMock()
            q.filter.return_value = q
            call_n = iter_state["call"]
            iter_state["call"] += 1
            if call_n == 0:
                # athlete 1 query raises
                q.all.side_effect = Exception("DB error")
            else:
                q.all.return_value = [_workout(2, days_ahead=5)]
            return q

        db.query.side_effect = _side
        db.commit.return_value = None

        with patch(_SESSION) as mock_ctx, \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   return_value=(1, 0, [])) as mock_sync:
            mock_ctx.return_value.__enter__ = lambda s: db
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            _run_garmin_reconciliation()  # must not raise

        # athlete 2's sync must still have been attempted
        assert mock_sync.call_count >= 1
