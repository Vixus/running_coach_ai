"""GET /api/admin/events endpoint."""

from flask import Blueprint, jsonify, request, session

from running_coach_ai.database.models import Athlete, WebEvent
from running_coach_ai.database.session import get_session
from running_coach_ai.web.auth import admin_required
from running_coach_ai.web.events import cleanup_old_events

bp = Blueprint("admin", __name__)

_MAX_LIMIT = 200


@bp.route("/api/admin/events")
@admin_required
def admin_events():
    category = request.args.get("category", "all")
    severity = request.args.get("severity", "all")
    try:
        limit = min(int(request.args.get("limit", 50)), _MAX_LIMIT)
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        limit = 50
        offset = 0

    with get_session() as db:
        cleanup_old_events(db)

        q = db.query(WebEvent)
        if category and category != "all":
            q = q.filter(WebEvent.category == category)
        if severity and severity != "all":
            q = q.filter(WebEvent.severity == severity)

        q = q.order_by(WebEvent.timestamp.desc())
        total = q.count()
        events = q.offset(offset).limit(limit).all()

        athlete_names: dict[int, str] = {}
        athlete_ids = {e.athlete_id for e in events if e.athlete_id is not None}
        for aid in athlete_ids:
            a = db.get(Athlete, aid)
            if a:
                athlete_names[aid] = a.name or str(aid)

        return jsonify({
            "events": [
                {
                    "id": e.id,
                    "timestamp": e.timestamp.isoformat() + "Z",
                    "severity": e.severity,
                    "category": e.category,
                    "message": e.message,
                    "athlete_name": athlete_names.get(e.athlete_id),
                    "details": e.details_json,
                }
                for e in events
            ],
            "total": total,
        })
