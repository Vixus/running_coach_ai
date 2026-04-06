"""T054: Tests for the activity poll interval and active-hours guard."""

from unittest.mock import MagicMock, patch

from apscheduler.triggers.interval import IntervalTrigger


class TestPollIntervalConfiguration:
    """Verify the scheduler registers the activity_poll job at ≤10 minutes."""

    def test_poll_trigger_uses_10_min_interval(self):
        """IntervalTrigger for activity_poll must be configured with minutes=10."""
        from running_coach_ai.scheduler.jobs import register_jobs

        mock_scheduler = MagicMock()
        mock_slack_app = MagicMock()

        # Patch all inline imports inside register_jobs
        with patch("running_coach_ai.database.session.get_session") as mock_get_session:
            mock_db = MagicMock()
            mock_db.__enter__ = MagicMock(return_value=mock_db)
            mock_db.__exit__ = MagicMock(return_value=False)
            mock_get_session.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = []

            register_jobs(mock_scheduler, mock_slack_app)

        # Find the add_job call for the activity_poll job
        add_job_calls = mock_scheduler.add_job.call_args_list
        poll_calls = [c for c in add_job_calls if c.kwargs.get("id") == "activity_poll"]
        assert len(poll_calls) == 1, "Expected exactly one activity_poll job"

        poll_call = poll_calls[0]
        # Trigger is the second positional arg: add_job(func, trigger, ...)
        trigger_arg = poll_call.args[1]
        assert isinstance(trigger_arg, IntervalTrigger), (
            f"Expected IntervalTrigger, got {type(trigger_arg)}"
        )
        interval_seconds = trigger_arg.interval.total_seconds()
        assert interval_seconds <= 600, (
            f"Poll interval is {interval_seconds}s — must be ≤10 min (600s)"
        )
        assert interval_seconds == 600, (
            f"Poll interval should be exactly 10 min (600s), got {interval_seconds}s"
        )

    def test_poll_job_id_is_activity_poll(self):
        """The activity poll job must be registered with id='activity_poll'."""
        from running_coach_ai.scheduler.jobs import register_jobs

        mock_scheduler = MagicMock()
        mock_slack_app = MagicMock()

        with patch("running_coach_ai.database.session.get_session") as mock_get_session:
            mock_db = MagicMock()
            mock_db.__enter__ = MagicMock(return_value=mock_db)
            mock_db.__exit__ = MagicMock(return_value=False)
            mock_get_session.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = []

            register_jobs(mock_scheduler, mock_slack_app)

        add_job_calls = mock_scheduler.add_job.call_args_list
        job_ids = [c.kwargs.get("id") for c in add_job_calls]
        assert "activity_poll" in job_ids, f"activity_poll job not found. All ids: {job_ids}"


class TestActiveHoursGuard:
    """Verify activity poll runs 24/7 with no active-hours gate."""

    def test_poll_proceeds_outside_former_active_hours(self):
        """_run_activity_poll must proceed at 02:00 — no server-time gate exists."""
        import datetime as _dt_mod
        from running_coach_ai.scheduler.jobs import _run_activity_poll

        mock_slack_client = MagicMock()

        frozen = _dt_mod.datetime(2024, 1, 15, 2, 0, 0)
        with patch("running_coach_ai.scheduler.jobs.datetime") as mock_dt_cls:
            mock_dt_cls.now.return_value = frozen

            with patch("running_coach_ai.database.session.get_session") as mock_get_session:
                mock_db = MagicMock()
                mock_db.__enter__ = MagicMock(return_value=mock_db)
                mock_db.__exit__ = MagicMock(return_value=False)
                mock_get_session.return_value = mock_db
                mock_db.query.return_value.filter.return_value.all.return_value = []

                _run_activity_poll(mock_slack_client)

            # The function must reach get_session even at 02:00 (no gate)
            mock_get_session.assert_called_once()

    def test_poll_proceeds_within_active_hours(self):
        """_run_activity_poll must proceed at 09:00 (within 06–22 window)."""
        import datetime as _dt_mod
        from running_coach_ai.scheduler.jobs import _run_activity_poll

        mock_slack_client = MagicMock()

        frozen = _dt_mod.datetime(2024, 1, 15, 9, 0, 0)
        with patch("running_coach_ai.scheduler.jobs.datetime") as mock_dt_cls:
            mock_dt_cls.now.return_value = frozen

            with patch("running_coach_ai.database.session.get_session") as mock_get_session:
                mock_db = MagicMock()
                mock_db.__enter__ = MagicMock(return_value=mock_db)
                mock_db.__exit__ = MagicMock(return_value=False)
                mock_get_session.return_value = mock_db
                mock_db.query.return_value.filter.return_value.all.return_value = []

                _run_activity_poll(mock_slack_client)

            # At 09:00 the function must proceed past the guard and call get_session
            mock_get_session.assert_called_once()

