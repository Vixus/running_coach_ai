"""Delivery-agnostic notification sink.

Writes a row to the `notifications` table. The web app polls these via
`GET /api/notifications/unread-count`, so any caller that creates a
notification will cause a connected browser to refresh within ~30s.

Slack delivery is *not* handled here; existing `send_dm()` calls are kept
in place during the transition and will be removed in Phase 6.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from running_coach_ai.database.models import Athlete, Notification

logger = logging.getLogger(__name__)


def notify(
    db: Session,
    athlete: Athlete,
    *,
    kind: str,
    title: str,
    body: str,
    action_path: Optional[str] = None,
    related_id: Optional[int] = None,
) -> Notification:
    """Create a Notification row. Caller is responsible for db.commit()."""
    n = Notification(
        athlete_id=athlete.id,
        kind=kind,
        title=title,
        body=body,
        action_path=action_path,
        related_id=related_id,
    )
    db.add(n)
    db.flush()
    logger.info(
        "notify athlete=%d kind=%s title=%r action_path=%s",
        athlete.id, kind, title, action_path,
    )
    return n
