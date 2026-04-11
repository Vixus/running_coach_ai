"""Application configuration loaded from environment variables."""

import logging
import logging.handlers
import os
from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ANTHROPIC_API_KEY: str = ""
    SLACK_BOT_TOKEN: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_APP_TOKEN: str = ""
    DB_PATH: str = "/data/coach.db"
    GARMIN_SESSION_DIR: str = "/data/garmin_sessions/"
    ENCRYPTION_KEY: str = ""
    ALLOWED_SLACK_USER_IDS: str = ""
    ADMIN_SLACK_USER_ID: str = ""
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = ""  # If set, logs are also written to this file path

    @property
    def allowed_user_ids(self) -> List[str]:
        if not self.ALLOWED_SLACK_USER_IDS:
            return []
        return [uid.strip() for uid in self.ALLOWED_SLACK_USER_IDS.split(",") if uid.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()


def configure_logging() -> None:
    """Set up structured logging: always to stdout, optionally to a rotating file."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt)

    log_file = settings.LOG_FILE
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True) if os.path.dirname(log_file) else None
        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        logging.getLogger().addHandler(handler)
