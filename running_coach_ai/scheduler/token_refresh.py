"""Daily Garmin OAuth2 token refresh scheduler job.

Runs once at 05:30 system time, just before the 06:00 activity_poll window
opens. For every athlete with stored Garmin credentials, calls
get_garmin_client which proactively refreshes the OAuth2 access token if
expired and writes the fresh state back to the DB.

Why bother: activity_poll already refreshes tokens as a side effect during
06:00-22:00, but the overnight gap (22:00 -> 06:00) is longer than the ~1h
OAuth2 TTL, so the token is always expired by morning. Without this job,
the first 06:00 poll has to refresh — and if Garmin's oauth/exchange
rate-limits Railway's IP at that exact moment, the entire day's first sync
fails. A dedicated 05:30 refresh primes the token while the load is low
and gives _ensure_fresh_oauth2's retry logic a chance to succeed before
real work depends on it.
"""

import logging

logger = logging.getLogger(__name__)


def _run_token_refresh() -> None:
    """Refresh Garmin OAuth2 tokens for every athlete with cached credentials."""
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.database.session import get_session
    from running_coach_ai.garmin.client import get_garmin_client

    logger.info("Garmin token refresh starting")
    refreshed = 0
    failed = 0
    skipped = 0

    with get_session() as db_session:
        athletes = (
            db_session.query(Athlete)
            .filter(
                Athlete.allowed == True,
                Athlete.onboarding_complete == True,
                Athlete.garmin_email.isnot(None),
                Athlete.garmin_password_encrypted.isnot(None),
            )
            .all()
        )

        for athlete in athletes:
            if not athlete.garmin_oauth_tokens:
                skipped += 1
                continue
            try:
                get_garmin_client(
                    athlete.id,
                    athlete.garmin_email,
                    athlete.garmin_password_encrypted,
                    db_session,
                )
                refreshed += 1
                logger.info("Token refresh OK for athlete %d", athlete.id)
            except Exception as e:
                failed += 1
                logger.warning("Token refresh failed for athlete %d: %s", athlete.id, e)

    logger.info(
        "Garmin token refresh complete: refreshed=%d failed=%d skipped=%d",
        refreshed, failed, skipped,
    )
