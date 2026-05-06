"""Morning check-in scheduler job.

Each onboarded athlete gets an IntervalTrigger polling every 30 min from
their local 06:00 to 12:00, with a daily dedup guard so the actual coach
delivery happens at most once per day.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from running_coach_ai.scheduler._helpers import _notify_garmin_auth_error

logger = logging.getLogger(__name__)


def _next_checkin_start(tz_name: str) -> datetime:
    """Return the start_date for the morning check-in IntervalTrigger.

    - Before 06:00 local → today's 06:00
    - 06:00–noon local   → now (fire immediately; dedup guard prevents double-send)
    - After noon local   → tomorrow's 06:00 (too late to retry today)
    """
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    today_6am = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    today_noon = now_local.replace(hour=12, minute=0, second=0, microsecond=0)

    if now_local < today_6am:
        return today_6am
    if now_local < today_noon:
        return now_local
    return today_6am + timedelta(days=1)


def _run_morning_checkin_for_athlete(athlete_id: int) -> None:
    """Morning check-in job for a single athlete."""
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.database.session import get_session
    from running_coach_ai.coach.adapter import run_morning_checkin
    from running_coach_ai.garmin.client import is_garmin_auth_error

    logger.info("Morning check-in starting for athlete %d", athlete_id)
    try:
        with get_session() as db_session:
            athlete = db_session.query(Athlete).get(athlete_id)
            if not athlete or not athlete.allowed or not athlete.onboarding_complete:
                return
            try:
                run_morning_checkin(athlete, db_session)
            except Exception as inner_e:
                if is_garmin_auth_error(inner_e):
                    _notify_garmin_auth_error(athlete)
                raise
    except Exception as e:
        logger.error("Morning check-in failed for athlete %d: %s", athlete_id, e)


def register_athlete_morning_job(scheduler: BlockingScheduler, athlete) -> None:
    """Register (or re-register) the morning check-in job for a single athlete.

    Called after new athlete onboarding completes so the job takes effect
    without restarting the scheduler.
    """
    tz = athlete.timezone or "America/New_York"
    scheduler.add_job(
        _run_morning_checkin_for_athlete,
        IntervalTrigger(minutes=30, start_date=_next_checkin_start(tz), timezone=tz),
        args=[athlete.id],
        id=f"morning_checkin_{athlete.id}",
        replace_existing=True,
        misfire_grace_time=300,
    )
    logger.info("Registered morning check-in for newly onboarded athlete %d (%s)", athlete.id, tz)
