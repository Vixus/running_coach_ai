"""APScheduler job registration entrypoint.

The actual job bodies live in per-job modules (morning.py, activity_poll.py,
health_backfill.py, reconcile.py, weekly_review.py). This module owns the
schedule wiring (cron triggers, intervals) and re-exports the public symbols
that external callers (admin_ops, tests, main.py) historically imported from
``scheduler.jobs``.
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# Re-export the per-job entrypoints. External callers expect these names on
# ``running_coach_ai.scheduler.jobs`` — keep the surface stable across the split.
from running_coach_ai.scheduler._helpers import (  # noqa: F401
    _garmin_auth_error_notified,
    _notify_garmin_auth_error,
)
from running_coach_ai.scheduler.activity_poll import (  # noqa: F401
    _ingest_and_feedback,
    _retry_pending_feedback,
    _run_activity_poll,
)
from running_coach_ai.scheduler.health_backfill import _run_health_backfill  # noqa: F401
from running_coach_ai.scheduler.morning import (  # noqa: F401
    _next_checkin_start,
    _run_morning_checkin_for_athlete,
    register_athlete_morning_job,
)
from running_coach_ai.scheduler.reconcile import _run_garmin_reconciliation  # noqa: F401
from running_coach_ai.scheduler.token_refresh import _run_token_refresh  # noqa: F401
from running_coach_ai.scheduler.weekly_review import (  # noqa: F401
    _run_weekly_review,
    _upsert_weekly_review_summary,
)

logger = logging.getLogger(__name__)


def register_jobs(scheduler: BlockingScheduler) -> None:
    """Register all scheduled jobs. Called from main.py at startup."""
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.database.session import get_session

    # Morning check-in — one IntervalTrigger per athlete polling every 30 min from 07:00 local
    with get_session() as db_session:
        athletes = (
            db_session.query(Athlete)
            .filter(
                Athlete.allowed == True,
                Athlete.onboarding_complete == True,
            )
            .all()
        )
        for athlete in athletes:
            tz = athlete.timezone or "America/New_York"
            scheduler.add_job(
                _run_morning_checkin_for_athlete,
                IntervalTrigger(minutes=30, start_date=_next_checkin_start(tz), timezone=tz),
                args=[athlete.id],
                id=f"morning_checkin_{athlete.id}",
                replace_existing=True,
                misfire_grace_time=300,
                max_instances=1,
            )
            logger.info("Registered morning check-in for athlete %d (%s)", athlete.id, tz)

    # Activity poll — every 30 minutes, 06:00–22:00 only
    scheduler.add_job(
        _run_activity_poll,
        CronTrigger(minute="*/30", hour="6-21"),
        args=[scheduler],
        id="activity_poll",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Weekly review — Sunday 20:00 system time
    scheduler.add_job(
        _run_weekly_review,
        CronTrigger(day_of_week="sun", hour=20, minute=0),
        id="weekly_review",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Daily Garmin reconciliation — 08:30, after morning check-ins have run
    scheduler.add_job(
        _run_garmin_reconciliation,
        CronTrigger(hour=8, minute=30),
        id="garmin_reconciliation",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    # Daily health data backfill — 14:00, catches any missed morning health snapshots
    scheduler.add_job(
        _run_health_backfill,
        CronTrigger(hour=14, minute=0),
        id="health_backfill",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    # Daily Garmin OAuth2 token refresh — 05:30, just before activity_poll opens at 06:00.
    # Primes the OAuth2 access token (expired after the 22:00 -> 06:00 overnight gap)
    # so the day's first real Garmin call doesn't have to refresh under load.
    #
    # Disabled when DISABLE_GARMIN_TOKEN_REFRESH is set. On Railway, Garmin
    # 429s the cloud-IP OAuth refresh endpoint persistently; the proactive
    # job just adds to that. Token refresh runs lazily inside
    # garmin/client.py:get_garmin_client when an actual API call needs fresh
    # tokens, gated by a module-level rate-limit cache that short-circuits
    # subsequent calls during a known 429 cooldown
    # (GARMIN_RATE_LIMIT_COOLDOWN_SECONDS, default 900).
    import os
    if os.environ.get("DISABLE_GARMIN_TOKEN_REFRESH"):
        logger.info(
            "Skipping garmin_token_refresh job registration "
            "(DISABLE_GARMIN_TOKEN_REFRESH is set — refresh runs lazily with rate-limit cache)"
        )
    else:
        scheduler.add_job(
            _run_token_refresh,
            CronTrigger(hour=5, minute=30),
            id="garmin_token_refresh",
            replace_existing=True,
            misfire_grace_time=1800,
        )

    # Refresh athlete morning jobs every 5 minutes — picks up athletes onboarded
    # via the web after the scheduler started, without a restart.
    scheduler.add_job(
        _refresh_athlete_morning_jobs,
        IntervalTrigger(minutes=5),
        args=[scheduler],
        id="refresh_athlete_morning_jobs",
        replace_existing=True,
        misfire_grace_time=300,
    )

    logger.info("All scheduler jobs registered")


def _refresh_athlete_morning_jobs(scheduler) -> None:
    """Idempotently ensure every onboarded athlete has a morning check-in job.

    Picks up athletes onboarded via the web after the scheduler started.
    """
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.database.session import get_session

    with get_session() as db_session:
        athletes = (
            db_session.query(Athlete)
            .filter(
                Athlete.allowed == True,
                Athlete.onboarding_complete == True,
            )
            .all()
        )
        existing = {j.id for j in scheduler.get_jobs()}
        for athlete in athletes:
            job_id = f"morning_checkin_{athlete.id}"
            if job_id in existing:
                continue
            tz = athlete.timezone or "America/New_York"
            scheduler.add_job(
                _run_morning_checkin_for_athlete,
                IntervalTrigger(minutes=30, start_date=_next_checkin_start(tz), timezone=tz),
                args=[athlete.id],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=300,
                max_instances=1,
            )
            logger.info("Refresh: registered morning check-in for athlete %d (%s)", athlete.id, tz)
