"""Entry point: starts Slack bot + APScheduler."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from running_coach_ai.config import configure_logging, settings

configure_logging()
logger = logging.getLogger(__name__)

# Ensure all DB tables exist. create_all() is idempotent — skips tables that
# already exist, creates any that are missing. No migration files needed.
try:
    from running_coach_ai.database.models import Base
    from running_coach_ai.database.session import engine
    Base.metadata.create_all(engine)
    logger.info("Database schema ready")
except Exception as _db_err:
    logger.critical("Database initialisation failed — cannot start: %s", _db_err)
    raise

# Slack Bolt app — no signing_secret in socket mode
app = App(token=settings.SLACK_BOT_TOKEN)

# Import and register event handlers
from running_coach_ai.slack.bot import register_handlers  # noqa: E402
register_handlers(app)

# Socket mode handler
handler = SocketModeHandler(app, settings.SLACK_APP_TOKEN)

# Scheduler
scheduler = BlockingScheduler()


def main() -> None:
    logger.info("Starting AI Running Coach...")

    # Non-blocking: opens WebSocket in daemon thread
    handler.connect()
    logger.info("Slack SocketMode connected")

    # Register all scheduled jobs (morning check-in, activity poll, weekly review)
    from running_coach_ai.scheduler.jobs import register_jobs
    register_jobs(scheduler, app)
    logger.info("Scheduler jobs registered")

    # Blocks main thread — keeps process alive; handles KeyboardInterrupt gracefully
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
        scheduler.shutdown(wait=False)
        handler.close()


if __name__ == "__main__":
    main()
