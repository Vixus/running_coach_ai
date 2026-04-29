"""Daily plan adaptation — morning check-in and weekly load adjustment."""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from running_coach_ai.coach.persona import call_claude, format_miles, format_pace_mi, km_to_mi
from running_coach_ai.coach.personas import get_persona
from running_coach_ai.database.models import Athlete, HealthSnapshot, PlannedWorkout
from running_coach_ai.database.session import scoped_query
from running_coach_ai.slack.conversation import extract_and_apply_plan

logger = logging.getLogger(__name__)

MORNING_CHECKIN_PROMPT = """You are running the daily morning check-in for {name}.

TODAY'S PLAN:
{todays_session}

TODAY'S HEALTH DATA:
{health_data}

WEATHER TODAY:
{weather}

Based on the health signals and weather, evaluate whether today's planned session is appropriate.

If adjustment is needed, include a <plan> tag with the modification (use action "modify_session" or "skip_session").
If the session should proceed as planned, confirm it with encouragement.

Write a morning message in coach voice: personalised, warm, direct. Include:
1. What they're doing today (or what's changed and why)
2. A weather note if relevant
3. One motivational or tactical cue for today's session

Keep it concise — this is a morning message, not a lecture."""


# Fallback for devices without Training Readiness: sleep_score is only finalised
# by Garmin after sleep tracking ends, making it a reliable wakeup proxy.
_HEALTH_KEY_FIELDS = ("sleep_score",)


def _garmin_morning_data_complete(snapshot) -> bool:
    """Return True when Garmin has finished processing overnight health data.

    Primary signal: training_readiness — Garmin only generates this composite
    score after sleep tracking ends, HRV is computed, and body battery is
    recalculated. It is the same gate Garmin uses to show the morning Training
    Readiness card in the app.

    Fallback (devices without Training Readiness support): require all three
    proxy fields to be present instead.
    """
    if snapshot is None:
        return False
    if snapshot.training_readiness is not None:
        return True
    return all(getattr(snapshot, f) is not None for f in _HEALTH_KEY_FIELDS)


def run_morning_checkin(athlete: Athlete, db_session: Session, slack_client=None) -> None:
    """Run the morning check-in for a single athlete.

    Fetches health data, weather, evaluates today's session, adapts if
    needed, and sends a personalised Slack DM.

    Health-data gate (FR-030–FR-033): returns early without sending if
    Garmin has not yet processed the night's sleep/HRV/body-battery data.
    Retries are handled by the 30-minute IntervalTrigger in the scheduler.
    """
    tz = ZoneInfo(athlete.timezone or "America/New_York")
    today = datetime.now(tz).date()
    today_str = today.isoformat()

    # Dedup guard — ensure exactly one DM per day (FR-033)
    if athlete.last_morning_checkin_date == today:
        logger.debug("Morning check-in already sent to athlete %d today, skipping", athlete.id)
        return

    # Time-of-day floor: never send before 06:00 local, even if Garmin has already
    # processed a nap as a completed sleep session and the data gate would pass.
    now_local = datetime.now(tz)
    if now_local.hour < 6:
        logger.debug(
            "Morning check-in suppressed for athlete %d — too early (%s local)",
            athlete.id, now_local.strftime("%H:%M"),
        )
        return

    # Fetch Garmin health data — try live fetch, fall back to yesterday's stored snapshot
    health_text = "No Garmin data available."
    snapshot = None
    if athlete.garmin_email and athlete.garmin_password_encrypted:
        try:
            from running_coach_ai.garmin.client import get_garmin_client, get_health_snapshot
            from running_coach_ai.garmin.parser import parse_health_snapshot
            garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)
            raw_health = get_health_snapshot(garmin, today_str)
            snapshot = parse_health_snapshot(raw_health, athlete.id, today, db_session)
            logger.info("Fetched live Garmin health data for athlete %d", athlete.id)
        except Exception as e:
            logger.error("Live health data fetch failed for athlete %d: %s", athlete.id, e)

    # Health-data gate (FR-031, FR-032): wait for Garmin to finish processing all overnight metrics.
    # Uses training_readiness as the primary signal (Garmin's own morning-complete indicator),
    # falling back to requiring all three proxy fields on devices that don't support it.
    # Only applies to athletes WITH Garmin — no-Garmin athletes always get a workout/weather check-in.
    if not _garmin_morning_data_complete(snapshot) and athlete.garmin_email:
        if now_local.hour < 12:
            logger.debug(
                "No health data yet for athlete %d at %s local — will retry on next tick",
                athlete.id, now_local.strftime("%H:%M"),
            )
            return
        logger.info(
            "No health data for athlete %d by 12:00pm (%s local) — skipping morning check-in for today",
            athlete.id, now_local.strftime("%H:%M"),
        )
        return

    # Fall back to most recent stored snapshot if live fetch failed or returned no useful data
    if snapshot is None or all(
        getattr(snapshot, f) is None
        for f in ("hrv_score", "sleep_score", "resting_hr", "body_battery_start")
    ):
        stored = (
            scoped_query(db_session, HealthSnapshot, athlete.id)
            .filter(
                HealthSnapshot.date >= today - timedelta(days=3),
            )
            .order_by(HealthSnapshot.date.desc())
            .first()
        )
        if stored and snapshot is None:
            snapshot = stored
            logger.info("Using stored health snapshot from %s for athlete %d", stored.date, athlete.id)

    if snapshot:
        date_label = "today" if snapshot.date == today else snapshot.date.isoformat()
        health_parts = [
            f"HRV: {snapshot.hrv_score} ({snapshot.hrv_status or 'N/A'})",
            f"Sleep score: {snapshot.sleep_score}",
            f"Resting HR: {snapshot.resting_hr} bpm",
            f"Body battery: {snapshot.body_battery_start}",
            f"Stress avg: {snapshot.stress_avg}",
        ]
        if snapshot.training_readiness is not None:
            health_parts.append(f"Training readiness: {snapshot.training_readiness}")
        health_text = ", ".join(health_parts)
        if snapshot.date != today:
            health_text += f" (data from {date_label})"

    # Fetch weather
    weather_text = "Weather data unavailable."
    if athlete.home_lat and athlete.home_lon:
        try:
            from running_coach_ai.weather.client import get_forecast, summarise_forecast
            forecast = get_forecast(athlete.home_lat, athlete.home_lon)
            summary = summarise_forecast(forecast)
            if summary:
                today_wx = summary[0]
                weather_text = (
                    f"{today_wx['condition']}, {today_wx['temp_min']}–{today_wx['temp_max']}°C, "
                    f"wind {today_wx['wind_max']} km/h, rain {today_wx['precip_prob']}%"
                )
                if today_wx["adjustment"] != "no_change":
                    weather_text += f" [Adjustment needed: {today_wx['adjustment']}]"
        except Exception as e:
            logger.warning("Weather fetch failed for athlete %d: %s", athlete.id, e)

    # Get today's planned workout
    todays_workout = (
        scoped_query(db_session, PlannedWorkout, athlete.id)
        .filter(
            PlannedWorkout.scheduled_date == today,
        )
        .first()
    )

    if todays_workout:
        if todays_workout.target_distance_km:
            vol_str = f" {format_miles(todays_workout.target_distance_km)}"
        elif todays_workout.target_duration_seconds:
            vol_str = f" {todays_workout.target_duration_seconds // 60}min"
        else:
            vol_str = ""
        pace_str = f" @ {format_pace_mi(todays_workout.target_pace_min_per_km)}" if todays_workout.target_pace_min_per_km else ""
        session_text = (
            f"{todays_workout.workout_type}{vol_str}{pace_str}\n"
            f"Description: {todays_workout.description or 'N/A'}"
        )
    else:
        session_text = "No session scheduled for today (rest day)."

    # Build prompt and call Claude
    prompt = MORNING_CHECKIN_PROMPT.format(
        name=athlete.name,
        todays_session=session_text,
        health_data=health_text,
        weather=weather_text,
    )

    try:
        response = call_claude(get_persona(athlete.coach_key).persona_block, [{"role": "user", "content": prompt}])
    except Exception as e:
        logger.error("Claude morning check-in failed for athlete %d: %s", athlete.id, e)
        return

    # Apply any plan mutations
    response = extract_and_apply_plan(athlete.id, response, db_session)

    # Persist conversation turn, in-app notification, and (transitionally) Slack DM.
    try:
        from running_coach_ai.database.models import ConversationMessage
        from running_coach_ai.coach.notify import notify
        db_session.add(ConversationMessage(
            athlete_id=athlete.id,
            role="assistant",
            content=response,
        ))
        notify(
            db_session, athlete,
            kind="morning_checkin",
            title="Morning check-in",
            body=response,
            action_path="/app#chat",
        )
        if slack_client is not None:
            from running_coach_ai.slack.bot import send_dm
            send_dm(slack_client, athlete, response, db_session)
        athlete.last_morning_checkin_date = today
        db_session.commit()
        logger.info("Morning check-in delivered to athlete %d (slack=%s)", athlete.id, slack_client is not None)
    except Exception as e:
        logger.error("Failed to deliver morning check-in to athlete %d: %s", athlete.id, e)


def adapt_next_week(athlete: Athlete, week_summary: dict, db_session: Session) -> None:
    """Adapt the following week's plan based on current week performance.

    If athlete completed <70% of sessions → reduce next week's load.
    If athlete completed all sessions with positive signals → allow increment.
    Applies changes via <plan> mutations.
    """
    from running_coach_ai.coach.persona import call_claude
    from running_coach_ai.slack.conversation import extract_and_apply_plan

    completion_pct = week_summary.get("completion_pct", 100)
    next_week_start = date.fromisoformat(week_summary["week_start"]) + timedelta(weeks=1)

    # Get next week's planned workouts
    next_week_end = next_week_start + timedelta(days=6)
    next_workouts = (
        scoped_query(db_session, PlannedWorkout, athlete.id)
        .filter(
            PlannedWorkout.scheduled_date >= next_week_start,
            PlannedWorkout.scheduled_date <= next_week_end,
            PlannedWorkout.status.in_(["planned", "modified"]),
        )
        .order_by(PlannedWorkout.scheduled_date)
        .all()
    )

    if not next_workouts:
        logger.info("No next-week workouts to adapt for athlete %d", athlete.id)
        return

    sessions_str = "\n".join(
        "- {}: {} {}".format(
            w.scheduled_date,
            w.workout_type,
            format_miles(w.target_distance_km) if w.target_distance_km else (
                f"{w.target_duration_seconds // 60}min" if w.target_duration_seconds else ""
            ),
        )
        for w in next_workouts
    )

    actual_mi = round(km_to_mi(week_summary["actual_km"]), 1)
    planned_mi = round(km_to_mi(week_summary["planned_km"]), 1)

    adapt_prompt = f"""You are adapting next week's training plan for {athlete.name}.

THIS WEEK'S PERFORMANCE:
- Sessions completed: {week_summary['completed_sessions']}/{week_summary['planned_sessions']} ({completion_pct:.0f}%)
- Actual: {actual_mi} mi / Planned: {planned_mi} mi
- Quality sessions: {week_summary['quality_sessions_completed']}/{week_summary['quality_sessions_planned']}
- Avg HRV: {week_summary.get('avg_hrv', 'N/A')}
- Avg sleep score: {week_summary.get('avg_sleep_score', 'N/A')}

CURRENT NEXT WEEK PLAN:
{sessions_str}

Based on this week's performance, decide if next week needs adjusting.
Rules:
- If completion < 70%: reduce distances by 10-15%, swap one quality session to easy
- If completion = 100% and HRV/sleep are good: can increase easy distances by up to 10%
- Taper weeks: never increase load

Output a <plan> tag with action "regenerate_week" if changes needed, or just explain why no changes are needed."""

    try:
        response = call_claude(get_persona(athlete.coach_key).persona_block, [{"role": "user", "content": adapt_prompt}])
        extract_and_apply_plan(athlete.id, response, db_session)
        logger.info("Next-week plan adaptation complete for athlete %d", athlete.id)
    except Exception as e:
        logger.error("Plan adaptation failed for athlete %d: %s", athlete.id, e)
