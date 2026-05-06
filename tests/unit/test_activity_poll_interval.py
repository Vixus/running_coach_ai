"""Tests for the activity poll cron trigger configuration.

Production registers activity_poll as a cron trigger that fires every 30
minutes during waking hours (06–21). This validates that scheduling shape.
"""

from unittest.mock import MagicMock, patch

from apscheduler.triggers.cron import CronTrigger


class TestPollIntervalConfiguration:
    """Verify activity_poll is a CronTrigger firing every 30 min during 06–21."""

    def test_poll_trigger_uses_30_min_cron_in_active_hours(self):
        from running_coach_ai.scheduler.jobs import register_jobs

        mock_scheduler = MagicMock()
        mock_slack_app = MagicMock()

        with patch("running_coach_ai.database.session.get_session") as mock_get_session:
            mock_db = MagicMock()
            mock_db.__enter__ = MagicMock(return_value=mock_db)
            mock_db.__exit__ = MagicMock(return_value=False)
            mock_get_session.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = []

            register_jobs(mock_scheduler)

        add_job_calls = mock_scheduler.add_job.call_args_list
        poll_calls = [c for c in add_job_calls if c.kwargs.get("id") == "activity_poll"]
        assert len(poll_calls) == 1, "Expected exactly one activity_poll job"

        trigger_arg = poll_calls[0].args[1]
        assert isinstance(trigger_arg, CronTrigger), (
            f"Expected CronTrigger, got {type(trigger_arg)}"
        )
        # Inspect the trigger fields — hour='6-21', minute='*/30'
        fields = {f.name: str(f) for f in trigger_arg.fields}
        assert "*/30" in fields.get("minute", ""), (
            f"Expected minute=*/30, got {fields.get('minute')}"
        )
        assert fields.get("hour") in ("6-21", "06-21"), (
            f"Expected hour=6-21, got {fields.get('hour')}"
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

            register_jobs(mock_scheduler)

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
        with patch("running_coach_ai.scheduler.activity_poll.datetime") as mock_dt_cls:
            mock_dt_cls.now.return_value = frozen

            with patch("running_coach_ai.database.session.get_session") as mock_get_session:
                mock_db = MagicMock()
                mock_db.__enter__ = MagicMock(return_value=mock_db)
                mock_db.__exit__ = MagicMock(return_value=False)
                mock_get_session.return_value = mock_db
                mock_db.query.return_value.filter.return_value.all.return_value = []

                _run_activity_poll()

            # The function must reach get_session even at 02:00 (no gate)
            mock_get_session.assert_called_once()

    def test_poll_proceeds_within_active_hours(self):
        """_run_activity_poll must proceed at 09:00 (within 06–22 window)."""
        import datetime as _dt_mod
        from running_coach_ai.scheduler.jobs import _run_activity_poll

        mock_slack_client = MagicMock()

        frozen = _dt_mod.datetime(2024, 1, 15, 9, 0, 0)
        with patch("running_coach_ai.scheduler.activity_poll.datetime") as mock_dt_cls:
            mock_dt_cls.now.return_value = frozen

            with patch("running_coach_ai.database.session.get_session") as mock_get_session:
                mock_db = MagicMock()
                mock_db.__enter__ = MagicMock(return_value=mock_db)
                mock_db.__exit__ = MagicMock(return_value=False)
                mock_get_session.return_value = mock_db
                mock_db.query.return_value.filter.return_value.all.return_value = []

                _run_activity_poll()

            # At 09:00 the function must proceed past the guard and call get_session
            mock_get_session.assert_called_once()

