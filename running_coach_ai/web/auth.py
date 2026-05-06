"""Authentication routes and decorators for the web dashboard."""

import functools
import logging
import secrets
from datetime import datetime

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from running_coach_ai.database.models import Athlete, InviteToken
from running_coach_ai.database.session import get_session

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "athlete_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "athlete_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        athlete_id = session["athlete_id"]
        with get_session() as db:
            athlete = db.get(Athlete, athlete_id)
            if not athlete or not athlete.is_admin:
                return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return decorated


@bp.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    with get_session() as db:
        # Look up by web_username (legacy) OR email (new web-native athletes).
        athlete = (
            db.query(Athlete)
            .filter((Athlete.web_username == username) | (Athlete.email == username))
            .first()
        )
        if not athlete or not athlete.web_password_hash:
            return jsonify({"error": "Invalid credentials"}), 401
        if not check_password_hash(athlete.web_password_hash, password):
            return jsonify({"error": "Invalid credentials"}), 401
        session["athlete_id"] = athlete.id
        session["is_admin"] = athlete.is_admin
    return jsonify({"ok": True})


@bp.route("/signup", methods=["POST"])
def signup():
    """Create a new athlete from a one-time invite token."""
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    token = (data.get("invite_token") or "").strip()

    if not email or not password or not token:
        return jsonify({"error": "Email, password, and invite token are required"}), 400
    if "@" not in email:
        return jsonify({"error": "Email looks invalid"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    with get_session() as db:
        invite = db.query(InviteToken).filter(InviteToken.token == token).first()
        if not invite:
            return jsonify({"error": "Invalid or unknown invite token"}), 400
        if invite.used_by_athlete_id is not None:
            return jsonify({"error": "This invite has already been used"}), 400
        if invite.expires_at is not None and invite.expires_at < datetime.utcnow():
            return jsonify({"error": "This invite has expired"}), 400

        existing = (
            db.query(Athlete)
            .filter((Athlete.email == email) | (Athlete.web_username == email))
            .first()
        )
        if existing:
            return jsonify({"error": "An account with this email already exists"}), 400

        athlete = Athlete(
            email=email,
            web_username=email,
            web_password_hash=generate_password_hash(password),
            allowed=True,
            is_admin=False,
            onboarding_complete=False,
        )
        db.add(athlete)
        db.flush()

        invite.used_by_athlete_id = athlete.id
        invite.used_at = datetime.utcnow()
        db.commit()

        session["athlete_id"] = athlete.id
        session["is_admin"] = False
        logger.info("New athlete signup id=%d email=%s via invite=%d", athlete.id, email, invite.id)

    return jsonify({"ok": True})


def issue_invite_token(*, created_by_athlete_id: int | None = None,
                       email_hint: str | None = None,
                       ttl_days: int = 30) -> InviteToken:
    """Create and return an InviteToken row. Caller must commit."""
    from datetime import timedelta
    with get_session() as db:
        token = secrets.token_urlsafe(24)
        invite = InviteToken(
            token=token,
            email_hint=email_hint,
            created_by_athlete_id=created_by_athlete_id,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(days=ttl_days),
        )
        db.add(invite)
        db.commit()
        db.refresh(invite)
        # Detach so the caller can read the fields after the session closes
        db.expunge(invite)
        return invite


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.route("/me", methods=["GET"])
def me():
    """Tiny session probe used by the front-end to decide initial routing
    (login screen vs onboarding chat vs full app)."""
    if "athlete_id" not in session:
        return jsonify({"authenticated": False}), 200
    with get_session() as db:
        athlete = db.get(Athlete, session["athlete_id"])
        if not athlete:
            session.clear()
            return jsonify({"authenticated": False}), 200
        return jsonify({
            "authenticated": True,
            "id": athlete.id,
            "email": athlete.email,
            "name": athlete.name,
            "onboarding_complete": bool(athlete.onboarding_complete),
            "pending_garmin": athlete.pending_onboarding_data is not None,
            "is_admin": bool(athlete.is_admin),
            "coach_key": athlete.coach_key or "classic",
        })


