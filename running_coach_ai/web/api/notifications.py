"""In-app inbox endpoints.

The front-end polls `/api/notifications/unread-count` every 30s. When
`latest_at` advances, the active view refetches its primary endpoint,
which is how the page "updates itself when new data arrives."
"""

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session

from running_coach_ai.database.models import Notification
from running_coach_ai.database.session import get_session
from running_coach_ai.web.auth import login_required

bp = Blueprint("notifications", __name__)


def _serialise(n: Notification) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "action_path": n.action_path,
        "related_id": n.related_id,
        "created_at": n.created_at.isoformat() + "Z",
        "read_at": (n.read_at.isoformat() + "Z") if n.read_at else None,
    }


@bp.route("/api/notifications")
@login_required
def list_notifications():
    athlete_id = session["athlete_id"]
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except ValueError:
        limit = 50

    with get_session() as db:
        rows = (
            db.query(Notification)
            .filter(Notification.athlete_id == athlete_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
            .all()
        )
        return jsonify({"notifications": [_serialise(n) for n in rows]})


@bp.route("/api/notifications/unread-count")
@login_required
def unread_count():
    athlete_id = session["athlete_id"]
    with get_session() as db:
        q = db.query(Notification).filter(
            Notification.athlete_id == athlete_id,
            Notification.read_at.is_(None),
        )
        count = q.count()
        latest = (
            db.query(Notification.created_at)
            .filter(Notification.athlete_id == athlete_id)
            .order_by(Notification.created_at.desc())
            .first()
        )
        latest_at = (latest[0].isoformat() + "Z") if latest else None
        return jsonify({"count": count, "latest_at": latest_at})


@bp.route("/api/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_read(notification_id: int):
    athlete_id = session["athlete_id"]
    with get_session() as db:
        n = (
            db.query(Notification)
            .filter(
                Notification.id == notification_id,
                Notification.athlete_id == athlete_id,
            )
            .first()
        )
        if not n:
            return jsonify({"error": "Notification not found"}), 404
        if n.read_at is None:
            n.read_at = datetime.now(timezone.utc).replace(tzinfo=None)
        return jsonify({"notification": _serialise(n)})


@bp.route("/api/notifications/read-all", methods=["POST"])
@login_required
def mark_all_read():
    athlete_id = session["athlete_id"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with get_session() as db:
        updated = (
            db.query(Notification)
            .filter(
                Notification.athlete_id == athlete_id,
                Notification.read_at.is_(None),
            )
            .update({Notification.read_at: now}, synchronize_session=False)
        )
        return jsonify({"marked_read": updated})
