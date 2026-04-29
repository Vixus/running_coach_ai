"""Flask app factory for the web analytics dashboard."""

import logging
import os

from flask import Flask, redirect, render_template, request, send_from_directory, session
from sqlalchemy import event, text

from running_coach_ai.config import settings
from running_coach_ai.database.session import engine, get_session
from running_coach_ai.database.models import Athlete

logger = logging.getLogger(__name__)


def _enable_wal_mode(dbapi_conn, connection_record):
    """Enable WAL mode on every new SQLite connection."""
    dbapi_conn.execute("PRAGMA journal_mode=WAL")


def _seed_admin(app: Flask) -> None:
    """Idempotently set is_admin=True for the configured admin Slack user."""
    admin_id = settings.ADMIN_SLACK_USER_ID
    if not admin_id:
        return
    try:
        with get_session() as db:
            athlete = db.query(Athlete).filter(Athlete.slack_user_id == admin_id).first()
            if athlete and not athlete.is_admin:
                athlete.is_admin = True
                logger.info("Set is_admin=True for athlete %s", admin_id)
    except Exception:
        logger.debug("Admin seed skipped — no athlete row for %s yet", admin_id)


def _bootstrap_admin() -> None:
    """Create or update the admin athlete from BOOTSTRAP_WEB_* env vars."""
    username = os.environ.get("BOOTSTRAP_WEB_USERNAME")
    password = os.environ.get("BOOTSTRAP_WEB_PASSWORD")
    if not username or not password:
        return
    slack_id = settings.ADMIN_SLACK_USER_ID
    if not slack_id:
        logger.warning("BOOTSTRAP_WEB_USERNAME set but ADMIN_SLACK_USER_ID is missing — skipping bootstrap")
        return
    from werkzeug.security import generate_password_hash
    with get_session() as db:
        athlete = db.query(Athlete).filter(Athlete.slack_user_id == slack_id).first()
        if not athlete:
            athlete = Athlete(slack_user_id=slack_id, allowed=True, is_admin=True)
            db.add(athlete)
        athlete.web_username = username
        athlete.web_password_hash = generate_password_hash(password)
        athlete.is_admin = True
    logger.info("Bootstrap admin set: username=%s slack_id=%s", username, slack_id)


def create_app() -> Flask:
    from running_coach_ai.database.models import Base
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
            return redirect('/app')
        return render_template('login.html')

    @app.route('/app')
    def app_page():
        if "athlete_id" not in session:
            return redirect('/login')
        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        return send_from_directory(static_dir, 'app.html')

    @app.route('/magazine')
    def magazine_page():
        if "athlete_id" not in session:
            return redirect('/login')
        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        return send_from_directory(static_dir, 'magazine.html')

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

    _seed_admin(app)
    _bootstrap_admin()

    return app
