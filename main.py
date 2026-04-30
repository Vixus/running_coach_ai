"""Entry point for the scheduler process."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from running_coach_ai.config import configure_logging

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

scheduler = BlockingScheduler()


def main() -> None:
    logger.info("Starting AI Running Coach scheduler...")

    from running_coach_ai.scheduler.jobs import register_jobs
    register_jobs(scheduler)
    logger.info("Scheduler jobs registered")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
