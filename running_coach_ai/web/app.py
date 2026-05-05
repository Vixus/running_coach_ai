"""Flask app factory for the web analytics dashboard."""

import logging
import os
import subprocess
import sys

from flask import Flask, redirect, render_template, request, send_from_directory, session
from sqlalchemy import event, text

from running_coach_ai.config import settings
from running_coach_ai.database.session import engine, get_session
from running_coach_ai.database.models import Athlete

logger = logging.getLogger(__name__)


def _enable_wal_mode(dbapi_conn, connection_record):
    """Enable WAL mode on every new SQLite connection."""
    dbapi_conn.execute("PRAGMA journal_mode=WAL")


def _bootstrap_admin() -> None:
    """Create or update the admin athlete from BOOTSTRAP_ADMIN_* env vars.

    Set BOOTSTRAP_ADMIN_EMAIL + BOOTSTRAP_ADMIN_PASSWORD on first deploy to
    create the seed admin account. Re-running with the same email updates
    the password and re-sets is_admin=True.
    """
    email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL")
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    if not email or not password:
        return
    from werkzeug.security import generate_password_hash
    email = email.strip().lower()
    with get_session() as db:
        athlete = (
            db.query(Athlete)
            .filter((Athlete.email == email) | (Athlete.web_username == email))
            .first()
        )
        if not athlete:
            athlete = Athlete(email=email, web_username=email, allowed=True, is_admin=True)
            db.add(athlete)
        athlete.email = email
        if not athlete.web_username:
            athlete.web_username = email
        athlete.web_password_hash = generate_password_hash(password)
        athlete.is_admin = True
    logger.info("Bootstrap admin set: email=%s", email)


def _apply_migrations() -> None:
    """Apply any pending alembic migrations.

    Base.metadata.create_all only creates *missing tables* — it never adds
    columns to existing tables. Without this, model columns added via
    migrations (e.g. athletes.email) drift ahead of the schema and queries
    fail at startup with "no such column". Alembic is idempotent at head,
    so this is safe to call on every boot regardless of entrypoint
    (web.py, start.py, gunicorn).
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    rc = subprocess.call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repo_root,
    )
    if rc != 0:
        logger.error("alembic upgrade failed (exit %d) — startup will likely fail", rc)
    else:
        logger.info("alembic upgrade head applied successfully")


def create_app() -> Flask:
    _apply_migrations()

    from running_coach_ai.database.models import Base
    try:
        Base.metadata.create_all(engine)
    except Exception:
        db_path = settings.DB_PATH
        logger.warning("DB at %s is corrupt — deleting and recreating", db_path)
        engine.dispose()
        for suffix in ('', '-wal', '-shm', '.backup'):
            p = f"{db_path}{suffix}"
            if os.path.exists(p):
                os.remove(p)
        Base.metadata.create_all(engine)

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SECRET_KEY"] = settings.WEB_SECRET_KEY

    event.listen(engine, "connect", _enable_wal_mode)

    # Auth routes and page serving
    from running_coach_ai.web.auth import bp as auth_bp  # added by T011
    app.register_blueprint(auth_bp, url_prefix='/auth')

    @app.route('/')
    def index():
        return redirect('/login')

    @app.route('/login')
    def login_page():
        if "athlete_id" in session:
            return redirect('/magazine')
        return render_template('login.html')

    @app.route('/app')
    def app_page():
        if "athlete_id" not in session:
            return redirect('/login')
        with get_session() as db:
            athlete = db.get(Athlete, session["athlete_id"])
            if not athlete or not athlete.is_admin:
                return redirect('/magazine')
        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        return send_from_directory(static_dir, 'app.html')

    @app.route('/magazine')
    def magazine_page():
        if "athlete_id" not in session:
            return redirect('/login')
        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        return send_from_directory(static_dir, 'magazine.html')

    @app.route('/admin/upload-db', methods=['POST'])
    def upload_db():
        token = os.environ.get('DB_UPLOAD_TOKEN')
        if not token:
            return ("Upload disabled", 403)
        if request.headers.get('X-Upload-Token') != token:
            return ("Invalid token", 403)
        if 'file' not in request.files:
            return ("No file", 400)
        f = request.files['file']
        db_path = settings.DB_PATH
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        engine.dispose()
        if os.path.exists(db_path):
            backup = f"{db_path}.backup"
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(db_path, backup)
        for suffix in ('-wal', '-shm'):
            sidecar = f"{db_path}{suffix}"
            if os.path.exists(sidecar):
                os.remove(sidecar)
        import gzip as _gzip
        raw = f.read()
        if raw[:2] == b'\x1f\x8b':
            raw = _gzip.decompress(raw)
        with open(db_path, 'wb') as out:
            out.write(raw)
        logger.info("DB uploaded to %s (%s bytes)", db_path, os.path.getsize(db_path))
        return ("OK — restart the service to use the new DB", 200)

    @app.route('/admin/db-info')
    def db_info():
        token = os.environ.get('DB_UPLOAD_TOKEN')
        if not token or request.args.get('token') != token:
            return ("Forbidden", 403)
        with get_session() as db:
            athletes = db.query(Athlete).all()
            rows = [{"id": a.id, "email": a.email, "name": a.name,
                     "web_username": a.web_username, "onboarding_complete": a.onboarding_complete}
                    for a in athletes]
        import json
        return (json.dumps(rows, indent=2), 200, {"Content-Type": "application/json"})

    @app.route('/admin/upload-garmin-session/<athlete_id>/<filename>', methods=['POST'])
    def upload_garmin_session(athlete_id, filename):
        token = os.environ.get('DB_UPLOAD_TOKEN')
        if not token:
            return ("Upload disabled", 403)
        if request.headers.get('X-Upload-Token') != token:
            return ("Invalid token", 403)
        if filename not in ('oauth1_token.json', 'oauth2_token.json'):
            return ("Invalid filename", 400)
        dest_dir = os.path.join(settings.GARMIN_SESSION_DIR, athlete_id)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, filename)
        request.get_data()
        with open(dest, 'wb') as fh:
            fh.write(request.data)
        logger.info("Garmin session uploaded: %s", dest)
        return ("OK", 200)

    from running_coach_ai.web.api.dashboard import bp as dashboard_bp
    app.register_blueprint(dashboard_bp)

    from running_coach_ai.web.api.activities import bp as activities_bp
    app.register_blueprint(activities_bp)

    from running_coach_ai.web.api.plan import bp as plan_bp
    app.register_blueprint(plan_bp)

    from running_coach_ai.web.api.chat import bp as chat_bp
    app.register_blueprint(chat_bp)

    from running_coach_ai.web.api.review import bp as review_bp
    app.register_blueprint(review_bp)

    from running_coach_ai.web.api.admin import bp as admin_bp
    app.register_blueprint(admin_bp)

    from running_coach_ai.web.api.magazine import bp as magazine_bp
    app.register_blueprint(magazine_bp)

    from running_coach_ai.web.api.notifications import bp as notifications_bp
    app.register_blueprint(notifications_bp)

    from running_coach_ai.web.api.onboarding import bp as onboarding_bp
    app.register_blueprint(onboarding_bp)

    from running_coach_ai.web.events import attach_web_event_handler
    attach_web_event_handler(app)

    @app.after_request
    def _log_5xx(response):
        if response.status_code >= 500:
            from running_coach_ai.web.events import web_event
            web_event(
                severity="error",
                category="http",
                message=f"HTTP {response.status_code} on {response.status}",
                details={"status_code": response.status_code},
            )
        return response

    @app.errorhandler(Exception)
    def _log_unhandled_exception(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        app.logger.exception('UNHANDLED EXCEPTION: %s', e)
        return ("Internal Server Error", 500)

    _bootstrap_admin()

    return app
