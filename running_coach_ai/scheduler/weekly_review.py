"""Weekly review scheduler job.

Runs Sunday 20:00 system time. For each athlete: aggregates the week, asks the
coach for a review narrative, adapts next week's plan, persists a
WeeklyReviewSummary row for the dashboard, syncs the next 2 weeks to Garmin,
and writes a notification.
"""

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def _upsert_weekly_review_summary(athlete_id: int, week_start, week_summary: dict, narrative: str, db_session) -> None:
    """Persist the weekly review to WeeklyReviewSummary using SQLite upsert."""
    from running_coach_ai.database.models import WeeklyReviewSummary, CompletedWorkout, HealthSnapshot, PlannedWorkout
    from sqlalchemy.dialects.sqlite import insert
    from running_coach_ai.coach.persona import km_to_mi

    # Build daily volume array (Mon–Sun) from completed workouts
    week_end = week_start + timedelta(days=6)
    completed = (
        db_session.query(CompletedWorkout)
        .filter(
            CompletedWorkout.athlete_id == athlete_id,
            CompletedWorkout.date >= week_start,
            CompletedWorkout.date <= week_end,
        )
        .all()
    )
    daily_volume = [0.0] * 7
    for cw in completed:
        dow = cw.date.weekday()  # 0=Mon
        daily_volume[dow] = round(daily_volume[dow] + km_to_mi(cw.distance_km or 0), 1)

    # Collect 8-week body battery data (most recent 8 Sundays)
    eight_weeks_ago = week_start - timedelta(weeks=7)
    health_rows = (
        db_session.query(HealthSnapshot)
        .filter(
            HealthSnapshot.athlete_id == athlete_id,
            HealthSnapshot.date >= eight_weeks_ago,
            HealthSnapshot.date <= week_end,
        )
        .order_by(HealthSnapshot.date.asc())
        .all()
    )
    body_battery = [h.body_battery_start for h in health_rows if h.body_battery_start is not None][-8:]

    # Build next-week preview from PlannedWorkout rows
    next_week_start = week_start + timedelta(weeks=1)
    next_week_end = next_week_start + timedelta(days=6)
    planned_next = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete_id,
            PlannedWorkout.scheduled_date >= next_week_start,
            PlannedWorkout.scheduled_date <= next_week_end,
        )
        .order_by(PlannedWorkout.scheduled_date.asc())
        .all()
    )
    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    next_week_json = [
        {
            "day": day_labels[pw.scheduled_date.weekday()],
            "type": pw.workout_type or "rest",
            "label": pw.notes or pw.workout_type or "Rest",
        }
        for pw in planned_next
    ]

    total_miles = round(km_to_mi(week_summary.get("actual_km", 0) or 0), 1)
    elevation_ft = None  # elevation not tracked in aggregate_week; kept null
    avg_hrv = week_summary.get("avg_hrv")
    total_tss = week_summary.get("total_training_load")

    stmt = insert(WeeklyReviewSummary).values(
        athlete_id=athlete_id,
        week_start_date=week_start,
        narrative=narrative,
        total_miles=total_miles,
        elevation_gain_ft=elevation_ft,
        avg_hrv=avg_hrv,
        total_tss=total_tss,
        daily_volume_json=daily_volume,
        body_battery_json=body_battery,
        next_week_json=next_week_json,
    ).on_conflict_do_update(
        index_elements=["athlete_id", "week_start_date"],
        set_={
            "narrative": narrative,
            "total_miles": total_miles,
            "elevation_gain_ft": elevation_ft,
            "avg_hrv": avg_hrv,
            "total_tss": total_tss,
            "daily_volume_json": daily_volume,
            "body_battery_json": body_battery,
            "next_week_json": next_week_json,
        },
    )
    db_session.execute(stmt)
    db_session.commit()
    logger.info("WeeklyReviewSummary upserted for athlete %d week %s", athlete_id, week_start)


def _run_weekly_review() -> None:
    """Weekly review job — runs Sunday 20:00."""
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.database.session import get_session
    from running_coach_ai.coach.planner import aggregate_week
    from running_coach_ai.coach.feedback import generate_weekly_review
    from running_coach_ai.coach.adapter import adapt_next_week
    from running_coach_ai.garmin.workout_builder import sync_week_to_garmin

    logger.info("Weekly review starting")
    today = date.today()
    week_start = today - timedelta(days=today.weekday())

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
            try:
                week_summary = aggregate_week(athlete.id, week_start, db_session)
                review_message = generate_weekly_review(athlete, week_summary, db_session)
                adapt_next_week(athlete, week_summary, db_session)

                # Persist the weekly review summary for the web dashboard
                _upsert_weekly_review_summary(athlete.id, week_start, week_summary, review_message, db_session)

                # Sync next 2 weeks to Garmin
                if athlete.garmin_email and athlete.garmin_password_encrypted:
                    for offset in (1, 2):
                        target_week = week_start + timedelta(weeks=offset)
                        try:
                            sync_week_to_garmin(
                                athlete.id,
                                athlete.garmin_email,
                                athlete.garmin_password_encrypted,
                                target_week,
                                db_session,
                            )
                        except Exception as sync_e:
                            logger.error(
                                "Weekly review Garmin sync failed for athlete %d week %s: %s",
                                athlete.id, target_week, sync_e,
                            )

                from running_coach_ai.coach.notify import notify
                notify(
                    db_session, athlete,
                    kind="weekly_review",
                    title="Weekly review",
                    body=review_message,
                    action_path="/#story",
                )
                db_session.commit()
                logger.info("Weekly review delivered to athlete %d", athlete.id)

            except Exception as e:
                logger.error("Weekly review failed for athlete %d: %s", athlete.id, e)
