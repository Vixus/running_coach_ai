"""Entry point for the scheduler process.

Slack is optional: if SLACK_BOT_TOKEN is unset (or empty), the Slack bot
and SocketMode connection are skipped and only the scheduler runs.
This is the soft-cutover path before Phase 6 deletes Slack entirely.
"""

import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler

from running_coach_ai.config import configure_logging, settings

configure_logging()
logger = logging.getLogger(__name__)

# Ensure all DB tables exist. create_all() is idempotent.
try:
    from running_coach_ai.database.models import Base
    from running_coach_ai.database.session import engine
    Base.metadata.create_all(engine)
    logger.info("Database schema ready")
except Exception as _db_err:
    logger.critical("Database initialisation failed — cannot start: %s", _db_err)
    raise


_SLACK_TOKEN = (settings.SLACK_BOT_TOKEN or "").strip()
_SLACK_APP_TOKEN = (settings.SLACK_APP_TOKEN or "").strip()
_SLACK_DISABLED = os.environ.get("DISABLE_SLACK", "").lower() in ("1", "true", "yes")
_SLACK_ENABLED = bool(_SLACK_TOKEN) and bool(_SLACK_APP_TOKEN) and not _SLACK_DISABLED

# Module-level globals — kept in scope so legacy code (e.g. slack/onboarding.py
# `import main as app_main`) can still resolve `app_main.scheduler` and
# `app_main.handler.app.client`. Both are None when Slack is disabled.
app = None
handler = None
scheduler = BlockingScheduler()


if _SLACK_ENABLED:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    from running_coach_ai.slack.bot import register_handlers

    app = App(token=_SLACK_TOKEN)
    register_handlers(app)
    handler = SocketModeHandler(app, _SLACK_APP_TOKEN)
    logger.info("Slack mode: ENABLED")
else:
    logger.info("Slack mode: DISABLED (web-only — set SLACK_BOT_TOKEN to re-enable)")


def main() -> None:
    logger.info("Starting AI Running Coach scheduler...")

    if _SLACK_ENABLED:
        handler.connect()
        logger.info("Slack SocketMode connected")

    from running_coach_ai.scheduler.jobs import register_jobs
    register_jobs(scheduler, app)
    logger.info("Scheduler jobs registered")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
        scheduler.shutdown(wait=False)
        if _SLACK_ENABLED:
            handler.close()


if __name__ == "__main__":
    main()
