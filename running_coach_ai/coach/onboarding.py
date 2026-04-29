"""Conversational onboarding flow — surface-agnostic.

Used by both the Slack adapter (`slack/onboarding.py`) and the web chat
endpoint (`web/api/chat.py`). All persistence is via SQLAlchemy; nothing
here knows about Slack or HTTP. Callers are responsible for displaying
the returned messages (DM, API response, etc.).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

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

logger = logging.getLogger(__name__)

PENDING_DATA_TTL_HOURS = 24

EXPIRY_MESSAGE = (
    "It's been a while — your profile session has expired. "
    "Send me a message and we'll pick up where we left off!"
)


def is_pending_data_expired(athlete: Athlete) -> bool:
    """Return True if the athlete's pending onboarding data has exceeded the TTL."""
    ts = athlete.pending_onboarding_data_created_at
    if ts is None:
        return True
    return (datetime.utcnow() - ts).total_seconds() > PENDING_DATA_TTL_HOURS * 3600


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


def build_system_prompt() -> str:
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
            build_system_prompt(),
            [{"role": "user", "content": "(start)"}],
        )
    except Exception as e:
        logger.warning("Could not generate dynamic welcome: %s", e)
        return "Hey! I'm your running coach — really glad you're here. To get us started, what's your name and how old are you?"


def handle_turn(athlete: Athlete, text: str, db: Session) -> dict:
    """Process one onboarding chat turn.

    Returns:
        {
            "response": str,        # visible assistant message (empty if expired)
            "is_complete": bool,    # <onboarding_complete> tag was found and parsed
            "pending_garmin": bool, # profile is set; Garmin creds modal needed
            "expired": bool,        # pending_onboarding_data has expired
        }

    Persists ConversationMessage rows for both user and assistant turns.
    Caller is responsible for displaying the response (DM, API response, etc.)
    and for triggering the Garmin creds modal when `pending_garmin=True`.
    """
    # Guard: if profile is already saved and waiting for Garmin creds, don't re-invoke Claude.
    if athlete.pending_onboarding_data is not None:
        if is_pending_data_expired(athlete):
            return {"response": EXPIRY_MESSAGE, "is_complete": False,
                    "pending_garmin": False, "expired": True}
        return {
            "response": "You're all set — just enter your Garmin credentials to finish setup.",
            "is_complete": False,
            "pending_garmin": True,
            "expired": False,
        }

    # Load conversation history
    history = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.athlete_id == athlete.id)
        .order_by(ConversationMessage.created_at)
        .all()
    )
    messages = [{"role": m.role, "content": m.content} for m in history]
    if messages and messages[0]["role"] == "assistant":
        messages.insert(0, {"role": "user", "content": "(start)"})
    messages.append({"role": "user", "content": text})

    step_number = len([m for m in history if m.role == "user"]) + 1

    try:
        response = call_claude(build_system_prompt(), messages)
    except Exception as e:
        logger.error("Claude API error during onboarding for athlete %d: %s", athlete.id, e)
        return {
            "response": "Lost my train of thought there — could you say that again?",
            "is_complete": False, "pending_garmin": False, "expired": False,
        }

    athlete.onboarding_step = step_number
    db.flush()

    match = re.search(r"<onboarding_complete>\s*(.*?)\s*</onboarding_complete>", response, re.DOTALL)
    clean = re.sub(r"<onboarding_complete>.*?</onboarding_complete>", "", response, flags=re.DOTALL).strip()

    db.add(ConversationMessage(athlete_id=athlete.id, role="user", content=text))
    db.add(ConversationMessage(athlete_id=athlete.id, role="assistant", content=clean or response))
    db.commit()

    if match:
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.error("Failed to parse onboarding JSON for athlete %d: %s", athlete.id, e)
            return {
                "response": clean or "Something went sideways on my end — let me pick that up again.",
                "is_complete": False, "pending_garmin": False, "expired": False,
            }
        athlete.pending_onboarding_data = data
        athlete.pending_onboarding_data_created_at = datetime.utcnow()
        db.flush()
        db.commit()
        logger.info("Stored pending onboarding data for athlete %d — awaiting Garmin creds", athlete.id)
        return {
            "response": clean,
            "is_complete": True,
            "pending_garmin": True,
            "expired": False,
        }

    return {
        "response": clean,
        "is_complete": False,
        "pending_garmin": False,
        "expired": False,
    }


CITIES = {
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


def parse_city_to_coords(city: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Look up approximate coordinates and timezone for a city name."""
    city_lower = (city or "").strip().lower()
    for name, (lat, lon, tz) in CITIES.items():
        if name in city_lower:
            return lat, lon, tz
    return None, None, None


def _import_historical_activities(athlete: Athlete, db: Session) -> int:
    from running_coach_ai.garmin.client import fetch_historical_activities, get_garmin_client
    from running_coach_ai.garmin.parser import parse_activity_list_entry

    end_date = date.today()
    start_date = end_date - timedelta(days=365)
    garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)
    activities = fetch_historical_activities(garmin, athlete.id, start_date.isoformat(), end_date.isoformat())
    if not activities:
        return 0
    imported = 0
    for activity in activities:
        try:
            workout = parse_activity_list_entry(activity, athlete.id, db)
            if workout:
                imported += 1
        except Exception as e:
            logger.warning("Skipped historical activity %s: %s", activity.get("activityId"), e)
    if imported > 0:
        db.commit()
    logger.info("Imported %d historical activities for athlete %d", imported, athlete.id)
    return imported


def _build_week1_summary(athlete: Athlete, plan, goal: Goal, db: Session) -> str:
    """Generate a natural-language week 1 summary via Claude."""
    from running_coach_ai.database.models import PlannedWorkout

    week1 = (
        db.query(PlannedWorkout)
        .filter(PlannedWorkout.plan_id == plan.id, PlannedWorkout.athlete_id == athlete.id)
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
        for w in week1
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
        return call_claude(get_persona(athlete.coach_key).persona_block, [{"role": "user", "content": prompt}])
    except Exception as e:
        logger.error("Week 1 summary generation failed for athlete %d: %s", athlete.id, e)
        lines = ["Your plan is ready! Here's week 1:\n"]
        for w in week1:
            dist = format_miles(w.target_distance_km) if w.target_distance_km else ""
            pace = f" @ {format_pace_mi(w.target_pace_min_per_km)}" if w.target_pace_min_per_km else ""
            lines.append(f"*{w.scheduled_date}* — {w.workout_type} {dist}{pace}: {w.description or ''}")
        lines.append("\nI'll check in every morning. Message me anytime.")
        return "\n".join(lines)


def complete_onboarding(
    athlete: Athlete,
    garmin_email: Optional[str],
    garmin_password: Optional[str],
    db: Session,
) -> dict:
    """Finalise onboarding: update Athlete, create Goal, generate plan, optionally upload to Garmin.

    `garmin_email` and `garmin_password` may be None to skip Garmin integration.
    Caller (web or slack adapter) is responsible for displaying the returned
    `messages` and for any external job-registration side effects.

    Returns:
        {
            "success": bool,
            "messages": list[str],   # final messages to deliver
            "error": str | None,
            "plan_id": int | None,
            "goal_id": int | None,
        }
    """
    from running_coach_ai.coach.planner import generate_plan
    from running_coach_ai.garmin.workout_builder import sync_week_to_garmin

    data = athlete.pending_onboarding_data or {}
    if not data:
        return {"success": False, "messages": [],
                "error": "No pending onboarding data found.", "plan_id": None, "goal_id": None}

    out_messages: list[str] = []

    # Update athlete record
    if data.get("name"):
        athlete.name = data["name"]
    if data.get("age"):
        athlete.age = int(data["age"])

    # Geocode city
    city = data.get("city", "")
    lat, lon, tz = parse_city_to_coords(city)
    if lat is not None:
        athlete.home_lat = lat
        athlete.home_lon = lon
        athlete.timezone = tz
    else:
        from running_coach_ai.coach.timezone_utils import derive_timezone_from_coords
        if athlete.home_lat is not None and athlete.home_lon is not None:
            derived = derive_timezone_from_coords(athlete.home_lat, athlete.home_lon)
            athlete.timezone = derived or "America/New_York"
        else:
            athlete.timezone = "America/New_York"

    # Garmin credentials (provided as form input, not from JSON)
    if garmin_email and garmin_password:
        athlete.garmin_email = garmin_email
        athlete.garmin_password_encrypted = encrypt_password(garmin_password)

    # Prescription style
    raw_style = data.get("prescription_style", "distance")
    athlete.prescription_style = raw_style if raw_style in ("time", "distance") else "distance"

    # Coach persona
    coach_key = resolve_coach_key(data.get("coach_key", DEFAULT_COACH_KEY))
    if coach_key is None or not is_valid_coach_key(coach_key):
        coach_key = DEFAULT_COACH_KEY
    athlete.coach_key = coach_key
    db.flush()

    # Historical activities (best-effort)
    if athlete.garmin_email and athlete.garmin_password_encrypted:
        try:
            _import_historical_activities(athlete, db)
        except Exception as e:
            logger.warning("Historical import failed for athlete %d (non-fatal): %s", athlete.id, e)

    # Race date
    try:
        race_date_val = date.fromisoformat(data["race_date"]) if data.get("race_date") else None
    except ValueError:
        race_date_val = None
    if not race_date_val:
        race_date_val = date.today() + timedelta(weeks=16)

    # Goal
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
    db.add(goal)
    db.flush()

    # Injuries
    injuries = data.get("injuries", "none")
    if injuries and injuries.lower() not in ("no", "none", "nah", "nothing", "n/a"):
        db.add(CoachMemory(
            athlete_id=athlete.id, category="injury", content=injuries, source="onboarding",
        ))

    # Plan + Garmin sync
    try:
        plan = generate_plan(athlete, goal, db)

        if athlete.garmin_email and athlete.garmin_password_encrypted:
            try:
                from running_coach_ai.garmin.client import get_garmin_client
                garmin_client = get_garmin_client(
                    athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted,
                )
                week = plan.valid_from
                while week <= plan.valid_to:
                    try:
                        sync_week_to_garmin(
                            athlete.id, athlete.garmin_email,
                            athlete.garmin_password_encrypted, week, db,
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
                out_messages.append(
                    "Your plan is ready — I hit a snag syncing to Garmin but the daily reconciliation will sort it out."
                )

        athlete.onboarding_complete = True
        athlete.pending_onboarding_data = None
        athlete.pending_onboarding_data_created_at = None
        db.commit()

        # Week 1 summary
        summary = _build_week1_summary(athlete, plan, goal, db)
        out_messages.append(summary)

        # Persist the summary into chat history so it shows on next /api/chat/history fetch.
        db.add(ConversationMessage(athlete_id=athlete.id, role="assistant", content=summary))
        db.commit()

        return {
            "success": True,
            "messages": out_messages,
            "error": None,
            "plan_id": plan.id,
            "goal_id": goal.id,
        }
    except Exception as e:
        logger.error("Failed to generate plan for athlete %d: %s", athlete.id, e, exc_info=True)
        return {
            "success": False,
            "messages": [
                "Something went wrong on my end generating the plan — give me a moment and then message me again. I'll get it sorted."
            ],
            "error": str(e),
            "plan_id": None,
            "goal_id": None,
        }
