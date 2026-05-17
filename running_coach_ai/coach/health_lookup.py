"""Shared 'today's HealthSnapshot, else most recent within 3 days' lookup.

Four call sites (coach/adapter, web/api/today, web/api/magazine, web/api/plan)
were independently implementing the same fallback pattern and had drifted
in subtle ways. This single helper is the source of truth.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import desc

from running_coach_ai.database.models import HealthSnapshot
from running_coach_ai.database.session import scoped_query

STALE_FALLBACK_DAYS = 3


def resolve_recent_snapshot(
    db_session, athlete_id: int, today_local: date
) -> tuple[HealthSnapshot | None, bool]:
    """Return ``(snapshot, is_stale)`` for the athlete's most usable snapshot.

    - Prefers ``today_local``'s row (``is_stale=False``).
    - Falls back to the newest row in ``[today_local - STALE_FALLBACK_DAYS, today_local)``
      (``is_stale=True``).
    - Returns ``(None, False)`` if neither exists.

    Today's row is excluded from the stale-fallback query — if it's missing,
    that's the actual "today missing" case; if it exists but is all-NULL, the
    caller can decide whether to fall back further (this helper doesn't, to
    avoid masking a partial today's row).
    """
    snap = (
        scoped_query(db_session, HealthSnapshot, athlete_id)
        .filter(HealthSnapshot.date == today_local)
        .first()
    )
    if snap is not None:
        return snap, False
    snap = (
        scoped_query(db_session, HealthSnapshot, athlete_id)
        .filter(
            HealthSnapshot.date >= today_local - timedelta(days=STALE_FALLBACK_DAYS),
            HealthSnapshot.date < today_local,
        )
        .order_by(desc(HealthSnapshot.date))
        .first()
    )
    return snap, (snap is not None)
