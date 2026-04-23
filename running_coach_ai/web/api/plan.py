"""GET /api/plan and POST /api/plan/sync endpoints."""

import logging
from calendar import monthrange
from datetime import date, timedelta

from flask import Blueprint, jsonify, request, session

from running_coach_ai.coach.persona import format_pace_mi, km_to_mi
from running_coach_ai.database.models import Athlete, CompletedWorkout, PlannedWorkout
from running_coach_ai.database.session import get_session
from running_coach_ai.web.auth import login_required
from running_coach_ai.web.events import web_event

logger = logging.getLogger(__name__)

bp = Blueprint("plan", __name__)


def _cell(workout, today: date) -> dict:
    dist_mi = km_to_mi(workout.target_distance_km) if workout.target_distance_km else None
    pace_str = format_pace_mi(workout.target_pace_min_per_km) if workout.target_pace_min_per_km else None
    dur_min = max(5, round(workout.target_duration_seconds / 300) * 5) if workout.target_duration_seconds else None

    cell = {
        "date": workout.scheduled_date.isoformat(),
        "type": workout.workout_type,
        "label": workout.workout_name or workout.workout_type.replace("_", " ").title(),
        "distance_mi": round(dist_mi, 1) if dist_mi else None,
        "duration_min": dur_min,
        "target_pace": pace_str,
        "description": workout.description,
        "done": workout.status == "completed" or workout.completed_workout is not None,
        "intensity": {"rest": 0, "easy": 1, "strides": 2, "long_run": 2, "cross_train": 1, "tempo": 3, "intervals": 4, "race": 5}.get(workout.workout_type, 1),
    }

    # Actual data from completed workout
    if workout.completed_workout:
        cw = workout.completed_workout
        if cw.avg_pace_min_per_km:
            cell["actual_pace"] = format_pace_mi(cw.avg_pace_min_per_km)

    return cell


def _completed_cell(cw: CompletedWorkout) -> dict:
    dist_mi = km_to_mi(cw.distance_km) if cw.distance_km else None
    pace_str = format_pace_mi(cw.avg_pace_min_per_km) if cw.avg_pace_min_per_km else None
    label = (cw.activity_type or "run").replace("_", " ").title()
    return {
        "date": cw.date.isoformat(),
        "type": cw.activity_type or "easy",
        "label": label,
        "distance_mi": round(dist_mi, 1) if dist_mi else None,
        "target_pace": None,
        "actual_pace": pace_str,
        "done": True,
        "unplanned": True,
        "intensity": 1,
    }


@bp.route("/api/plan")
@login_required
def plan():
    athlete_id = session["athlete_id"]
    today = date.today()

    month_str = request.args.get("month") or today.strftime("%Y-%m")
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
    except (ValueError, IndexError):
        year, month = today.year, today.month

    _, last_day = monthrange(year, month)
    first = date(year, month, 1)
    last = date(year, month, last_day)

    # Expand to Monday-first grid boundaries
    grid_start = first - timedelta(days=first.weekday())
    grid_end = last + timedelta(days=6 - last.weekday())

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            return jsonify({"error": "Athlete not found"}), 404

        workouts = (
            db.query(PlannedWorkout)
            .filter(
                PlannedWorkout.athlete_id == athlete_id,
                PlannedWorkout.scheduled_date >= grid_start,
                PlannedWorkout.scheduled_date <= grid_end,
                PlannedWorkout.status != "cancelled",
            )
            .all()
        )

        # Deduplicate: multiple plan generations can leave several rows per date.
        # Prefer 'modified' (coach explicitly updated), then highest id (most recent).
        workout_map: dict = {}
        for w in workouts:
            existing = workout_map.get(w.scheduled_date)
            if not existing:
                workout_map[w.scheduled_date] = w
            elif w.status == "modified" and existing.status != "modified":
                workout_map[w.scheduled_date] = w
            elif w.status == existing.status and w.id > existing.id:
                workout_map[w.scheduled_date] = w

        # Also fetch completed workouts that have no linked planned workout
        # (unplanned runs, runs on rest days, etc.)
        unlinked = (
            db.query(CompletedWorkout)
            .filter(
                CompletedWorkout.athlete_id == athlete_id,
                CompletedWorkout.date >= grid_start,
                CompletedWorkout.date <= grid_end,
                CompletedWorkout.planned_workout_id.is_(None),
            )
            .order_by(CompletedWorkout.date, CompletedWorkout.id)
            .all()
        )
        # Keyed by date — keep the first (earliest) unlinked run per day
        unlinked_map: dict = {}
        for cw in unlinked:
            if cw.date not in unlinked_map:
                unlinked_map[cw.date] = cw

        # Build week rows
        weeks = []
        week_labels = []
        d = grid_start
        active_week_index = 0
        today_col = None

        while d <= grid_end:
            week_mon = d
            row = []
            for col in range(7):
                cell_date = week_mon + timedelta(days=col)
                w = workout_map.get(cell_date)
                if w:
                    row.append(_cell(w, today))
                elif cell_date in unlinked_map:
                    row.append(_completed_cell(unlinked_map[cell_date]))
                else:
                    row.append(None)

                if cell_date == today:
                    today_col = col

            week_label = f"{week_mon.day} {week_mon.strftime('%b')}" if week_mon.month == month else f"{week_mon.strftime('%b')} {week_mon.day}"
            if week_mon <= today <= week_mon + timedelta(days=6):
                week_label += " · This week"
                active_week_index = len(weeks)

            weeks.append(row)
            week_labels.append(week_label)
            d = week_mon + timedelta(weeks=1)

        import calendar
        month_label = f"{calendar.month_name[month]} {year}"

        return jsonify({
            "month_label": month_label,
            "active_week_index": active_week_index,
            "today_col": today_col,
            "week_labels": week_labels,
            "weeks": weeks,
        })


@bp.route("/api/plan/sync", methods=["POST"])
@login_required
def plan_sync():
    athlete_id = session["athlete_id"]

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            return jsonify({"ok": False, "error": "Athlete not found"}), 404

        if not athlete.garmin_email or not athlete.garmin_password_encrypted:
            return jsonify({"ok": False, "error": "No Garmin credentials — re-authenticate via Slack"}), 400

        try:
            synced_count = sync_week_to_garmin(athlete, db)
            web_event(
                severity="info", category="garmin",
                message=f"Garmin sync triggered from web: {synced_count} workouts",
                athlete_id=athlete_id,
            )
            return jsonify({"ok": True, "synced_count": synced_count})
        except Exception as exc:
            err_msg = str(exc)
            logger.warning("Garmin sync failed for athlete %s: %s", athlete_id, err_msg)
            web_event(
                severity="error", category="garmin",
                message=f"Garmin sync failed for athlete {athlete_id}: {err_msg}",
                athlete_id=athlete_id,
                details={"error_type": type(exc).__name__},
            )
            return jsonify({"ok": False, "error": err_msg}), 500


def sync_week_to_garmin(athlete, db_session) -> int:
    """Trigger a full Garmin resync for all upcoming workouts. Returns count synced."""
    from datetime import timedelta
    from running_coach_ai.garmin.client import get_garmin_client
    from running_coach_ai.garmin.workout_builder import sync_week_to_garmin as _sync

    today = date.today()
    upcoming_workouts = (
        db_session.query(PlannedWorkout)
        .filter(
            PlannedWorkout.athlete_id == athlete.id,
            PlannedWorkout.scheduled_date >= today,
            PlannedWorkout.status.in_(["planned", "modified"]),
        )
        .all()
    )

    if not upcoming_workouts:
        return 0

    # Get unique weeks
    weeks = sorted({w.scheduled_date - timedelta(days=w.scheduled_date.weekday()) for w in upcoming_workouts})
    garmin = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)

    total = 0
    for week_start in weeks:
        uploaded, _, _ = _sync(
            athlete_id=athlete.id,
            email=athlete.garmin_email,
            encrypted_password=athlete.garmin_password_encrypted,
            week_start_date=week_start,
            db_session=db_session,
            garmin_client=garmin,
        )
        total += uploaded

    return total
