"""GET /api/activities and POST /api/activities/{id}/feedback endpoints."""

import logging
from datetime import date, timedelta

from flask import Blueprint, jsonify, request, session

from running_coach_ai.coach.persona import format_pace_mi, km_to_mi
from running_coach_ai.database.models import Athlete, CompletedWorkout, RunFeedback
from running_coach_ai.database.session import get_session
from running_coach_ai.web.auth import login_required

logger = logging.getLogger(__name__)

bp = Blueprint("activities", __name__)

_FEET_PER_METER = 3.28084


def _format_duration(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def _activity_cell(cw: CompletedWorkout) -> dict:
    dist_mi = km_to_mi(cw.distance_km) if cw.distance_km else None
    pace_str = format_pace_mi(cw.avg_pace_min_per_km) if cw.avg_pace_min_per_km else None

    is_time_based = bool(cw.planned_workout and not cw.planned_workout.target_distance_km)
    cell: dict = {
        "id": cw.id,
        "date": cw.date.isoformat() if cw.date else None,
        "label": (cw.planned_workout.workout_name if cw.planned_workout and cw.planned_workout.workout_name
                  else (cw.activity_type or "Run").replace("_", " ").title()),
        "is_time_based": is_time_based,
        "distance_mi": round(dist_mi, 1) if dist_mi else None,
        "duration": _format_duration(cw.duration_seconds) if cw.duration_seconds else None,
        "avg_pace": pace_str,
        "avg_hr": cw.avg_hr,
    }

    # Biomechanics
    bio = {}
    if cw.avg_cadence_spm:
        bio["cadence_spm"] = cw.avg_cadence_spm
    if cw.avg_ground_contact_time_ms:
        bio["ground_contact_ms"] = round(cw.avg_ground_contact_time_ms)
    if cw.avg_vertical_oscillation_cm:
        bio["vertical_oscillation_cm"] = round(cw.avg_vertical_oscillation_cm, 1)
    if cw.avg_power_w:
        bio["power_w"] = round(cw.avg_power_w)
    if bio:
        cell["biomechanics"] = bio

    # Coach analysis — omit key if null
    if cw.coach_analysis:
        cell["coach_analysis"] = cw.coach_analysis

    # Feedback from RunFeedback relationship
    if cw.run_feedback:
        fb = cw.run_feedback
        cell["feedback"] = {
            "feel_score": fb.feel_score,
            "rpe": fb.rpe,
            "notes": fb.notes,
            "saved": True,
        }

    return cell


@bp.route("/api/activities")
@login_required
def activities():
    athlete_id = session["athlete_id"]
    today = date.today()

    week_start_str = request.args.get("week_start")
    if week_start_str:
        try:
            week_start = date.fromisoformat(week_start_str)
        except ValueError:
            week_start = today - timedelta(days=today.weekday())
    else:
        week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            return jsonify({"error": "Athlete not found"}), 404

        base_q = db.query(CompletedWorkout).filter(CompletedWorkout.athlete_id == athlete_id)
        total = base_q.count()

        rows = (
            base_q
            .filter(
                CompletedWorkout.date >= week_start,
                CompletedWorkout.date <= week_end,
            )
            .order_by(CompletedWorkout.date.desc())
            .all()
        )

        week_miles = sum(km_to_mi(r.distance_km) for r in rows if r.distance_km)

        ytd_start = date(today.year, 1, 1)
        ytd_rows = db.query(CompletedWorkout).filter(
            CompletedWorkout.athlete_id == athlete_id,
            CompletedWorkout.date >= ytd_start,
        ).all()
        ytd_miles = sum(km_to_mi(r.distance_km) for r in ytd_rows if r.distance_km) if ytd_rows else 0.0

        seven_days_ago = today - timedelta(days=7)
        recent_rows = db.query(CompletedWorkout).filter(
            CompletedWorkout.athlete_id == athlete_id,
            CompletedWorkout.date >= seven_days_ago,
        ).all()
        recent_paces = [r.avg_pace_min_per_km for r in recent_rows if r.avg_pace_min_per_km]
        avg_pace_7d = format_pace_mi(sum(recent_paces) / len(recent_paces)) if recent_paces else None

        return jsonify({
            "summary": {
                "week_miles": round(week_miles, 1),
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "ytd_miles": round(ytd_miles, 1),
                "avg_pace_7d": avg_pace_7d,
                "training_load": None,
            },
            "activities": [_activity_cell(r) for r in rows],
            "total": total,
        })


@bp.route("/api/activities/<int:activity_id>/feedback", methods=["POST"])
@login_required
def activity_feedback(activity_id: int):
    athlete_id = session["athlete_id"]
    data = request.get_json(silent=True) or {}

    feel_score = data.get("feel_score")
    rpe = data.get("rpe")
    notes = data.get("notes", "")

    if feel_score is not None and not (0 <= feel_score <= 4):
        return jsonify({"error": "feel_score must be between 0 and 4"}), 422
    if rpe is not None and not (1 <= rpe <= 10):
        return jsonify({"error": "rpe must be between 1 and 10"}), 422
    if notes and len(notes) > 1000:
        return jsonify({"error": "notes must be 1000 characters or fewer"}), 422

    with get_session() as db:
        cw = (
            db.query(CompletedWorkout)
            .filter(CompletedWorkout.id == activity_id, CompletedWorkout.athlete_id == athlete_id)
            .first()
        )
        if not cw:
            return jsonify({"error": "Activity not found"}), 404

        existing = db.query(RunFeedback).filter(RunFeedback.completed_workout_id == activity_id).first()
        if existing:
            if feel_score is not None:
                existing.feel_score = feel_score
            if rpe is not None:
                existing.rpe = rpe
            if notes is not None:
                existing.notes = notes
        else:
            from datetime import datetime
            fb = RunFeedback(
                athlete_id=athlete_id,
                completed_workout_id=activity_id,
                feel_score=feel_score,
                rpe=rpe,
                notes=notes,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(fb)

        return jsonify({"ok": True})
