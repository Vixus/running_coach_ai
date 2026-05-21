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


def upsert_morning_checkin(
    db: Session,
    athlete: Athlete,
    *,
    body: str,
    morning_snapshot_date,  # date | None
    today_local,            # date
    title: str = "Morning check-in",
    action_path: str = "/#morning",
) -> Notification:
    """Insert or update today's morning_checkin Notification.

    "Today" is athlete-local. If a `morning_checkin` row already exists with
    created_at on `today_local`, update its body / morning_snapshot_date /
    updated_at fields and return it. Otherwise insert a new row.

    The two-row-per-day case (yesterday's row + today's upsert) is guarded
    against by filtering created_at to today_local's bounds.

    Caller is responsible for db.commit().
    """
    from datetime import datetime, time, timedelta, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(athlete.timezone or "America/New_York")
    day_start_local = datetime.combine(today_local, time.min, tzinfo=tz)
    day_start_utc = day_start_local.astimezone(timezone.utc).replace(tzinfo=None)
    day_end_utc = (day_start_local + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)

    existing = (
        db.query(Notification)
        .filter(
            Notification.athlete_id == athlete.id,
            Notification.kind == "morning_checkin",
            Notification.created_at >= day_start_utc,
            Notification.created_at < day_end_utc,
        )
        .order_by(Notification.created_at.desc())
        .first()
    )

    if existing is not None:
        existing.body = body
        existing.morning_snapshot_date = morning_snapshot_date
        existing.read_at = None  # silent overwrite — surface as unread again
        db.flush()
        logger.info(
            "upsert_morning_checkin athlete=%d UPDATED notif id=%d snap=%s",
            athlete.id, existing.id, morning_snapshot_date,
        )
        return existing

    n = Notification(
        athlete_id=athlete.id,
        kind="morning_checkin",
        title=title,
        body=body,
        action_path=action_path,
        morning_snapshot_date=morning_snapshot_date,
    )
    db.add(n)
    db.flush()
    logger.info(
        "upsert_morning_checkin athlete=%d INSERTED notif id=%d snap=%s",
        athlete.id, n.id, morning_snapshot_date,
    )
    return n
