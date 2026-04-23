"""GET /api/dashboard endpoint."""

import logging
from datetime import date, timedelta

from flask import Blueprint, jsonify, session

from running_coach_ai.coach.persona import format_miles, format_pace_mi, km_to_mi
from running_coach_ai.coach.personas import get_persona
from running_coach_ai.database.models import Athlete, Goal, HealthSnapshot, PlannedWorkout
from running_coach_ai.database.session import get_session
from running_coach_ai.web.auth import login_required

logger = logging.getLogger(__name__)

bp = Blueprint("dashboard", __name__)

_WORKOUT_COLORS = {
    "easy": "#5a8a62",
    "long_run": "#3a7a52",
    "tempo": "#b8673e",
    "intervals": "#c0392b",
    "strides": "#8e44ad",
    "cross_train": "#2980b9",
    "rest": "#888888",
    "race": "#f39c12",
}


def _duration_str(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}:{m:02d}"


@bp.route("/api/dashboard")
@login_required
def dashboard():
    athlete_id = session["athlete_id"]
    today = date.today()

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            return jsonify({"error": "Athlete not found"}), 404

        # Active goal
        goal = (
            db.query(Goal)
            .filter(Goal.athlete_id == athlete_id, Goal.active == True)
            .first()
        )

        # Health snapshot — today and 7-day window for trends
        today_snap = (
            db.query(HealthSnapshot)
            .filter(HealthSnapshot.athlete_id == athlete_id, HealthSnapshot.date == today)
            .first()
        )
        week_ago = today - timedelta(days=7)
        recent_snaps = (
            db.query(HealthSnapshot)
            .filter(
                HealthSnapshot.athlete_id == athlete_id,
                HealthSnapshot.date >= week_ago,
                HealthSnapshot.date < today,
            )
            .order_by(HealthSnapshot.date)
            .limit(7)
            .all()
        )

        def _avg(snaps, attr):
            vals = [getattr(s, attr) for s in snaps if getattr(s, attr) is not None]
            return (sum(vals) / len(vals)) if vals else None

        hrv_7d_avg = _avg(recent_snaps, "hrv_score")
        bb_7d_avg = _avg(recent_snaps, "body_battery_end")
        sleep_7d_avg = _avg(recent_snaps, "sleep_duration_seconds")
        rhr_7d_avg = _avg(recent_snaps, "resting_hr")

        def _trend(current, avg):
            if current is None or avg is None:
                return None
            return round(current - avg, 1)

        health = {}
        if today_snap:
            hrv = today_snap.hrv_score
            bb = today_snap.body_battery_end
            sleep_sec = today_snap.sleep_duration_seconds
            rhr = today_snap.resting_hr
            health = {
                "hrv_score": hrv,
                "hrv_trend": _trend(hrv, hrv_7d_avg),
                "body_battery": bb,
                "body_battery_trend": _trend(bb, bb_7d_avg),
                "sleep_hours": round(sleep_sec / 3600, 1) if sleep_sec else None,
                "sleep_trend": round((sleep_sec - sleep_7d_avg) / 3600, 1) if sleep_sec and sleep_7d_avg else None,
                "resting_hr": rhr,
                "resting_hr_trend": _trend(rhr, rhr_7d_avg),
            }

        # Today's planned workout
        today_workout_row = (
            db.query(PlannedWorkout)
            .filter(
                PlannedWorkout.athlete_id == athlete_id,
                PlannedWorkout.scheduled_date == today,
                PlannedWorkout.status != "cancelled",
            )
            .first()
        )
        today_workout = None
        if today_workout_row:
            dist_mi = km_to_mi(today_workout_row.target_distance_km) if today_workout_row.target_distance_km else None
            pace_str = format_pace_mi(today_workout_row.target_pace_min_per_km) if today_workout_row.target_pace_min_per_km else None
            today_workout = {
                "id": today_workout_row.id,
                "type": today_workout_row.workout_type,
                "name": today_workout_row.workout_name or today_workout_row.workout_type.replace("_", " ").title(),
                "target_distance_mi": round(dist_mi, 1) if dist_mi else None,
                "target_pace_min_per_mi": pace_str,
                "garmin_synced": bool(today_workout_row.garmin_workout_id),
                "coach_notes": today_workout_row.description,
            }

        # Coach message (persona greeting)
        persona = get_persona(athlete.coach_key)
        coach_message = persona.greeting

        # Week strip (Mon–Sun this week)
        monday = today - timedelta(days=today.weekday())
        week_days = [monday + timedelta(days=i) for i in range(7)]
        day_workouts = (
            db.query(PlannedWorkout)
            .filter(
                PlannedWorkout.athlete_id == athlete_id,
                PlannedWorkout.scheduled_date >= week_days[0],
                PlannedWorkout.scheduled_date <= week_days[-1],
                PlannedWorkout.status != "cancelled",
            )
            .all()
        )
        day_map = {w.scheduled_date: w for w in day_workouts}
        week_strip = []
        for d in week_days:
            w = day_map.get(d)
            if w:
                dist_mi = km_to_mi(w.target_distance_km) if w.target_distance_km else None
                name = w.workout_name or w.workout_type.replace("_", " ").title()
                label = f"{round(dist_mi)} mi · {name}" if dist_mi else name
                week_strip.append({
                    "day": d.strftime("%a"),
                    "type": w.workout_type,
                    "label": label,
                    "color": _WORKOUT_COLORS.get(w.workout_type, "#888888"),
                    "done": w.status == "completed",
                    "is_today": d == today,
                })
            else:
                week_strip.append({
                    "day": d.strftime("%a"),
                    "type": "rest",
                    "label": "Rest",
                    "color": _WORKOUT_COLORS["rest"],
                    "done": d < today,
                    "is_today": d == today,
                })

        # 8-week chart data
        eight_weeks_ago = today - timedelta(weeks=8)
        eight_week_snaps = (
            db.query(HealthSnapshot)
            .filter(
                HealthSnapshot.athlete_id == athlete_id,
                HealthSnapshot.date >= eight_weeks_ago,
            )
            .order_by(HealthSnapshot.date)
            .all()
        )

        def _weekly_rollup(snaps, attr, agg="sum"):
            weeks = {}
            for s in snaps:
                week_mon = s.date - timedelta(days=s.date.weekday())
                val = getattr(s, attr, None)
                if val is None:
                    continue
                if week_mon not in weeks:
                    weeks[week_mon] = []
                weeks[week_mon].append(val)
            sorted_weeks = sorted(weeks.keys())
            if agg == "sum":
                return [sum(weeks[w]) for w in sorted_weeks], [f"W{w.isocalendar()[1]}" for w in sorted_weeks]
            else:
                return [round(sum(weeks[w]) / len(weeks[w]), 1) for w in sorted_weeks], [f"W{w.isocalendar()[1]}" for w in sorted_weeks]

        hrv_vals, week_labels = _weekly_rollup(eight_week_snaps, "hrv_score", "avg")

        # Weekly volume from PlannedWorkout (completed only)
        eight_week_workouts = (
            db.query(PlannedWorkout)
            .filter(
                PlannedWorkout.athlete_id == athlete_id,
                PlannedWorkout.scheduled_date >= eight_weeks_ago,
                PlannedWorkout.status == "completed",
            )
            .all()
        )
        vol_weeks: dict = {}
        for w in eight_week_workouts:
            week_mon = w.scheduled_date - timedelta(days=w.scheduled_date.weekday())
            dist = km_to_mi(w.target_distance_km) if w.target_distance_km else 0
            vol_weeks[week_mon] = vol_weeks.get(week_mon, 0) + dist

        sorted_vol_weeks = sorted(vol_weeks.keys())
        weekly_volume = [round(vol_weeks[w]) for w in sorted_vol_weeks]
        if not week_labels:
            week_labels = [f"W{w.isocalendar()[1]}" for w in sorted_vol_weeks]

        # Athlete section
        race_date = goal.race_date if goal else None
        days_to_race = (race_date - today).days if race_date else None

        athlete_section = {
            "name": athlete.name,
            "coach_key": athlete.coach_key or "classic",
            "race_name": goal.race_name if goal else None,
            "race_date": race_date.isoformat() if race_date else None,
            "days_to_race": days_to_race,
        }

        return jsonify({
            "athlete": athlete_section,
            "health": health,
            "today_workout": today_workout,
            "coach_message": coach_message,
            "week_strip": week_strip,
            "charts": {
                "weekly_volume_8w": weekly_volume,
                "hrv_8w": hrv_vals,
                "week_labels": week_labels,
            },
        })
