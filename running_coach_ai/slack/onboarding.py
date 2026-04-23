"""Conversational onboarding flow powered by Claude."""

import json
import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from running_coach_ai.coach.persona import call_claude, format_miles, format_pace_mi
from running_coach_ai.coach.personas import (
    DEFAULT_COACH_KEY,
    PERSONAS,
    get_persona,
    is_valid_coach_key,
    resolve_coach_key,
)
from running_coach_ai.database.models import Athlete, CoachMemory, ConversationMessage, Goal
from running_coach_ai.garmin.client import encrypt_password

PENDING_DATA_TTL_HOURS = 24


def _is_pending_data_expired(athlete: Athlete) -> bool:
    """Return True if the athlete's pending onboarding data has exceeded the TTL."""
    ts = athlete.pending_onboarding_data_created_at
    if ts is None:
        return True
    return (datetime.utcnow() - ts).total_seconds() > PENDING_DATA_TTL_HOURS * 3600


def _send_garmin_credential_button(channel: str, client, *, include_skip: bool = False) -> None:
    """Send a Block Kit button message prompting the athlete to enter Garmin credentials.

    include_skip: show a 'Skip for now' button alongside the connect button.
    Use True during onboarding; False for post-onboarding credential-update prompts.
    """
    if include_skip:
        section_text = (
            "Your profile is all set! Connect your Garmin account to:\n"
            "• Sync your training plan to your watch\n"
            "• Get daily check-ins based on your sleep and HRV\n"
            "• Receive post-run feedback\n\n"
            "Or skip for now and chat with your coach without Garmin features."
        )
    else:
        section_text = (
            "Re-enter your Garmin Connect credentials to restore full training integration."
        )

    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Connect Garmin"},
            "action_id": "open_garmin_creds_modal",
            "style": "primary",
        }
    ]
    if include_skip:
        buttons.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Skip for now"},
            "action_id": "skip_garmin_creds",
        })

    client.chat_postMessage(
        channel=channel,
        text="Connect your Garmin account or skip for now.",
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": section_text},
            },
            {
                "type": "actions",
                "elements": buttons,
            },
        ],
    )


_EXPIRY_MESSAGE_ONBOARDING = (
    "It's been a while — your profile session has expired. "
    "Send me a message and we'll pick up where we left off!"
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_BASE = """You are an AI running coach onboarding a new athlete for the first time. Collect the information you need to build their training plan through natural, warm conversation.

You need to learn:
- Name and age
- Target race: type (marathon, half marathon, 10K, 5K), specific event name if they mention one (e.g. "Berlin Marathon", "Boston Marathon"), and date
- Target finish time
- Current weekly running volume in miles (convert to km internally for the JSON)
- Training days available per week
- Running experience level: beginner (first year of structured training), intermediate (1–3 years), or advanced (4+ years competing)
- Any injuries or health conditions ("none" is fine)
- City they train in (for weather forecasts)
- Coach selection: present the available coaches below and ask the athlete which one they'd like to work with. If they don't have a preference or are unclear, default to "classic".
- Prescription style: whether they prefer workouts prescribed by **time** ("45-minute easy run") or **distance** ("4-mile easy run"). Explain both options naturally and let them choose. If they're unsure or don't have a preference, choose for them based on their profile:
  - Recommend **time** for: beginners, athletes focused on consistency, those returning from injury, or anyone who finds distance-watching stressful.
  - Recommend **distance** for: experienced runners who track weekly mileage closely, athletes focused on race-pace precision, or anyone who prefers concrete measurable targets.
  - Pros of time: forgiving on varied terrain, consistent perceived effort, removes pace anxiety, great for aerobic base building.
  - Pros of distance: easy to track weekly volume, direct alignment with race goals, concrete progress measurement.
  Record whichever was chosen (including your recommendation if they deferred to you).

Use miles and min/mile paces throughout this conversation. Round numbers: 30, 45, 60 min; 8, 10, 12 miles.

{coach_roster}

Conversation style:
- Warm, direct, and genuinely interested — you're meeting a real athlete, not filling a form
- Combine related questions naturally; don't fire one question per message
- Acknowledge what they say before moving on
- If they volunteer information, don't ask again
- Ask a brief follow-up if something's unclear

Profile confirmation: once you have everything, present it as a clear, readable summary and ask the athlete to confirm or correct it. After they confirm, write a warm closing line that kicks off their coaching journey and signals you're building their plan now — then end with the completion tag.

<onboarding_complete>
{{"name":"ATHLETE_NAME","age":99,"race_type":"marathon","race_name":"EVENT NAME OR null","race_date":"YYYY-MM-DD","target_time_seconds":99999,"weekly_mileage_km":99.9,"training_days":9,"experience_level":"intermediate","injuries":"none","city":"CITY_NAME","coach_key":"classic","prescription_style":"distance"}}
</onboarding_complete>

Field rules:
- prescription_style: "time" | "distance" — always set one, even if you chose for the athlete
- race_type: marathon | half_marathon | 10k | 5k
- race_name: the specific event name the athlete mentioned (e.g. "Berlin Marathon"), or null if they didn't name one — never infer or guess from city/date
- race_date: YYYY-MM-DD. If the athlete names a well-known race (e.g. Berlin Marathon, Boston Marathon), use your knowledge of that event's typical date to suggest the correct date and confirm with the athlete — do not silently assume; flag if the date they gave seems inconsistent with the named event
- target_time_seconds: integer seconds (3:45:00 → 13500, 1:30:00 → 5400)
- weekly_mileage_km: float km (convert miles: × 1.60934)
- experience_level: beginner | intermediate | advanced
- injuries: athlete's own words, or "none"
- coach_key: the registry key of the chosen coach ("classic", "maya", or "jordan"); default to "classic" if the athlete does not express a preference

Only output the completion tag after the athlete has explicitly confirmed their profile."""


def _build_onboarding_system_prompt() -> str:
    """Build the onboarding system prompt with the dynamic coach roster."""
    roster_lines = ["## Available Coaches\n"]
    for p in PERSONAS.values():
        roster_lines.append(f"- **{p.name}** (`{p.key}`): {p.description}")
    coach_roster = "\n".join(roster_lines)
    return _SYSTEM_PROMPT_BASE.format(coach_roster=coach_roster)


def generate_welcome() -> str:
    """Generate a dynamic first greeting via Claude."""
    try:
        return call_claude(
            _build_onboarding_system_prompt(),
            [{"role": "user", "content": "(start)"}],
        )
    except Exception as e:
        logger.warning("Could not generate dynamic welcome: %s", e)
        return "Hey! I'm your running coach — really glad you're here. To get us started, what's your name and how old are you?"


def handle(athlete: Athlete, text: str, db_session: Session, say_fn,
           *, client=None, channel: str | None = None, msg_ts: str | None = None) -> None:
    """Process one message in the conversational onboarding flow."""

    # US2 guard: if the athlete has pending onboarding data awaiting modal submission,
    # do not invoke Claude — just remind them to submit the button.
    if athlete.pending_onboarding_data is not None:
        if _is_pending_data_expired(athlete):
            say_fn(_EXPIRY_MESSAGE_ONBOARDING)
        else:
            say_fn("You're all set — just click the button below to enter your Garmin credentials securely.")
            if client and channel:
                _send_garmin_credential_button(channel, client)
        return

    # Load conversation history for this athlete
    history = (
        db_session.query(ConversationMessage)
        .filter(ConversationMessage.athlete_id == athlete.id)
        .order_by(ConversationMessage.created_at)
        .all()
    )

    messages = [{"role": msg.role, "content": msg.content} for msg in history]

    # Anthropic API requires messages to start with role="user".
    # If the first stored message is the assistant welcome, prepend the synthetic "(start)" turn.
    if messages and messages[0]["role"] == "assistant":
        messages.insert(0, {"role": "user", "content": "(start)"})

    messages.append({"role": "user", "content": text})

    # Track onboarding progress: step count = number of prior turns + 1
    step_number = len([m for m in history if m.role == "user"]) + 1

    # Call Claude
    try:
        response = call_claude(_build_onboarding_system_prompt(), messages)
    except Exception as e:
        logger.error("Claude API error during onboarding for athlete %d: %s", athlete.id, e)
        say_fn("Lost my train of thought there — could you say that again?")
        return

    # Persist onboarding_step so we can resume if the athlete disconnects
    athlete.onboarding_step = step_number
    db_session.flush()

    # Check for completion tag
    match = re.search(r"<onboarding_complete>\s*(.*?)\s*</onboarding_complete>", response, re.DOTALL)

    # Strip the tag from the visible response
    clean_response = re.sub(
        r"<onboarding_complete>.*?</onboarding_complete>", "", response, flags=re.DOTALL
    ).strip()

    # Persist the conversation turn (store cleaned response so history stays readable)
    db_session.add(ConversationMessage(athlete_id=athlete.id, role="user", content=text))
    db_session.add(ConversationMessage(athlete_id=athlete.id, role="assistant", content=clean_response or response))
    db_session.commit()

    if match:
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.error("Failed to parse onboarding JSON for athlete %d: %s", athlete.id, e)
            say_fn(clean_response or "Something went sideways on my end — let me pick that up again.")
            return

        # Send Claude's closing message (if any) before presenting the credentials button
        if clean_response:
            say_fn(clean_response)

        # Store profile data and prompt the athlete to submit Garmin credentials via modal
        athlete.pending_onboarding_data = data
        athlete.pending_onboarding_data_created_at = datetime.utcnow()
        db_session.flush()
        logger.info("Stored pending onboarding data for athlete %d — awaiting modal submission", athlete.id)

        if client and channel:
            _send_garmin_credential_button(channel, client, include_skip=True)
        else:
            logger.warning("No client/channel available for athlete %d — garmin button not sent", athlete.id)
    else:
        say_fn(clean_response)


def _parse_city_to_coords(city: str) -> tuple[float | None, float | None, str | None]:
    """Look up approximate coordinates and timezone for a city name."""
    cities = {
        "london": (51.5074, -0.1278, "Europe/London"),
        "new york": (40.7128, -74.0060, "America/New_York"),
        "los angeles": (34.0522, -118.2437, "America/Los_Angeles"),
        "sydney": (-33.8688, 151.2093, "Australia/Sydney"),
        "berlin": (52.5200, 13.4050, "Europe/Berlin"),
        "paris": (48.8566, 2.3522, "Europe/Paris"),
        "tokyo": (35.6762, 139.6503, "Asia/Tokyo"),
        "toronto": (43.6532, -79.3832, "America/Toronto"),
        "melbourne": (-37.8136, 144.9631, "Australia/Melbourne"),
        "amsterdam": (52.3676, 4.9041, "Europe/Amsterdam"),
        "cape town": (-33.9249, 18.4241, "Africa/Johannesburg"),
        "dubai": (25.2048, 55.2708, "Asia/Dubai"),
        "singapore": (1.3521, 103.8198, "Asia/Singapore"),
        "johannesburg": (-26.2041, 28.0473, "Africa/Johannesburg"),
        "chicago": (41.8781, -87.6298, "America/Chicago"),
        "boston": (42.3601, -71.0589, "America/New_York"),
        "san francisco": (37.7749, -122.4194, "America/Los_Angeles"),
        "seattle": (47.6062, -122.3321, "America/Los_Angeles"),
        "denver": (39.7392, -104.9903, "America/Denver"),
        "madrid": (40.4168, -3.7038, "Europe/Madrid"),
        "barcelona": (41.3851, 2.1734, "Europe/Madrid"),
        "rome": (41.9028, 12.4964, "Europe/Rome"),
        "munich": (48.1351, 11.5820, "Europe/Berlin"),
        "zurich": (47.3769, 8.5417, "Europe/Zurich"),
        "stockholm": (59.3293, 18.0686, "Europe/Stockholm"),
        "oslo": (59.9139, 10.7522, "Europe/Oslo"),
        "copenhagen": (55.6761, 12.5683, "Europe/Copenhagen"),
        "dublin": (53.3498, -6.2603, "Europe/Dublin"),
        "edinburgh": (55.9533, -3.1883, "Europe/London"),
        "manchester": (53.4808, -2.2426, "Europe/London"),
        "auckland": (-36.8485, 174.7633, "Pacific/Auckland"),
        "hong kong": (22.3193, 114.1694, "Asia/Hong_Kong"),
        "seoul": (37.5665, 126.9780, "Asia/Seoul"),
        "beijing": (39.9042, 116.4074, "Asia/Shanghai"),
        "shanghai": (31.2304, 121.4737, "Asia/Shanghai"),
        "mumbai": (19.0760, 72.8777, "Asia/Kolkata"),
        "delhi": (28.6139, 77.2090, "Asia/Kolkata"),
        "buenos aires": (-34.6037, -58.3816, "America/Argentina/Buenos_Aires"),
        "sao paulo": (-23.5505, -46.6333, "America/Sao_Paulo"),
    }
    city_lower = city.strip().lower()
    for name, (lat, lon, tz) in cities.items():
        if name in city_lower:
            return lat, lon, tz
    return None, None, None


def _import_historical_activities(athlete: Athlete, db_session: Session) -> int:
    """Import up to 1 year of historical running activities from Garmin.

    Gives the coach context on the athlete's fitness, recent paces, and consistency
    before generating the training plan. Returns count of imported activities.
    """
    from running_coach_ai.garmin.client import fetch_historical_activities, get_garmin_client
    from running_coach_ai.garmin.parser import parse_activity_list_entry

    end_date = date.today()
    start_date = end_date - timedelta(days=365)

    garmin = get_garmin_client(
        athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted
    )
    activities = fetch_historical_activities(
        garmin, athlete.id, start_date.isoformat(), end_date.isoformat()
    )
    if not activities:
        return 0

    imported = 0
    for activity in activities:
        try:
            workout = parse_activity_list_entry(activity, athlete.id, db_session)
            if workout:
                imported += 1
        except Exception as e:
            logger.warning("Skipped historical activity %s: %s", activity.get("activityId"), e)

    if imported > 0:
        db_session.commit()
    logger.info("Imported %d historical activities for athlete %d", imported, athlete.id)
    return imported


def _complete_onboarding(athlete: Athlete, data: dict, db_session: Session, say_fn) -> None:
    """Finalise onboarding: update Athlete, create Goal, generate plan, upload to Garmin."""
    from running_coach_ai.coach.planner import generate_plan
    from running_coach_ai.garmin.workout_builder import sync_week_to_garmin

    # Update athlete record
    if data.get("name"):
        athlete.name = data["name"]
    if data.get("age"):
        athlete.age = int(data["age"])

    # Geocode city
    city = data.get("city", "")
    lat, lon, tz = _parse_city_to_coords(city)
    if lat is not None:
        athlete.home_lat = lat
        athlete.home_lon = lon
        athlete.timezone = tz
    else:
        # City not in hardcoded table. If coords are available (e.g., from a future
        # geocoder), derive the timezone via timezonefinder. Today this branch only
        # fires if home_lat/home_lon were pre-populated by another code path.
        from running_coach_ai.coach.timezone_utils import derive_timezone_from_coords
        if athlete.home_lat is not None and athlete.home_lon is not None:
            derived = derive_timezone_from_coords(athlete.home_lat, athlete.home_lon)
            if derived:
                athlete.timezone = derived
            else:
                logger.warning(
                    "Could not derive timezone for athlete %d from coords (%.4f, %.4f) — defaulting to America/New_York",
                    athlete.id, athlete.home_lat, athlete.home_lon,
                )
                athlete.timezone = "America/New_York"
        else:
            athlete.timezone = "America/New_York"

    # Store Garmin credentials
    garmin_email = data.get("garmin_email")
    garmin_password = data.get("garmin_password")
    if garmin_email and garmin_password:
        athlete.garmin_email = garmin_email
        athlete.garmin_password_encrypted = encrypt_password(garmin_password)

    # Set prescription style
    raw_style = data.get("prescription_style", "distance")
    athlete.prescription_style = raw_style if raw_style in ("time", "distance") else "distance"

    # Set coach persona
    coach_key = resolve_coach_key(data.get("coach_key", DEFAULT_COACH_KEY))
    if coach_key is None or not is_valid_coach_key(coach_key):
        logger.warning(
            "Invalid coach_key '%s' for athlete %d — defaulting to '%s'",
            data.get("coach_key"), athlete.id, DEFAULT_COACH_KEY,
        )
        coach_key = DEFAULT_COACH_KEY
    athlete.coach_key = coach_key

    db_session.flush()

    # Import historical activities to give the coach fitness context before plan generation
    if athlete.garmin_email and athlete.garmin_password_encrypted:
        try:
            say_fn("Give me a moment — I'm pulling your training history from Garmin to build a better plan for you.")
            count = _import_historical_activities(athlete, db_session)
            if count > 0:
                logger.info("Imported %d historical activities for athlete %d", count, athlete.id)
        except Exception as e:
            logger.warning("Historical import failed for athlete %d (non-fatal): %s", athlete.id, e)

    # Parse race date
    try:
        race_date_val = date.fromisoformat(data["race_date"]) if data.get("race_date") else None
    except ValueError:
        race_date_val = None
    if not race_date_val:
        race_date_val = date.today() + timedelta(weeks=16)

    # Create Goal
    goal = Goal(
        athlete_id=athlete.id,
        race_type=data.get("race_type", "marathon"),
        race_name=data.get("race_name") or None,
        race_date=race_date_val,
        target_time_seconds=int(data.get("target_time_seconds") or 14400),
        current_weekly_mileage_km=float(data.get("weekly_mileage_km") or 0),
        experience_level=data.get("experience_level", "intermediate"),
        training_days_per_week=int(data.get("training_days") or 5),
    )
    db_session.add(goal)
    db_session.flush()

    # Store injuries
    injuries = data.get("injuries", "none")
    if injuries and injuries.lower() not in ("no", "none", "nah", "nothing", "n/a"):
        db_session.add(CoachMemory(
            athlete_id=athlete.id,
            category="injury",
            content=injuries,
            source="onboarding",
        ))

    # Generate training plan
    try:
        plan = generate_plan(athlete, goal, db_session)

        # Upload all plan weeks to Garmin
        if athlete.garmin_email and athlete.garmin_password_encrypted:
            try:
                from running_coach_ai.garmin.client import get_garmin_client
                garmin_client = get_garmin_client(
                    athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted
                )
                week = plan.valid_from
                while week <= plan.valid_to:
                    try:
                        sync_week_to_garmin(
                            athlete.id,
                            athlete.garmin_email,
                            athlete.garmin_password_encrypted,
                            week,
                            db_session,
                            garmin_client=garmin_client,
                        )
                    except Exception as week_e:
                        logger.warning(
                            "Garmin sync failed for week %s during onboarding (athlete %d): %s",
                            week, athlete.id, week_e,
                        )
                    week += timedelta(weeks=1)
            except Exception as e:
                logger.error("Failed to sync plan to Garmin for athlete %d: %s", athlete.id, e)
                say_fn("Your plan is ready — I hit a snag syncing to Garmin but the daily reconciliation will sort it out.")

        # Mark onboarding complete
        athlete.onboarding_complete = True
        db_session.commit()

        # Register morning check-in job
        try:
            import main as app_main
            from running_coach_ai.scheduler.jobs import register_athlete_morning_job
            register_athlete_morning_job(app_main.scheduler, athlete, app_main.handler.app.client)
        except Exception as e:
            logger.warning("Could not register morning job for athlete %d: %s", athlete.id, e)

        _send_week1_summary(athlete, plan, goal, db_session, say_fn)

    except Exception as e:
        logger.error("Failed to generate plan for athlete %d: %s", athlete.id, e, exc_info=True)
        say_fn("Something went wrong on my end generating the plan — give me a moment and then message me again. I'll get it sorted.")


def _send_week1_summary(athlete: Athlete, plan, goal: Goal, db_session: Session, say_fn) -> None:
    """Generate and send a natural-language week 1 summary via Claude."""
    from running_coach_ai.database.models import PlannedWorkout

    week1_workouts = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.plan_id == plan.id,
            PlannedWorkout.athlete_id == athlete.id,
        )
        .order_by(PlannedWorkout.scheduled_date)
        .limit(7)
        .all()
    )

    sessions_text = "\n".join(
        "- {}: {}{}{}".format(
            w.scheduled_date.strftime("%A %d %b"),
            w.workout_type,
            f" {format_miles(w.target_distance_km)}" if w.target_distance_km else "",
            f" — {w.description}" if w.description else "",
        )
        for w in week1_workouts
    )

    h = goal.target_time_seconds // 3600
    m = (goal.target_time_seconds % 3600) // 60

    prompt = (
        f"You've just finished onboarding {athlete.name} (age {athlete.age}). "
        f"Their goal: {goal.race_type} on {goal.race_date}, target {h}:{m:02d}. "
        f"You've built their full training plan. Here are week 1's sessions:\n\n{sessions_text}\n\n"
        f"Write them a message introducing week 1. Don't just list the sessions — explain the shape of the week, "
        f"why you've structured it this way, and what they should focus on. "
        f"Weave the specific dates and sessions into the message naturally. "
        f"End with a brief, genuine send-off — you're their coach now, not a chatbot."
    )

    try:
        response = call_claude(get_persona(athlete.coach_key).persona_block, [{"role": "user", "content": prompt}])
        say_fn(response)
    except Exception as e:
        logger.error("Week 1 summary generation failed for athlete %d: %s", athlete.id, e)
        # Fallback: structured list is better than silence
        lines = ["Your plan is ready! Here's week 1:\n"]
        for w in week1_workouts:
            dist = format_miles(w.target_distance_km) if w.target_distance_km else ""
            pace = f" @ {format_pace_mi(w.target_pace_min_per_km)}" if w.target_pace_min_per_km else ""
            lines.append(f"*{w.scheduled_date}* — {w.workout_type} {dist}{pace}: {w.description or ''}")
        lines.append("\nI'll check in every morning. Message me anytime.")
        say_fn("\n".join(lines))
