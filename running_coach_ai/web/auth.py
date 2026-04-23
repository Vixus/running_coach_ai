"""Authentication routes and decorators for the web dashboard."""

import functools
import logging

from flask import Blueprint, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash

from running_coach_ai.database.models import Athlete
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
        athlete = db.query(Athlete).filter(Athlete.web_username == username).first()
        if not athlete or not athlete.web_password_hash:
            return jsonify({"error": "Invalid credentials"}), 401
        if not check_password_hash(athlete.web_password_hash, password):
            return jsonify({"error": "Invalid credentials"}), 401
        session["athlete_id"] = athlete.id
        session["is_admin"] = athlete.is_admin
    return jsonify({"ok": True})


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.route("/")
def index():
    if "athlete_id" in session:
        return redirect("/app")
    return redirect("/login")


@bp.route("/app")
def app_page():
    if "athlete_id" not in session:
        return redirect("/login")
    import os
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    return send_from_directory(static_dir, "app.html")


@bp.route("/test")
def test():
    return jsonify({"ok": True})
