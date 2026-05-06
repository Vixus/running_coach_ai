"""Admin endpoints — events, athlete management, Garmin ops, invites."""

import logging

from flask import Blueprint, jsonify, request, session

from running_coach_ai.coach import admin_ops
from running_coach_ai.database.models import Athlete, InviteToken, WebEvent
from running_coach_ai.database.session import get_session
from running_coach_ai.garmin import admin as garmin_admin
from running_coach_ai.web.auth import admin_required, issue_invite_token
from running_coach_ai.web.events import cleanup_old_events

logger = logging.getLogger(__name__)

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


# ── Athlete management ──────────────────────────────────────────────────────

@bp.route("/api/admin/athletes")
@admin_required
def admin_list_athletes():
    with get_session() as db:
        return jsonify({"athletes": admin_ops.list_athletes(db)})


@bp.route("/api/admin/athletes/<int:athlete_id>/allow", methods=["POST"])
@admin_required
def admin_allow_athlete(athlete_id: int):
    with get_session() as db:
        result = admin_ops.set_allowed(athlete_id, True, db)
    if result is None:
        return jsonify({"error": "Athlete not found"}), 404
    return jsonify(result)


@bp.route("/api/admin/athletes/<int:athlete_id>/disable", methods=["POST"])
@admin_required
def admin_disable_athlete(athlete_id: int):
    with get_session() as db:
        result = admin_ops.set_allowed(athlete_id, False, db)
    if result is None:
        return jsonify({"error": "Athlete not found"}), 404
    return jsonify(result)


@bp.route("/api/admin/athletes/<int:athlete_id>/reset-onboarding", methods=["POST"])
@admin_required
def admin_reset_onboarding(athlete_id: int):
    with get_session() as db:
        result = admin_ops.reset_onboarding(athlete_id, db)
    if result is None:
        return jsonify({"error": "Athlete not found"}), 404
    return jsonify(result)


@bp.route("/api/admin/athletes/<int:athlete_id>/refresh-garmin-data", methods=["POST"])
@admin_required
def admin_refresh_garmin_data(athlete_id: int):
    body = request.get_json(silent=True) or {}
    try:
        days_back = min(int(body.get("days_back", 7)), 30)
    except (TypeError, ValueError):
        days_back = 7
    result = admin_ops.refresh_garmin_data(athlete_id, days_back=days_back)
    return jsonify(result), (200 if result.get("ok") else 400)


@bp.route("/api/admin/athletes/<int:athlete_id>/morning-checkin", methods=["POST"])
@admin_required
def admin_morning_checkin(athlete_id: int):
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))
    result = admin_ops.trigger_morning_checkin(athlete_id, force=force)
    return jsonify(result), (200 if result["ok"] else 400)


# ── Garmin admin ────────────────────────────────────────────────────────────

@bp.route("/api/admin/athletes/<int:athlete_id>/resync-garmin", methods=["POST"])
@admin_required
def admin_resync_garmin(athlete_id: int):
    with get_session() as db:
        a = db.get(Athlete, athlete_id)
        if not a:
            return jsonify({"error": "Athlete not found"}), 404
        result = garmin_admin.resync_garmin_for_athlete(a, db)
    return jsonify(result), (200 if result["ok"] else 400)


@bp.route("/api/admin/athletes/<int:athlete_id>/clean-garmin", methods=["POST"])
@admin_required
def admin_clean_garmin(athlete_id: int):
    with get_session() as db:
        a = db.get(Athlete, athlete_id)
        if not a:
            return jsonify({"error": "Athlete not found"}), 404
        result = garmin_admin.clean_garmin_for_athlete(a, db)
    return jsonify(result), (200 if result["ok"] else 400)


@bp.route("/api/admin/athletes/<int:athlete_id>/verify-garmin")
@admin_required
def admin_verify_garmin(athlete_id: int):
    with get_session() as db:
        a = db.get(Athlete, athlete_id)
        if not a:
            return jsonify({"error": "Athlete not found"}), 404
        result = garmin_admin.verify_garmin_for_athlete(a, db)
    return jsonify(result), (200 if result["ok"] else 400)


# ── Invites ─────────────────────────────────────────────────────────────────

@bp.route("/api/admin/invites", methods=["GET"])
@admin_required
def admin_list_invites():
    with get_session() as db:
        rows = (
            db.query(InviteToken)
            .order_by(InviteToken.created_at.desc())
            .limit(100)
            .all()
        )
        return jsonify({"invites": [
            {
                "id": i.id,
                "token": i.token,
                "email_hint": i.email_hint,
                "created_at": i.created_at.isoformat() + "Z" if i.created_at else None,
                "expires_at": i.expires_at.isoformat() + "Z" if i.expires_at else None,
                "used_by_athlete_id": i.used_by_athlete_id,
                "used_at": i.used_at.isoformat() + "Z" if i.used_at else None,
            }
            for i in rows
        ]})


@bp.route("/api/admin/invites", methods=["POST"])
@admin_required
def admin_create_invite():
    body = request.get_json(silent=True) or {}
    email_hint = (body.get("email_hint") or "").strip() or None
    try:
        ttl_days = int(body.get("ttl_days") or 30)
    except (TypeError, ValueError):
        ttl_days = 30
    invite = issue_invite_token(
        created_by_athlete_id=session.get("athlete_id"),
        email_hint=email_hint,
        ttl_days=ttl_days,
    )
    base = request.host_url.rstrip("/")
    return jsonify({
        "id": invite.id,
        "token": invite.token,
        "email_hint": invite.email_hint,
        "expires_at": invite.expires_at.isoformat() + "Z" if invite.expires_at else None,
        "signup_url": f"{base}/login?invite={invite.token}",
    })
