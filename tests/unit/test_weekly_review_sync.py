"""Tests for Change 4: weekly review syncs the next 2 weeks (not just next week).

_run_weekly_review must call sync_week_to_garmin for:
  - next_week_start      (week_start + 1 week)
  - next_week_start + 1  (week_start + 2 weeks)
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch


from running_coach_ai.scheduler.jobs import _run_weekly_review

TODAY = date.today()
_JOBS = "running_coach_ai.scheduler.jobs"
_SESSION = "running_coach_ai.database.session.get_session"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _athlete(athlete_id: int = 1, has_garmin: bool = True):
    a = MagicMock()
    a.id = athlete_id
    a.name = "Alice"
    a.garmin_email = "alice@example.com" if has_garmin else None
    a.garmin_password_encrypted = b"enc" if has_garmin else None
    a.allowed = True
    a.onboarding_complete = True
    return a


def _make_db(athletes):
    from running_coach_ai.database.models import Athlete
    db = MagicMock()
    athlete_q = MagicMock()
    athlete_q.filter.return_value.all.return_value = athletes

    generic_q = MagicMock()
    generic_q.filter.return_value = generic_q
    generic_q.order_by.return_value = generic_q
    generic_q.all.return_value = []
    generic_q.scalar.return_value = None
    generic_q.first.return_value = None

    db.query.side_effect = lambda m: athlete_q if m is Athlete else generic_q
    db.commit.return_value = None
    return db


def _run(athletes, sync_return=(3, 0, [])):
    """Run _run_weekly_review with mocked DB, planner, feedback, and Garmin sync."""
    slack_client = MagicMock()
    db = _make_db(athletes)

    with patch(_SESSION) as mock_ctx, \
         patch("running_coach_ai.coach.planner.aggregate_week", return_value={
             "week_start": TODAY.isoformat(),
             "completed_sessions": 4, "planned_sessions": 5,
             "completion_pct": 80, "actual_km": 50.0, "planned_km": 60.0,
             "quality_sessions_completed": 1, "quality_sessions_planned": 2,
         }), \
         patch("running_coach_ai.coach.feedback.generate_weekly_review", return_value="Great week!"), \
         patch("running_coach_ai.coach.adapter.adapt_next_week"), \
         patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
               return_value=sync_return) as mock_sync, \
         patch("running_coach_ai.slack.bot.send_dm"):
        mock_ctx.return_value.__enter__ = lambda s: db
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        _run_weekly_review(slack_client)

    return mock_sync


# ---------------------------------------------------------------------------
# Two weeks synced
# ---------------------------------------------------------------------------

class TestWeeklyReviewSyncsNextTwoWeeks:

    def test_exactly_two_sync_calls_per_athlete(self):
        """Weekly review must call sync_week_to_garmin exactly twice per athlete."""
        mock_sync = _run([_athlete()])
        assert mock_sync.call_count == 2

    def test_first_sync_is_next_week(self):
        """First sync call must use next_week_start (this week's Monday + 7 days)."""
        mock_sync = _run([_athlete()])
        week_start = TODAY - timedelta(days=TODAY.weekday())
        expected_next_week = week_start + timedelta(weeks=1)
        first_week = mock_sync.call_args_list[0][0][3]
        assert first_week == expected_next_week

    def test_second_sync_is_week_after_next(self):
        """Second sync call must use next_week_start + 7 days."""
        mock_sync = _run([_athlete()])
        week_start = TODAY - timedelta(days=TODAY.weekday())
        expected_week_2 = week_start + timedelta(weeks=2)
        second_week = mock_sync.call_args_list[1][0][3]
        assert second_week == expected_week_2

    def test_no_third_sync_call(self):
        """Weekly review must NOT sync a third week (that's the reconciliation job's job)."""
        mock_sync = _run([_athlete()])
        assert mock_sync.call_count == 2

    def test_athlete_without_garmin_not_synced(self):
        """Athletes without Garmin credentials must not trigger any sync calls."""
        mock_sync = _run([_athlete(has_garmin=False)])
        mock_sync.assert_not_called()

    def test_two_athletes_each_get_two_sync_calls(self):
        """Two athletes → 4 total sync calls (2 per athlete)."""
        mock_sync = _run([_athlete(athlete_id=1), _athlete(athlete_id=2)])
        assert mock_sync.call_count == 4

    def test_sync_failure_week_1_does_not_skip_week_2(self):
        """If the first week sync fails, the second week must still be attempted."""
        slack_client = MagicMock()
        db = _make_db([_athlete()])

        call_count = [0]

        def _flaky(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Garmin 429")
            return (3, 0, [])

        with patch(_SESSION) as mock_ctx, \
             patch("running_coach_ai.coach.planner.aggregate_week", return_value={
                 "week_start": TODAY.isoformat(),
                 "completed_sessions": 4, "planned_sessions": 5,
                 "completion_pct": 80, "actual_km": 50.0, "planned_km": 60.0,
                 "quality_sessions_completed": 1, "quality_sessions_planned": 2,
             }), \
             patch("running_coach_ai.coach.feedback.generate_weekly_review", return_value="ok"), \
             patch("running_coach_ai.coach.adapter.adapt_next_week"), \
             patch("running_coach_ai.garmin.workout_builder.sync_week_to_garmin",
                   side_effect=_flaky) as mock_sync, \
             patch("running_coach_ai.slack.bot.send_dm"):
            mock_ctx.return_value.__enter__ = lambda s: db
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            _run_weekly_review(slack_client)  # must not raise

        assert mock_sync.call_count == 2

    def test_correct_athlete_credentials_used(self):
        """Sync calls must use the correct athlete's email and encrypted password."""
        athlete = _athlete(athlete_id=7)
        mock_sync = _run([athlete])

        for c in mock_sync.call_args_list:
            assert c[0][0] == 7
            assert c[0][1] == athlete.garmin_email
            assert c[0][2] == athlete.garmin_password_encrypted
