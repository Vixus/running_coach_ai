"""WebEvent dual-sink logging handler and helpers."""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

VALID_SEVERITIES = {"info", "warn", "error"}
VALID_CATEGORIES = {"auth", "garmin", "claude", "scheduler", "http", "today"}


class WebEventHandler(logging.Handler):
    """Writes log records to the WebEvent table via a fresh DB session."""

    LEVEL_TO_SEVERITY = {
        logging.DEBUG: "info",
        logging.INFO: "info",
        logging.WARNING: "warn",
        logging.ERROR: "error",
        logging.CRITICAL: "error",
    }

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from running_coach_ai.database.models import WebEvent
            from running_coach_ai.database.session import get_session

            severity = self.LEVEL_TO_SEVERITY.get(record.levelno, "info")
            category = getattr(record, "web_category", "http")

            with get_session() as db:
                event = WebEvent(
                    timestamp=datetime.utcnow(),
                    severity=severity,
                    category=category,
                    message=self.format(record),
                    athlete_id=getattr(record, "web_athlete_id", None),
                    details_json=getattr(record, "web_details", None),
                )
                db.add(event)
        except Exception:
            self.handleError(record)


def web_event(
    severity: str,
    category: str,
    message: str,
    athlete_id: Optional[int] = None,
    details: Optional[dict] = None,
) -> None:
    """Convenience helper: write a WebEvent row directly without going through logging."""
    from running_coach_ai.database.models import WebEvent
    from running_coach_ai.database.session import get_session

    if severity not in VALID_SEVERITIES:
        severity = "info"
    if category not in VALID_CATEGORIES:
        category = "http"

    try:
        with get_session() as db:
            event = WebEvent(
                timestamp=datetime.utcnow(),
                severity=severity,
                category=category,
                message=message,
                athlete_id=athlete_id,
                details_json=details,
            )
            db.add(event)
    except Exception:
        logger.debug("Failed to write web_event: %s", message)


def cleanup_old_events(db_session) -> int:
    """Delete WebEvent rows older than 30 days. Returns count deleted."""
    from running_coach_ai.database.models import WebEvent

    cutoff = datetime.utcnow() - timedelta(days=30)
    deleted = db_session.query(WebEvent).filter(WebEvent.timestamp < cutoff).delete()
    return deleted


def attach_web_event_handler(app) -> None:
    """Attach WebEventHandler to key logger namespaces."""
    handler = WebEventHandler()
    handler.setLevel(logging.WARNING)

    for name in ("running_coach_ai.web", "running_coach_ai.garmin", "running_coach_ai.coach.personas"):
        log = logging.getLogger(name)
        log.addHandler(handler)
