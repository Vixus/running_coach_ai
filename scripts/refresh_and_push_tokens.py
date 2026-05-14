"""Garmin OAuth token refresh worker — runs on a residential-IP host (NAS).

WHY THIS EXISTS
---------------
Garmin's OAuth refresh endpoint (`connectapi.garmin.com/oauth-service/...`)
rate-limits cloud-host IP ranges (Railway, AWS, etc) with 429s. Residential
ISP ranges are not throttled. This worker therefore runs on a residential
machine (NAS, home server, dev box) and acts as a token refresh courier:

    [NAS] ──refresh──▶ Garmin       (succeeds, residential IP)
       │
       └──upload─────▶ Railway      (POST /admin/upload-garmin-session/...)

The Railway-side scheduler's daily refresh job stays in place as a backup.

WHAT IT DOES
------------
On each tick:
  1. Discover athletes by scanning GARMIN_SESSION_DIR/<id>/ for oauth*.json
  2. For each athlete:
       a. Load cached garth tokens (oauth1 long-lived refresh + oauth2 access)
       b. Call garth.refresh_oauth2() — uses oauth1 to mint a fresh oauth2.
          oauth1 typically lasts ~1 year; if it ever expires the operator
          will need to re-SSO once on this host.
       c. Dump both tokens back to disk
       d. POST both files to Railway's /admin/upload-garmin-session endpoint
  3. Sleep until the next interval

WHAT IT DOES NOT DO
-------------------
- It does NOT need Garmin passwords or the ENCRYPTION_KEY. The refresh flow
  is token-based: as long as the oauth1 refresh token is valid, oauth2 can
  be minted without re-SSO.
- It does NOT need a copy of the SQLite DB. It works off the filesystem
  token cache only.
- It does NOT need any inbound ports / DNS / TLS cert. Only outbound HTTPS.

REQUIRED ENVIRONMENT
--------------------
  RAILWAY_URL              e.g. https://running-coach-ai-production.up.railway.app
  DB_UPLOAD_TOKEN          shared secret matching Railway's env var
  GARMIN_SESSION_DIR       path to the per-athlete token cache (default ./data/garmin_sessions)

OPTIONAL
--------
  REFRESH_INTERVAL_SECONDS default 1800 (30 min). oauth2 TTL is ~1h, this
                           leaves headroom for one failed tick.
  ATHLETE_IDS              comma-separated whitelist, e.g. "1,2". Default:
                           refresh every athlete with a token directory.
  RUN_ONCE                 "1" → run a single refresh pass and exit. Useful
                           for cron-driven setups (Synology Task Scheduler).

INITIAL BOOTSTRAP
-----------------
The host running this script needs an initial set of garth tokens on disk.
Easiest way: run the main app locally once with valid Garmin credentials so
it writes filesystem tokens, then copy `data/garmin_sessions/<id>/` to the
NAS.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import garth
import requests

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
logger = logging.getLogger("garmin-token-refresher")

OAUTH_FILES = ("oauth1_token.json", "oauth2_token.json")


def _required_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(
            f"FATAL: {name} env var is required. "
            f"See module docstring in scripts/refresh_and_push_tokens.py\n"
        )
        sys.exit(2)
    return v


def discover_athletes(session_dir: Path, whitelist: list[int] | None) -> list[int]:
    """Find athletes with cached garth tokens on disk."""
    if not session_dir.is_dir():
        return []
    found: list[int] = []
    for sub in sorted(session_dir.iterdir()):
        if not sub.is_dir() or not sub.name.isdigit():
            continue
        if not all((sub / f).is_file() for f in OAUTH_FILES):
            continue
        aid = int(sub.name)
        if whitelist is None or aid in whitelist:
            found.append(aid)
    return found


def refresh_athlete(
    athlete_id: int,
    session_dir: Path,
    railway_url: str,
    upload_token: str,
    http_timeout: int = 30,
) -> None:
    """Refresh one athlete's oauth2 token, then upload both files to Railway.

    Raises on any failure so the caller can record an error and continue
    with the next athlete.
    """
    path = session_dir / str(athlete_id)

    client = garth.Client()
    client.load(str(path))
    client.refresh_oauth2()
    client.dump(str(path))
    logger.info("athlete=%d garth refresh_oauth2 OK", athlete_id)

    for filename in OAUTH_FILES:
        body = (path / filename).read_bytes()
        url = f"{railway_url.rstrip('/')}/admin/upload-garmin-session/{athlete_id}/{filename}"
        resp = requests.post(
            url,
            data=body,
            headers={"X-Upload-Token": upload_token},
            timeout=http_timeout,
        )
        resp.raise_for_status()
        logger.info(
            "athlete=%d uploaded %s to Railway (HTTP %d, %d bytes)",
            athlete_id, filename, resp.status_code, len(body),
        )


def run_once(
    session_dir: Path,
    railway_url: str,
    upload_token: str,
    whitelist: list[int] | None,
) -> int:
    """One refresh pass over all athletes. Returns number of failures."""
    athletes = discover_athletes(session_dir, whitelist)
    if not athletes:
        logger.warning(
            "no athletes found under %s — copy data/garmin_sessions/<id>/ "
            "from a host that has working Garmin auth.",
            session_dir,
        )
        return 0

    failures = 0
    for aid in athletes:
        try:
            refresh_athlete(aid, session_dir, railway_url, upload_token)
        except Exception as e:
            failures += 1
            logger.error("athlete=%d refresh FAILED: %s", aid, e)
    logger.info(
        "tick complete: %d athletes processed, %d failures",
        len(athletes), failures,
    )
    return failures


def main() -> int:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)

    railway_url = _required_env("RAILWAY_URL")
    upload_token = _required_env("DB_UPLOAD_TOKEN")
    session_dir = Path(
        os.environ.get("GARMIN_SESSION_DIR", "./data/garmin_sessions")
    )

    whitelist: list[int] | None = None
    raw_whitelist = os.environ.get("ATHLETE_IDS", "").strip()
    if raw_whitelist:
        whitelist = [int(x) for x in raw_whitelist.split(",") if x.strip()]

    interval = int(os.environ.get("REFRESH_INTERVAL_SECONDS", "1800"))
    run_once_only = os.environ.get("RUN_ONCE") == "1"

    logger.info(
        "starting garmin-token-refresher: session_dir=%s interval=%ds run_once=%s",
        session_dir, interval, run_once_only,
    )

    if run_once_only:
        failures = run_once(session_dir, railway_url, upload_token, whitelist)
        return 1 if failures else 0

    while True:
        try:
            run_once(session_dir, railway_url, upload_token, whitelist)
        except Exception as e:
            logger.exception("unexpected error in refresh loop: %s", e)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
