"""GET /api/magazine — aggregated data for the magazine view.

Returns a flat JSON shape that the static magazine.html hydrates into the
design's placeholder values (athlete name, race, today's health, this week's
mileage, coach greeting, etc). All numbers are pre-formatted in athlete-facing
units (miles, hours, %), since the magazine is a pure view layer.
"""

import json
import logging
import re as _re
import threading
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request, session
from sqlalchemy import func

from running_coach_ai.coach.persona import call_claude, km_to_mi
from running_coach_ai.coach.personas import (
    PERSONAS,
    get_persona,
    is_valid_coach_key,
    resolve_coach_key,
)
from running_coach_ai.database.models import (
    Athlete,
    CompletedWorkout,
    Goal,
    HealthSnapshot,
    Notification,
    PlannedWorkout,
    TrainingPlan,
)
from running_coach_ai.database.session import get_session
from running_coach_ai.web.auth import login_required

logger = logging.getLogger(__name__)

# ── Quote cache ──────────────────────────────────────────────────────────────
# Keyed by athlete_id. Regenerates when date changes or a new workout is logged.
_quote_lock = threading.Lock()
_quote_cache: dict[int, dict] = {}


def _generate_quotes(
    persona,
    athlete_name: str,
    race_name: str | None,
    current_week: int | None,
    total_weeks: int | None,
    health: dict | None,
    week_miles: int,
) -> list[dict]:
    first = athlete_name.split()[0] if athlete_name else "athlete"
    race_ctx = f"preparing for {race_name}" if race_name else "training"
    week_ctx = (
        f"week {current_week} of {total_weeks}" if current_week and total_weeks
        else (f"week {current_week}" if current_week else "current training block")
    )
    hrv_line = f", HRV {health['hrv']} ms" if health and health.get("hrv") else ""
    bb_line = f", body battery {health['body_battery']}%" if health and health.get("body_battery") else ""

    prompt = f"""Generate exactly 3 short coach quotes for {first}'s running magazine page.

Coach: {persona.name} — {persona.philosophy if hasattr(persona, 'philosophy') else ''}
Athlete: {athlete_name}, {week_ctx}, {race_ctx}
This week: {week_miles} miles planned{hrv_line}{bb_line}

Rules:
- 1–3 sentences each, written in first-person as the coach observing {first}
- Reference specific numbers (week, miles, health data) in at least one quote
- Vary tone: one analytical, one motivational, one reflective
- Use {first}'s first name naturally in each quote
- Attribution ends with a context like "Week {current_week or 'X'} Assessment", "Morning Briefing", or "Post-Workout Analysis"

Return ONLY a JSON array, no markdown, no extra text:
[{{"q": "\\"quote\\"", "attr": "{persona.name} · Context"}}, ...]"""

    try:
        raw = call_claude(
            system_prompt=f"You are {persona.name}, a running coach. Respond only with the requested JSON array.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
        )
        text = raw.strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1].lstrip("json").strip() if len(parts) > 1 else text
        quotes = json.loads(text)
        return quotes if isinstance(quotes, list) and quotes else []
    except Exception:
        logger.exception("Failed to generate magazine quotes")
        return []


def _get_quotes(
    athlete_id: int,
    persona,
    athlete_name: str,
    race_name: str | None,
    current_week: int | None,
    total_weeks: int | None,
    health: dict | None,
    week_miles: int,
    latest_completed_id: int | None,
    today: date,
) -> list[dict]:
    with _quote_lock:
        cached = _quote_cache.get(athlete_id)
        stale = (
            cached is None
            or cached["date"] != today
            or cached["last_completed_id"] != latest_completed_id
        )
        if not stale:
            return cached["quotes"]

    quotes = _generate_quotes(persona, athlete_name, race_name, current_week, total_weeks, health, week_miles)

    if quotes:
        with _quote_lock:
            _quote_cache[athlete_id] = {
                "date": today,
                "last_completed_id": latest_completed_id,
                "quotes": quotes,
            }
    return quotes

bp = Blueprint("magazine", __name__)

# ── Type maps ─────────────────────────────────────────────────────────────────
_TYPE_LABEL = {
    "easy": "Easy", "long_run": "Long Run", "tempo": "Tempo",
    "interval": "Intervals", "recovery": "Recovery", "rest": "Rest",
    "strides": "Strides", "race": "Race", "cross_training": "Cross-Train",
}
_TYPE_COLOR = {
    "easy": "#5a8a62", "long_run": "#5e7e96", "tempo": "#b8673e",
    "interval": "#b8673e", "recovery": "#5a8a62",
    "strides": "#8a7aba", "race": "#b8ff4f", "cross_training": "#8a7868",
}


def _format_pace_mi(pace_min_per_km: float | None) -> str | None:
    if not pace_min_per_km or pace_min_per_km <= 0:
        return None
    pace_mi = pace_min_per_km * 1.60934
    m = int(pace_mi)
    s = round((pace_mi - m) * 60)
    if s == 60:
        m += 1
        s = 0
    return f"{m}:{s:02d}"


def _week_key_from_types(types: set) -> str:
    if "interval" in types:
        return "Interval Training"
    if "tempo" in types and "long_run" in types:
        return "Tempo + Long"
    if "tempo" in types:
        return "Threshold Work"
    if "long_run" in types:
        return "Long Run Week"
    if types - {"rest"}:
        return "Aerobic Base"
    return "Rest / Recovery"


# ── Geographic landmark lookup (closest landmark <= season miles) ────────────
_MILES_LANDMARKS = [
    (26,   "Marathon distance",              "Athens to Marathon"),
    (50,   "An ultra",                       "50-mile classic"),
    (100,  "Boston to Providence",           "MA → RI by foot"),
    (135,  "Badwater 135",                   "Death Valley to Mt Whitney portal"),
    (200,  "NYC to Boston",                  "one-way, full coast"),
    (310,  "Tahoe 200 trail",                "around Lake Tahoe rim"),
    (500,  "NYC to Pittsburgh",              "NY → PA, mountains and rivers"),
    (700,  "John Muir Trail · twice",        "Yosemite to Whitney, both ways"),
    (892,  "San Francisco to Mexico border", "California coast end to end"),
    (1100, "Camino de Santiago · 1.2x",      "Saint-Jean-Pied-de-Port to Santiago"),
    (1500, "Appalachian Trail · GA → PA",    "Springer to the Mason-Dixon"),
    (2000, "NYC to Salt Lake City",          "Atlantic seaboard to the Wasatch"),
    (2189, "Appalachian Trail · end-to-end", "Springer Mountain to Mt Katahdin"),
    (2650, "Pacific Crest Trail · end-to-end","Mexico to Canada along the spine"),
    (3000, "NYC to Los Angeles",             "Atlantic to Pacific"),
    (3100, "Sri Chinmoy 3100",               "longest certified footrace on Earth"),
]


def _miles_landmark(miles: int) -> dict | None:
    if miles <= 0:
        return None
    for threshold, name, sub in reversed(_MILES_LANDMARKS):
        if miles >= threshold:
            return {"miles": threshold, "name": name, "sub": sub}
    first = _MILES_LANDMARKS[0]
    return {"miles": first[0], "name": first[1], "sub": first[2]}


def _excerpt_first_sentences(text: str, n: int = 2) -> str:
    """Take the first `n` sentences of `text`, collapsing whitespace.

    Used to fit the multi-paragraph morning check-in body into the single-line
    serif quote slot on the home card. Falls back to the full (stripped) text
    if no sentence terminators are present.
    """
    stripped = (text or "").strip()
    if not stripped:
        return ""
    sentences = _re.split(r'(?<=[.!?])\s+', stripped)
    excerpt = " ".join(sentences[:n]).strip()
    return excerpt or stripped


def _resolve_morning_message(
    db,
    athlete_id: int,
    persona_greeting: str | None,
    tz: ZoneInfo,
    today_local: date,
) -> tuple[str | None, str | None, str]:
    """Pick the home-card morning message.

    Returns (message, created_at_iso, source) where source is "morning_checkin"
    when today's check-in is being surfaced, or "persona_greeting" when we fell
    back to the persona's static greeting.

    Only morning_checkin notifications whose timestamp falls within the
    athlete's local "today" are eligible — yesterday's check-in is never
    surfaced under today's date, even if it was created less than 24h ago.
    """
    day_start_local = datetime.combine(today_local, time.min, tzinfo=tz)
    day_start_utc = day_start_local.astimezone(timezone.utc).replace(tzinfo=None)
    day_end_utc = (day_start_local + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
    notif = (
        db.query(Notification)
        .filter(
            Notification.athlete_id == athlete_id,
            Notification.kind == "morning_checkin",
            Notification.created_at >= day_start_utc,
            Notification.created_at < day_end_utc,
        )
        .order_by(Notification.created_at.desc())
        .first()
    )
    if notif and notif.body:
        # The Today Card surfaces the leading `**Today.**` tagline. The
        # Morning Readiness section shows the FULL rationale below it, so
        # the same content isn't repeated in two places on the same page.
        from running_coach_ai.coach.today_rationale import strip_today_tagline
        body_without_tagline = strip_today_tagline(notif.body)
        excerpt = _excerpt_first_sentences(body_without_tagline, n=2)
        if excerpt:
            return excerpt, notif.created_at.isoformat(), "morning_checkin"
    fallback = (persona_greeting or "").strip() or None
    return fallback, None, "persona_greeting"


def _persona_tagline(coach_key: str) -> str:
    return {
        "classic": "Elite · Data-Driven",
        "maya":    "Consistency · Low-Friction",
        "jordan":  "Resilience · Injury-Prevention",
    }.get(coach_key or "classic", "Elite · Data-Driven")


@bp.route("/api/coach", methods=["GET", "POST"])
@login_required
def coach_switch():
    """GET — list available personas + currently active key.
       POST {coach_key} — persist a new coach for the logged-in athlete.
    """
    athlete_id = session["athlete_id"]
    if request.method == "GET":
        with get_session() as db:
            athlete = db.get(Athlete, athlete_id)
            if not athlete:
                return jsonify({"error": "Athlete not found"}), 404
            return jsonify({
                "current": resolve_coach_key(athlete.coach_key) or "classic",
                "options": [
                    {"key": p.key, "name": p.name, "description": p.description}
                    for p in PERSONAS.values()
                ],
            })

    data = request.get_json(silent=True) or {}
    raw_key = (data.get("coach_key") or "").strip()
    if not raw_key or not is_valid_coach_key(raw_key):
        return jsonify({"error": "Invalid coach_key"}), 400
    resolved = resolve_coach_key(raw_key)

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            return jsonify({"error": "Athlete not found"}), 404
        if athlete.coach_key != resolved:
            athlete.coach_key = resolved
            db.commit()
            # Invalidate cached quotes so the new persona's voice loads next refresh
            with _quote_lock:
                _quote_cache.pop(athlete_id, None)
            logger.info("Coach switched to '%s' for athlete %d via /api/coach", resolved, athlete_id)
        return jsonify({"current": resolved})


@bp.route("/api/magazine")
@login_required
def magazine():
    athlete_id = session["athlete_id"]

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            return jsonify({"error": "Athlete not found"}), 404

        # Use the athlete's local "today" so the page header, week boundaries,
        # and all "is this past/today/future?" comparisons agree with the
        # athlete's calendar — not the container's UTC clock. (At 10:29pm
        # Thursday EDT, server UTC is already Friday, which would cause the
        # Morning Readiness section to read "Friday" a day early.)
        tz = ZoneInfo(athlete.timezone or "America/New_York")
        today = datetime.now(tz).date()

        goal = (
            db.query(Goal)
            .filter(Goal.athlete_id == athlete_id, Goal.active == True)
            .first()
        )

        plan = None
        if goal:
            plan = (
                db.query(TrainingPlan)
                .filter(TrainingPlan.goal_id == goal.id, TrainingPlan.active == True)
                .first()
            )

        # Today's health snapshot, with the shared today-then-stale-fallback
        # resolver so we stay consistent with /api/today and the morning
        # adapter. Stale rows surface to the UI flagged as such.
        from running_coach_ai.coach.health_lookup import resolve_recent_snapshot
        snap, snap_is_stale = resolve_recent_snapshot(db, athlete_id, today)

        # 7-day HRV trend — compare snap.hrv_score to the 7 days preceding the
        # snap (not the 7 days preceding "today"), so trend math stays correct
        # when we're displaying yesterday's snapshot.
        if snap is not None:
            trend_window_start = snap.date - timedelta(days=7)
            trend_window_end = snap.date
        else:
            trend_window_start = today - timedelta(days=7)
            trend_window_end = today
        prior_snaps = (
            db.query(HealthSnapshot)
            .filter(
                HealthSnapshot.athlete_id == athlete_id,
                HealthSnapshot.date >= trend_window_start,
                HealthSnapshot.date < trend_window_end,
            )
            .all()
        )
        prior_hrv = [s.hrv_score for s in prior_snaps if s.hrv_score is not None]
        hrv_7d_avg = (sum(prior_hrv) / len(prior_hrv)) if prior_hrv else None

        health = None
        if snap:
            sleep_h = round(snap.sleep_duration_seconds / 3600, 1) if snap.sleep_duration_seconds else None
            hrv_trend = None
            if snap.hrv_score is not None and hrv_7d_avg is not None:
                hrv_trend = round(snap.hrv_score - hrv_7d_avg, 1)
            health = {
                "hrv": snap.hrv_score,
                "hrv_trend": hrv_trend,
                "body_battery": snap.body_battery_end,
                "sleep_hours": sleep_h,
                "resting_hr": snap.resting_hr,
                "date_iso": snap.date.isoformat(),
                "is_stale": snap_is_stale,
                "stale_date": (
                    f"{snap.date.strftime('%b')} {snap.date.day}"
                    if snap_is_stale else None
                ),
            }

        # This week (Mon–Sun) — planned + completed mileage
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        week_workouts = (
            db.query(PlannedWorkout)
            .filter(
                PlannedWorkout.athlete_id == athlete_id,
                PlannedWorkout.scheduled_date >= monday,
                PlannedWorkout.scheduled_date <= sunday,
                PlannedWorkout.status != "cancelled",
            )
            .all()
        )
        week_mi = sum(km_to_mi(w.target_distance_km or 0) for w in week_workouts)
        run_count = sum(1 for w in week_workouts if (w.target_distance_km or 0) > 0)

        # This week completed workouts (for day cards and load %)
        week_completed_list = (
            db.query(CompletedWorkout)
            .filter(
                CompletedWorkout.athlete_id == athlete_id,
                CompletedWorkout.date >= monday,
                CompletedWorkout.date <= sunday,
            )
            .all()
        )
        week_completed = {cw.date: cw for cw in week_completed_list}
        week_planned_by_date = {}
        for pw in week_workouts:
            week_planned_by_date.setdefault(pw.scheduled_date, pw)

        # Per-day card data
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        this_week_days = []
        completed_mi_this_week = 0.0
        planned_mi_this_week = 0.0
        for i, dname in enumerate(day_names):
            d = monday + timedelta(days=i)
            pw = week_planned_by_date.get(d)
            cw = week_completed.get(d)
            wtype = (
                (pw.workout_type if pw else None)
                or (cw.planned_workout.workout_type if cw and cw.planned_workout else None)
                or "rest"
            )
            target_mi_val = round(km_to_mi(pw.target_distance_km), 1) if pw and pw.target_distance_km else None
            actual_mi_val = round(km_to_mi(cw.distance_km), 1) if cw and cw.distance_km else None
            actual_pace_val = _format_pace_mi(cw.avg_pace_min_per_km) if cw else None
            if pw and pw.target_distance_km:
                planned_mi_this_week += km_to_mi(pw.target_distance_km)
            if cw and cw.distance_km:
                completed_mi_this_week += km_to_mi(cw.distance_km)
            if d < today:
                status = "done" if cw else ("rest" if not pw else "skipped")
            elif d == today:
                status = "done" if cw else "today"
            else:
                status = "upcoming" if (pw and wtype != "rest") else "rest"
            this_week_days.append({
                "name": dname,
                "date_iso": d.isoformat(),
                "type": wtype,
                "type_label": _TYPE_LABEL.get(wtype, wtype.replace("_", " ").title()),
                "type_color": _TYPE_COLOR.get(wtype, "#5a8a62"),
                "target_mi": target_mi_val,
                "actual_mi": actual_mi_val,
                "actual_pace_mi": actual_pace_val,
                "status": status,
            })

        load_pct = None
        if planned_mi_this_week > 0:
            load_pct = min(100, round(completed_mi_this_week / planned_mi_this_week * 100))

        # %-d isn't supported on Windows; build day-of-month manually
        def _md(d):
            return f"{d.strftime('%b')} {d.day}"

        # Headline: count days that have runs (planned OR completed with distance)
        has_run_days = sum(
            1 for d in this_week_days
            if d["actual_mi"] or d["target_mi"]
        )
        display_mi = round(max(week_mi, completed_mi_this_week))
        display_runs = max(run_count, sum(1 for d in this_week_days if d["actual_mi"]))
        this_week = {
            "label": f"Week of {_md(monday)}–{_md(sunday)}",
            "miles": display_mi,
            "run_count": display_runs,
            "headline": "1 BREAKTHROUGH." if display_runs else "REST WEEK.",
            "days": this_week_days,
            "completed_mi": round(completed_mi_this_week, 1),
            "planned_mi": round(planned_mi_this_week, 1),
            "load_pct": load_pct,
        }

        # Season (year-to-date) miles from completed workouts
        ytd_start = date(today.year, 1, 1)
        ytd_km = (
            db.query(func.coalesce(func.sum(CompletedWorkout.distance_km), 0.0))
            .filter(
                CompletedWorkout.athlete_id == athlete_id,
                CompletedWorkout.date >= ytd_start,
            )
            .scalar()
        )
        season_miles = round(km_to_mi(ytd_km or 0))

        # Remaining planned miles (future non-cancelled workouts with distance set)
        miles_remaining = round(km_to_mi(
            db.query(func.coalesce(func.sum(PlannedWorkout.target_distance_km), 0.0))
            .filter(
                PlannedWorkout.athlete_id == athlete_id,
                PlannedWorkout.scheduled_date > today,
                PlannedWorkout.status != "cancelled",
            )
            .scalar() or 0
        ))
        total_season_miles = season_miles + miles_remaining
        season_pct = round(season_miles / total_season_miles * 100) if total_season_miles > 0 else 0

        # Latest completed workout id — used to detect new workouts for quote cache invalidation
        latest_completed = (
            db.query(CompletedWorkout.id)
            .filter(CompletedWorkout.athlete_id == athlete_id)
            .order_by(CompletedWorkout.id.desc())
            .first()
        )
        latest_completed_id = latest_completed[0] if latest_completed else None

        # ── Most recent completed workout (Last Run section) ──────────────────
        last_cw = (
            db.query(CompletedWorkout)
            .filter(CompletedWorkout.athlete_id == athlete_id)
            .order_by(CompletedWorkout.date.desc())
            .first()
        )
        last_run = None
        if last_cw:
            cw_type = (
                (last_cw.planned_workout.workout_type if last_cw.planned_workout else None) or "easy"
            )
            cw_date = last_cw.date
            analysis_excerpt = None
            if last_cw.coach_analysis:
                sentences = _re.split(r'(?<=[.!?])\s+', last_cw.coach_analysis.strip())
                analysis_excerpt = " ".join(sentences[:2])

            t = last_cw.telemetry

            # HR zones from 1-second telemetry
            hr_zones = None
            if t and t.heart_rate_json:
                lthr = athlete.lthr_bpm or (round(last_cw.max_hr * 0.92) if last_cw.max_hr else None)
                if lthr:
                    bounds = [0, lthr * 0.85, lthr * 0.89, lthr * 0.94, lthr * 0.99]
                    counts = [0] * 5
                    valid = [h for h in t.heart_rate_json if h and h > 0]
                    for h in valid:
                        for i in range(4, -1, -1):
                            if h >= bounds[i]:
                                counts[i] += 1
                                break
                    total = len(valid)
                    if total > 0:
                        hr_zones = [
                            {"label": "Z1", "name": "Recovery",  "pct": round(counts[0] / total * 100), "color": "#5e7e96"},
                            {"label": "Z2", "name": "Aerobic",   "pct": round(counts[1] / total * 100), "color": "#5a8a62"},
                            {"label": "Z3", "name": "Tempo",     "pct": round(counts[2] / total * 100), "color": "#c4a040"},
                            {"label": "Z4", "name": "Threshold", "pct": round(counts[3] / total * 100), "color": "#b8673e"},
                            {"label": "Z5", "name": "VO2max",    "pct": round(counts[4] / total * 100), "color": "#c05040"},
                        ]

            # Lap splits
            laps = None
            if t and t.laps_json:
                laps = [
                    {
                        "num": lap.get("lap_number"),
                        "mi": round(km_to_mi((lap.get("distance_m") or 0) / 1000), 2),
                        "pace": _format_pace_mi(lap.get("avg_pace_min_per_km")),
                        "hr": lap.get("avg_hr"),
                    }
                    for lap in t.laps_json
                ]

            # Elevation profile — downsampled to 60 points, normalised 0–100
            elevation_pts = None
            if t and t.elevation_json and len(t.elevation_json) >= 2:
                raw = t.elevation_json
                step = max(1, len(raw) // 60)
                sampled = raw[::step][:60]
                e_min, e_max = min(sampled), max(sampled)
                rng = max(e_max - e_min, 0.1)
                elevation_pts = [round((v - e_min) / rng * 100, 1) for v in sampled]

            last_run = {
                "date_pretty": f"{cw_date.strftime('%A')}, {cw_date.strftime('%B')} {cw_date.day}",
                "date_iso": cw_date.isoformat(),
                "type": cw_type,
                "type_label": _TYPE_LABEL.get(cw_type, cw_type.replace("_", " ").title()),
                "miles": round(km_to_mi(last_cw.distance_km), 1) if last_cw.distance_km else None,
                "pace_mi": _format_pace_mi(last_cw.avg_pace_min_per_km),
                "avg_hr": last_cw.avg_hr,
                "max_hr": last_cw.max_hr,
                "cadence": last_cw.avg_cadence_spm,
                "power_w": round(last_cw.avg_power_w) if last_cw.avg_power_w else None,
                "elevation_ft": round(last_cw.elevation_gain_m * 3.28084) if last_cw.elevation_gain_m else None,
                "training_load": round(last_cw.training_load) if last_cw.training_load else None,
                "aerobic_effect": round(last_cw.aerobic_training_effect, 1) if last_cw.aerobic_training_effect else None,
                "coach_analysis": analysis_excerpt,
                "coach_analysis_full": last_cw.coach_analysis,
                "hr_zones": hr_zones,
                "laps": laps,
                "elevation_pts": elevation_pts,
            }

        # ── Recent activities (feed: featured run excluded, last ~60 days) ─────
        # Show a meaningful slice of training history rather than just the most
        # recent 5. A typical athlete running 4–6 times/week generates 25–40
        # runs in 60 days; capping at 60 keeps the section bounded on the
        # rare end of the volume distribution.
        recent_activities = []
        if last_cw:
            sixty_days_ago = today - timedelta(days=60)
            feed_cws = (
                db.query(CompletedWorkout)
                .filter(
                    CompletedWorkout.athlete_id == athlete_id,
                    CompletedWorkout.id != last_cw.id,
                    CompletedWorkout.date >= sixty_days_ago,
                )
                .order_by(CompletedWorkout.date.desc())
                .limit(60)
                .all()
            )
            for cw in feed_cws:
                cw_type = (
                    (cw.planned_workout.workout_type if cw.planned_workout else None) or "easy"
                )
                analysis_excerpt = None
                if cw.coach_analysis:
                    sentences = _re.split(r'(?<=[.!?])\s+', cw.coach_analysis.strip())
                    analysis_excerpt = " ".join(sentences[:2])
                t = cw.telemetry
                laps = None
                if t and t.laps_json:
                    laps = [
                        {
                            "num": lap.get("lap_number"),
                            "mi": round(km_to_mi((lap.get("distance_m") or 0) / 1000), 2),
                            "pace": _format_pace_mi(lap.get("avg_pace_min_per_km")),
                            "hr": lap.get("avg_hr"),
                        }
                        for lap in t.laps_json
                    ]
                recent_activities.append({
                    "date_pretty": f"{cw.date.strftime('%A')}, {cw.date.strftime('%B')} {cw.date.day}",
                    "date_iso": cw.date.isoformat(),
                    "type": cw_type,
                    "type_label": _TYPE_LABEL.get(cw_type, cw_type.replace("_", " ").title()),
                    "miles": round(km_to_mi(cw.distance_km), 1) if cw.distance_km else None,
                    "pace_mi": _format_pace_mi(cw.avg_pace_min_per_km),
                    "avg_hr": cw.avg_hr,
                    "max_hr": cw.max_hr,
                    "cadence": cw.avg_cadence_spm,
                    "power_w": round(cw.avg_power_w) if cw.avg_power_w else None,
                    "elevation_ft": round(cw.elevation_gain_m * 3.28084) if cw.elevation_gain_m else None,
                    "training_load": round(cw.training_load) if cw.training_load else None,
                    "coach_analysis": analysis_excerpt,
                    "hr_zones": None,
                    "laps": laps,
                })

        # ── Weekly history — last 8 weeks ───────────────────────────────────────
        eight_wk_ago = monday - timedelta(weeks=8)
        hist_completed = (
            db.query(CompletedWorkout)
            .filter(
                CompletedWorkout.athlete_id == athlete_id,
                CompletedWorkout.date >= eight_wk_ago,
            )
            .all()
        )
        hist_snaps = (
            db.query(HealthSnapshot)
            .filter(
                HealthSnapshot.athlete_id == athlete_id,
                HealthSnapshot.date >= eight_wk_ago,
            )
            .all()
        )
        hist_planned = (
            db.query(PlannedWorkout)
            .filter(
                PlannedWorkout.athlete_id == athlete_id,
                PlannedWorkout.scheduled_date >= eight_wk_ago,
                PlannedWorkout.scheduled_date <= sunday,
                PlannedWorkout.status != "cancelled",
            )
            .all()
        )

        weekly_history = []
        tempo_pace_history = []
        for wk_offset in range(7, -1, -1):  # oldest → newest
            wk_start = monday - timedelta(weeks=wk_offset)
            wk_end = wk_start + timedelta(days=6)
            wk_cws = [c for c in hist_completed if wk_start <= c.date <= wk_end]
            wk_snaps = [s for s in hist_snaps if wk_start <= s.date <= wk_end]
            wk_planned = [p for p in hist_planned if wk_start <= p.scheduled_date <= wk_end]

            mi = round(sum(km_to_mi(c.distance_km or 0) for c in wk_cws))
            hrv_vals = [s.hrv_score for s in wk_snaps if s.hrv_score]
            avg_hrv = round(sum(hrv_vals) / len(hrv_vals)) if hrv_vals else None
            total_load = round(sum(c.training_load or 0 for c in wk_cws))

            types: set = set()
            for c in wk_cws:
                if c.planned_workout:
                    types.add(c.planned_workout.workout_type)
            for p in wk_planned:
                types.add(p.workout_type)
            key = _week_key_from_types(types) if types else ("Building" if mi > 0 else "No Data")

            runs = []
            for c in sorted(wk_cws, key=lambda x: x.date):
                ctype = (c.planned_workout.workout_type if c.planned_workout else None) or "run"
                mi_str = f"{round(km_to_mi(c.distance_km), 1)}mi" if c.distance_km else ""
                runs.append({
                    "d": c.date.strftime("%a"),
                    "r": f"{_TYPE_LABEL.get(ctype, ctype.title())} {mi_str}".strip(),
                })

            weekly_history.append({
                "wk_label": f"{wk_start.strftime('%b')} {wk_start.day}",
                "wk_start_iso": wk_start.isoformat(),
                "mi": mi,
                "hrv": avg_hrv,
                "load": total_load,
                "key": key,
                "is_current": wk_offset == 0,
                "runs": runs,
            })

            tempo_cws = [
                c for c in wk_cws
                if c.planned_workout
                and c.planned_workout.workout_type in ("tempo", "interval", "threshold")
                and c.avg_pace_min_per_km
            ]
            if tempo_cws:
                avg_pace_km = sum(c.avg_pace_min_per_km for c in tempo_cws) / len(tempo_cws)
                pace_str = _format_pace_mi(avg_pace_km)
                parts = pace_str.split(":") if pace_str else ["0", "00"]
                secs = int(parts[0]) * 60 + int(parts[1])
                tempo_pace_history.append({
                    "wk_label": f"{wk_start.strftime('%b')} {wk_start.day}",
                    "pace": pace_str,
                    "secs": secs,
                    "is_current": wk_offset == 0,
                })

        # Training week within plan
        current_week = None
        total_weeks = None
        if plan and plan.valid_from and plan.valid_to:
            elapsed_days = (today - plan.valid_from).days
            current_week = max(1, (elapsed_days // 7) + 1)
            total_weeks = max(1, ((plan.valid_to - plan.valid_from).days // 7) + 1)
            current_week = min(current_week, total_weeks)

        # ── Flip card data ────────────────────────────────────────────────────
        best_tempo_cw = (
            db.query(CompletedWorkout)
            .join(PlannedWorkout, CompletedWorkout.planned_workout_id == PlannedWorkout.id, isouter=True)
            .filter(
                CompletedWorkout.athlete_id == athlete_id,
                PlannedWorkout.workout_type.in_(["tempo", "interval", "threshold"]),
                CompletedWorkout.avg_pace_min_per_km.isnot(None),
            )
            .order_by(CompletedWorkout.avg_pace_min_per_km.asc())
            .first()
        )
        flipcards = {
            "phase": (plan.current_phase.replace("_", " ").title() if plan and plan.current_phase else "Base"),
            "week_label": f"Week {current_week}" if current_week else "Training",
            "tempo_pr": _format_pace_mi(best_tempo_cw.avg_pace_min_per_km) if best_tempo_cw else None,
        }

        # ── Upcoming planned workouts (race section) ──────────────────────────
        upcoming_planned = (
            db.query(PlannedWorkout)
            .filter(
                PlannedWorkout.athlete_id == athlete_id,
                PlannedWorkout.scheduled_date >= today,
                PlannedWorkout.status.notin_(["cancelled", "skipped"]),
            )
            .order_by(PlannedWorkout.scheduled_date.asc())
            .limit(4)
            .all()
        )
        race_upcoming = []
        for pw in upcoming_planned:
            type_lbl = _TYPE_LABEL.get(pw.workout_type, pw.workout_type.replace("_", " ").title())
            mi_str = f"{round(km_to_mi(pw.target_distance_km), 1)} mi" if pw.target_distance_km else ""
            dur_str = f"{pw.target_duration_seconds // 60} min" if pw.target_duration_seconds else ""
            sub = " · ".join(filter(None, [type_lbl, mi_str or dur_str]))
            race_upcoming.append({
                "id": pw.id,
                "day": f"{pw.scheduled_date.strftime('%A')}, {pw.scheduled_date.strftime('%b')} {pw.scheduled_date.day}",
                "headline": mi_str or type_lbl,
                "sub": sub,
                "date_iso": pw.scheduled_date.isoformat(),
            })

        # Race
        race = None
        if goal:
            days = (goal.race_date - today).days if goal.race_date else None
            race_pretty = (
                f"{goal.race_date.strftime('%B')} {goal.race_date.day}, {goal.race_date.year}"
                if goal.race_date else None
            )
            race = {
                "name": goal.race_name or goal.race_type.replace("_", " ").title(),
                "date_iso": goal.race_date.isoformat() if goal.race_date else None,
                "date_pretty": race_pretty,
                "days_to_race": days,
                "upcoming": race_upcoming,
            }

        # Coach — `persona.name` is the display form, e.g. "Coach Alex"
        persona = get_persona(athlete.coach_key)
        first = (persona.name or "Coach Alex").replace("Coach ", "").strip() or "Alex"
        race_name_for_quotes = race["name"] if race else None
        coach_quotes = _get_quotes(
            athlete_id=athlete_id,
            persona=persona,
            athlete_name=athlete.name or "Athlete",
            race_name=race_name_for_quotes,
            current_week=current_week,
            total_weeks=total_weeks,
            health=health,
            week_miles=round(week_mi),
            latest_completed_id=latest_completed_id,
            today=today,
        )
        morning_msg, morning_msg_at, morning_msg_source = _resolve_morning_message(
            db, athlete_id, persona.greeting, tz, today,
        )

        coach = {
            "key":     athlete.coach_key or "classic",
            "name":    first,
            "display": persona.name,
            "tagline": _persona_tagline(athlete.coach_key or "classic"),
            "message": morning_msg,
            "message_at": morning_msg_at,
            "message_source": morning_msg_source,
            "quotes":  coach_quotes,
        }

        today_pretty = f"{today.strftime('%A, %B')} {today.day}, {today.year}"
        month_pretty = today.strftime("%B %Y")

        # ── Calendar (Jan 1 → race day) ─────────────────────────────────────
        cal_start = date(today.year, 1, 1)
        if goal and goal.race_date and goal.race_date >= today:
            cal_end = goal.race_date
        else:
            cal_end = today + timedelta(days=7 * 8)
        cal_planned = (
            db.query(PlannedWorkout)
            .filter(
                PlannedWorkout.athlete_id == athlete_id,
                PlannedWorkout.scheduled_date >= cal_start,
                PlannedWorkout.scheduled_date <= cal_end,
                PlannedWorkout.status != "cancelled",
            )
            .all()
        )
        cal_completed = (
            db.query(CompletedWorkout)
            .filter(
                CompletedWorkout.athlete_id == athlete_id,
                CompletedWorkout.date >= cal_start,
                CompletedWorkout.date <= cal_end,
            )
            .all()
        )
        cal_planned_by_date = {pw.scheduled_date: pw for pw in cal_planned}
        cal_completed_by_date = {cw.date: cw for cw in cal_completed}

        calendar_map = {}
        cur = cal_start
        while cur <= cal_end:
            pw = cal_planned_by_date.get(cur)
            cw = cal_completed_by_date.get(cur)
            if pw or cw:
                wtype = (
                    (pw.workout_type if pw else None)
                    or (cw.planned_workout.workout_type if cw and cw.planned_workout else None)
                    or "easy"
                )
                target_mi_val = round(km_to_mi(pw.target_distance_km), 1) if pw and pw.target_distance_km else None
                actual_mi_val = round(km_to_mi(cw.distance_km), 1) if cw and cw.distance_km else None
                actual_pace_val = _format_pace_mi(cw.avg_pace_min_per_km) if cw else None
                target_pace_val = _format_pace_mi(pw.target_pace_min_per_km) if pw and pw.target_pace_min_per_km else None
                target_dur_min = (
                    f"{pw.target_duration_seconds // 60}:{pw.target_duration_seconds % 60:02d}"
                    if pw and pw.target_duration_seconds else None
                )
                if cur < today:
                    status = "done" if cw else ("rest" if (pw and pw.workout_type == "rest") else "skipped")
                elif cur == today:
                    status = "done" if cw else "today"
                else:
                    status = "upcoming"
                # Intensity heuristic: 1=easy/recovery, 2=long/strength, 3=tempo/workout/interval, 4=race
                _intensity_map = {
                    "easy": 1, "recovery": 1, "rest": 0, "strides": 1,
                    "long_run": 2, "strength": 2, "cross_training": 1,
                    "tempo": 3, "workout": 3, "interval": 3, "threshold": 3,
                    "race": 4,
                }
                tss = round(cw.training_load) if cw and cw.training_load else 0
                calendar_map[cur.isoformat()] = {
                    "type": wtype,
                    "type_label": _TYPE_LABEL.get(wtype, wtype.replace("_", " ").title()),
                    "target_mi": target_mi_val,
                    "target_pace_mi": target_pace_val,
                    "target_dur": target_dur_min,
                    "actual_mi": actual_mi_val,
                    "actual_pace_mi": actual_pace_val,
                    "tss": tss,
                    "intensity": _intensity_map.get(wtype, 1),
                    "status": status,
                    "is_today": cur == today,
                    "name": (pw.workout_name if pw and pw.workout_name else _TYPE_LABEL.get(wtype, "Run")),
                }
            cur += timedelta(days=1)

        calendar_range = {"start_iso": cal_start.isoformat(), "end_iso": cal_end.isoformat()}

        # ── Health history (last 60 days, daily) ────────────────────────────
        hh_start = today - timedelta(days=60)
        hh_snaps = (
            db.query(HealthSnapshot)
            .filter(
                HealthSnapshot.athlete_id == athlete_id,
                HealthSnapshot.date >= hh_start,
                HealthSnapshot.date <= today,
            )
            .order_by(HealthSnapshot.date.asc())
            .all()
        )
        health_history = {
            "hrv":          [{"date": s.date.isoformat(), "value": s.hrv_score} for s in hh_snaps if s.hrv_score is not None],
            "body_battery": [{"date": s.date.isoformat(), "value": s.body_battery_start} for s in hh_snaps if s.body_battery_start is not None],
            "sleep_hours":  [{"date": s.date.isoformat(), "value": round(s.sleep_duration_seconds / 3600, 1)} for s in hh_snaps if s.sleep_duration_seconds],
            "resting_hr":   [{"date": s.date.isoformat(), "value": s.resting_hr} for s in hh_snaps if s.resting_hr is not None],
            "range":        {"start_iso": hh_start.isoformat(), "end_iso": today.isoformat()},
        }

        # ── Geographic landmark for season miles ────────────────────────────
        miles_landmark = _miles_landmark(season_miles)

        # ── Story feature block ─────────────────────────────────────────────
        story_block: dict
        if not athlete.story_opt_in:
            story_block = {"feature_enabled": False}
        else:
            from running_coach_ai.database.models import AthleteStory as _AS
            current = (
                db.query(_AS)
                .filter(_AS.athlete_id == athlete_id, _AS.deleted_at.is_(None))
                .order_by(_AS.created_at.desc())
                .first()
            )
            current_payload = None
            if current is not None:
                from running_coach_ai.web.api.stories import _serialise_story_full
                current_payload = _serialise_story_full(current, db)
            story_block = {
                "feature_enabled": True,
                "needs_intro_modal": athlete.story_intro_seen_at is None,
                "current_story": current_payload,
            }

        return jsonify({
            "athlete": {
                "id": athlete.id,
                "name": athlete.name,
            },
            "today_iso":    today.isoformat(),
            "today_pretty": today_pretty,
            "month_pretty": month_pretty,
            "training": {
                "current_week": current_week,
                "total_weeks":  total_weeks,
                "phase": flipcards["phase"],
            },
            "season": {
                "miles_completed":  season_miles,
                "miles_remaining":  miles_remaining,
                "total_miles":      total_season_miles,
                "pct_complete":     season_pct,
            },
            "this_week":      this_week,
            "health":         health,
            "race":           race,
            "coach":          coach,
            "last_run":         last_run,
            "recent_activities": recent_activities,
            "weekly_history": weekly_history,
            "tempo_pace_history": tempo_pace_history,
            "flipcards":      flipcards,
            "calendar":       calendar_map,
            "calendar_range": calendar_range,
            "health_history": health_history,
            "miles_landmark": miles_landmark,
            "story":          story_block,
        })
