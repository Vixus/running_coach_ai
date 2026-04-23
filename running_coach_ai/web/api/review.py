"""GET /api/review endpoint."""

from flask import Blueprint, jsonify, request, session

from running_coach_ai.database.models import Athlete, WeeklyReviewSummary
from running_coach_ai.database.session import get_session
from running_coach_ai.web.auth import login_required

bp = Blueprint("review", __name__)


@bp.route("/api/review")
@login_required
def get_review():
    athlete_id = session["athlete_id"]

    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        offset = 0

    with get_session() as db:
        athlete = db.get(Athlete, athlete_id)
        if not athlete:
            return jsonify({"error": "Athlete not found"}), 404

        total = (
            db.query(WeeklyReviewSummary)
            .filter(WeeklyReviewSummary.athlete_id == athlete_id)
            .count()
        )

        review = (
            db.query(WeeklyReviewSummary)
            .filter(WeeklyReviewSummary.athlete_id == athlete_id)
            .order_by(WeeklyReviewSummary.week_start_date.desc())
            .offset(offset)
            .first()
        )

        if not review:
            return jsonify({
                "error": "No weekly review available yet. Next generation: Sunday 20:00."
            }), 404

        return jsonify({
            "week_start": review.week_start_date.isoformat(),
            "has_older": offset < total - 1,
            "has_newer": offset > 0,
            "metrics": {
                "total_miles": review.total_miles,
                "elevation_ft": review.elevation_gain_ft,
                "avg_hrv": review.avg_hrv,
                "total_tss": review.total_tss,
            },
            "narrative": review.narrative,
            "daily_volume": review.daily_volume_json or [],
            "body_battery_8w": review.body_battery_json or [],
            "next_week": review.next_week_json or [],
        })
