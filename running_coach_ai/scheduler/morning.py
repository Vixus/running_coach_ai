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


def _run_morning_checkin_for_athlete(athlete_id: int, force: bool = False) -> None:
    """Morning check-in job for a single athlete.

    `force=True` is set by the admin "Morning" button and bypasses all
    skip gates (dedup, 06:00 floor, health-data gate) inside run_morning_checkin.
    """
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.database.session import get_session
    from running_coach_ai.coach.adapter import run_morning_checkin
    from running_coach_ai.garmin.client import is_garmin_auth_error

    logger.info("Morning check-in starting for athlete %d (force=%s)", athlete_id, force)
    try:
        with get_session() as db_session:
            athlete = db_session.query(Athlete).get(athlete_id)
            if not athlete or not athlete.allowed or not athlete.onboarding_complete:
                return
            try:
                run_morning_checkin(athlete, db_session, force=force)
            except Exception as inner_e:
                if is_garmin_auth_error(inner_e):
                    _notify_garmin_auth_error(athlete)
                raise

            # Story triggers: race_upcoming (7d before goal) + difficult_week + stalled-session sweep.
            # Wrapped in try/except per Constitution V — never abort the morning job for story errors.
            try:
                from datetime import datetime as _dt, timedelta as _td

                from running_coach_ai.coach.story import (
                    detect_race_upcoming, detect_difficult_week,
                    fire_trigger_if_eligible, close_session,
                )
                from running_coach_ai.database.models import (
                    StoryInterviewSession, StoryQuestion,
                )

                # Athlete-local today — matches run_morning_checkin so the race-upcoming
                # 7-day window doesn't shift by one near server UTC midnight.
                today = _dt.now(ZoneInfo(athlete.timezone or "America/New_York")).date()
                goal = detect_race_upcoming(athlete, today, db_session)
                if goal is not None:
                    fire_trigger_if_eligible(
                        athlete, "race_upcoming", {"goal_id": goal.id}, db_session,
                    )
                elif detect_difficult_week(athlete, db_session, today):
                    fire_trigger_if_eligible(
                        athlete, "difficult_week", {}, db_session,
                    )

                # Stalled-session sweep (T035): close any session whose newest
                # StoryQuestion was asked >7 days ago and is still unanswered.
                stale_cutoff = _dt.utcnow() - _td(days=7)
                stalled = (
                    db_session.query(StoryInterviewSession)
                    .filter(
                        StoryInterviewSession.athlete_id == athlete.id,
                        StoryInterviewSession.completed_at.is_(None),
                    )
                    .all()
                )
                for s in stalled:
                    newest_q = (
                        db_session.query(StoryQuestion)
                        .filter(StoryQuestion.session_id == s.id)
                        .order_by(StoryQuestion.asked_at.desc())
                        .first()
                    )
                    if newest_q and newest_q.asked_at < stale_cutoff:
                        close_session(s, db_session, skipped=False)
                        logger.info("Closed stalled story session %d for athlete %d",
                                    s.id, athlete.id)
            except Exception as story_err:
                logger.warning("Story morning hooks failed for athlete %d: %s",
                               athlete.id, story_err)
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
        max_instances=1,
    )
    logger.info("Registered morning check-in for newly onboarded athlete %d (%s)", athlete.id, tz)
