"""Refresh Garmin oauth2 tokens locally (residential IP) and POST them to Railway.

WHY THIS EXISTS
---------------
Garmin permanently rate-limits Railway's IP block on the OAuth refresh
endpoint (`oauth-service/oauth/exchange/user/2.0`). Actual data endpoints
still work from Railway's IP — the bottleneck is purely refresh. This
script runs from a residential IP (the user's dev box via Windows Task
Scheduler), mints fresh oauth2 tokens, and POSTs them to Railway via the
admin token-push endpoint.

USAGE
-----
Run as needed (manually) or via Task Scheduler every ~45 min:

    python scripts/push_garmin_tokens_to_railway.py

CONFIG (from .env in repo root)
-------------------------------
- ADMIN_PUSH_TOKEN: shared secret matching Railway's ADMIN_PUSH_TOKEN env var
- RAILWAY_URL: optional override (default https://running-coach-ai-production.up.railway.app)
- DB_PATH, ENCRYPTION_KEY, etc.: standard project env, used to refresh tokens locally

ATHLETES
--------
Discovered automatically from the local DB — every Athlete row with a
non-null garmin_email gets refreshed and pushed.
"""

from __future__ import annotations

import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Make the repo importable when run from anywhere
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# Load .env into the process so DB_PATH/ENCRYPTION_KEY/etc are visible
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
except ImportError:
    pass  # If dotenv isn't installed, assume the env is already configured

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("token-pusher")


def main() -> int:
    push_token = os.environ.get("ADMIN_PUSH_TOKEN")
    if not push_token:
        logger.error("ADMIN_PUSH_TOKEN not set in env / .env")
        return 1

    base = os.environ.get(
        "RAILWAY_URL",
        "https://running-coach-ai-production.up.railway.app",
    ).rstrip("/")

    from running_coach_ai.database.session import get_session
    from running_coach_ai.database.models import Athlete
    from running_coach_ai.garmin.client import get_garmin_client

    refreshed: dict[int, str] = {}
    with get_session() as db:
        athletes = db.query(Athlete).filter(Athlete.garmin_email.isnot(None)).all()
        for a in athletes:
            if not a.garmin_password_encrypted:
                logger.warning("Athlete %d (%s): no encrypted password, skipping", a.id, a.garmin_email)
                continue
            try:
                get_garmin_client(a.id, a.garmin_email, a.garmin_password_encrypted, db)
                logger.info("Athlete %d (%s): local refresh OK", a.id, a.garmin_email)
            except Exception as e:
                logger.error("Athlete %d (%s): local refresh FAILED — %s", a.id, a.garmin_email, e)
                continue
        db.commit()
        # Re-read tokens after the commit so we get the just-refreshed values
        for a in db.query(Athlete).filter(Athlete.garmin_email.isnot(None)).all():
            if a.garmin_oauth_tokens:
                refreshed[a.id] = a.garmin_oauth_tokens

    if not refreshed:
        logger.error("No athletes had refreshable tokens — nothing to push")
        return 2

    failures = 0
    for athlete_id, token_blob in refreshed.items():
        url = f"{base}/api/admin/athletes/{athlete_id}/garmin-tokens"
        req = urllib.request.Request(
            url,
            data=token_blob.encode("utf-8"),
            headers={
                "X-Admin-Token": push_token,
                "Content-Type": "text/plain",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", errors="replace")
                logger.info("Athlete %d push: HTTP %d %s", athlete_id, r.status, body.strip())
        except urllib.error.HTTPError as e:
            failures += 1
            logger.error(
                "Athlete %d push: HTTP %d %s",
                athlete_id, e.code, e.read().decode("utf-8", errors="replace").strip(),
            )
        except Exception as e:
            failures += 1
            logger.error("Athlete %d push: %s", athlete_id, e)

    logger.info(
        "Done at %s UTC — %d athlete(s) refreshed, %d push failure(s)",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        len(refreshed), failures,
    )
    return 0 if failures == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
