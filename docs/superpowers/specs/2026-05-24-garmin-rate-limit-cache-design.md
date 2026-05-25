# Garmin OAuth rate-limit cache + NAS-worker retirement

**Branch:** `009-garmin-rate-limit-cache`
**Status:** design approved 2026-05-24

## Problem

Garmin's `connectapi.garmin.com/oauth-service/oauth/exchange/user/2.0` endpoint rate-limits Railway's IP block aggressively. Every activity-poll cycle (every 30 min) fires up to **8 OAuth-exchange requests per athlete**:

- 2 token sources (DB → filesystem fallback)
- × 4 retry attempts each (3s, 8s, 20s backoff)

Each request stamps Garmin's per-IP counter and prolongs the throttle window. The system effectively keeps Garmin's rate limiter saturated, so Railway never gets a clean run.

The original mitigation — a residential-IP NAS worker (`docker-compose.refresher.yml` + `scripts/refresh_and_push_tokens.py`) that refreshed tokens from a home VPN exit and uploaded them to Railway via `POST /admin/upload-garmin-session/<id>` — turned out unworkable because the user's NAS is behind a VPN tunnel whose DNS configuration is broken at the system level. The NAS host itself cannot resolve external hostnames via any standard Linux path (verified by `nslookup`, `socket.gethostbyname`, `urllib.request.urlopen` all failing with `[Errno -3] Temporary failure in name resolution`). Only DSM's proprietary internal services can fetch updates — Python scripts cannot.

Three days of attempted Garmin sync on Railway, zero successful OAuth refreshes, zero tokens uploaded from the NAS, every activity poll failing with the same 8-call 429 storm.

## Goal

Stop the 429 storm. Let Garmin's per-IP cooldown window drain. Recover automatically when the IP clears. Retire the NAS-worker scaffolding so future-us doesn't waste time on a dead-end architecture.

## Non-goals

- Not solving Garmin's 429s — that's a Garmin-side rate limit on shared cloud IPs and outside our control.
- Not adding a residential proxy (BrightData/IPRoyal etc) — separate decision, future PR if needed.
- Not changing the morning-staleness UX shipped in `008-morning-staleness` — that already handles user-facing staleness gracefully.
- Not adding a manual re-SSO admin endpoint — included as a noted follow-up if it turns out IP blocks become permanent.

## Design

### 1. Module-level rate-limit cache in `running_coach_ai/garmin/client.py`

Two endpoints get cooldown tracking:

- `oauth_exchange` — set when `_ensure_fresh_oauth2` raises 429
- `sso_login` — set when `_login_with_rate_limit_retry` raises 429

Cache shape:

```python
# Module-level state. In-memory only — resets on each Railway redeploy,
# which is fine: a fresh start gives Garmin's IP cooldown a clean window.
_rate_limit_until: dict[str, datetime] = {}
_rate_limit_lock = threading.Lock()
_COOLDOWN_SECONDS = int(os.environ.get("GARMIN_RATE_LIMIT_COOLDOWN_SECONDS", "900"))  # 15 min default
```

Helper:

```python
def _rate_limited(endpoint: str) -> tuple[bool, datetime | None]:
    """Return (is_limited, until) for a named endpoint."""
    with _rate_limit_lock:
        until = _rate_limit_until.get(endpoint)
        if until is None or datetime.utcnow() >= until:
            return False, None
        return True, until


def _mark_rate_limited(endpoint: str) -> None:
    """Record a 429 on the given endpoint; cooldown is set from env (default 15 min)."""
    until = datetime.utcnow() + timedelta(seconds=_COOLDOWN_SECONDS)
    with _rate_limit_lock:
        _rate_limit_until[endpoint] = until
    logger.warning(
        "Garmin %s endpoint rate-limited; suppressing further calls until %s UTC",
        endpoint, until.isoformat(timespec="seconds"),
    )


def _clear_rate_limit(endpoint: str) -> None:
    """Clear cooldown after a successful call (Garmin's cleared the IP block)."""
    with _rate_limit_lock:
        if _rate_limit_until.pop(endpoint, None) is not None:
            logger.info("Garmin %s endpoint recovered from rate limit", endpoint)
```

Integration into `_ensure_fresh_oauth2`:

```python
def _ensure_fresh_oauth2(garmin: Garmin, athlete_id: int, max_attempts: int = 4) -> None:
    from garth.auth_tokens import OAuth2Token as _OAuth2Token

    tok = getattr(garmin.garth, "oauth2_token", None)
    if isinstance(tok, _OAuth2Token) and not tok.expired:
        return

    # NEW: short-circuit if we're in a known 429 cooldown
    limited, until = _rate_limited("oauth_exchange")
    if limited:
        raise GarminRateLimited(
            f"oauth_exchange suppressed until {until.isoformat()} UTC "
            f"(cached 429; set GARMIN_RATE_LIMIT_COOLDOWN_SECONDS to tune)"
        )

    delays = [3, 8, 20]
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            garmin.garth.refresh_oauth2()
            _clear_rate_limit("oauth_exchange")  # NEW: success clears cooldown
            logger.info("Garmin OAuth2 refreshed for athlete %s (attempt %d/%d)", ...)
            return
        except Exception as e:
            last_exc = e
            err = str(e)
            is_429 = "429" in err or "too many requests" in err.lower()
            if not is_429 or attempt == max_attempts - 1:
                if is_429:
                    _mark_rate_limited("oauth_exchange")  # NEW: cache before raising
                break
            wait = delays[attempt] if attempt < len(delays) else delays[-1]
            ...
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc
```

`_login_with_rate_limit_retry` gets the same treatment for the `sso_login` endpoint key.

The new `GarminRateLimited` exception is a subclass of `Exception` so callers can opt-in to special handling later, but for now it's treated identically to a regular Exception by the activity-poll / morning-checkin try-blocks (which already log and continue).

### 2. Delete the NAS worker scaffolding

Files removed:

- `docker-compose.refresher.yml` — the compose definition
- `scripts/refresh_and_push_tokens.py` — the worker script
- `scripts/seed_garmin_tokens.py` — verify with grep first; if unused outside of the NAS bootstrap, delete

Code paths removed:

- `POST /admin/upload-garmin-session/<athlete_id>` route in `web/api/admin.py`
- Any `DB_UPLOAD_TOKEN` env-var lookup in `web/api/admin.py` or `config.py`
- `DB_UPLOAD_TOKEN` entry in `.env.example`

Comments / mentions:

- Any reference in `CLAUDE.md` to the NAS worker path
- Comments in `garmin/client.py` that describe the "DB tokens uploaded by NAS worker" expectation (those tokens still get persisted, but they originate from Railway's own lazy refresh now, not an external pusher)

The DB column `Athlete.garmin_oauth_tokens` stays — it's still used as a persistent cache surviving Railway redeploys, just not populated by an external uploader anymore.

The Railway-side `garmin_token_refresh` scheduler job stays gated behind `DISABLE_GARMIN_TOKEN_REFRESH` (default disabled). Token refresh stays purely lazy — only fires when activity-poll or morning-checkin actually needs fresh data — so the cache's effect is maximized.

### 3. Tests

Unit tests in `tests/unit/test_garmin_rate_limit_cache.py` (new file):

- `test_rate_limited_returns_false_when_cache_empty`
- `test_mark_rate_limited_sets_cooldown_per_endpoint`
- `test_rate_limited_returns_true_within_cooldown_window`
- `test_rate_limited_returns_false_after_cooldown_expires`
- `test_clear_rate_limit_removes_cache_entry`
- `test_ensure_fresh_oauth2_short_circuits_when_cached_429` — patches `garmin.garth.refresh_oauth2` to track call count; first call raises 429 (4 retries × ~31s wall — but tests should mock `time.sleep`), subsequent call within cooldown short-circuits with zero network attempts.
- `test_ensure_fresh_oauth2_clears_cache_on_success` — first call cached 429, then we manually clear cache and patch refresh_oauth2 to succeed; verify `_rate_limited` returns False after.

Use `monkeypatch.setattr("running_coach_ai.garmin.client.time.sleep", lambda s: None)` to avoid 31s waits in tests.

### 4. Verification (manual, post-deploy)

1. After deploy, watch logs for `Garmin oauth_exchange endpoint rate-limited; suppressing further calls until X UTC`. Should appear once per ~15 min cooldown window, not every 30 sec.
2. Watch for the count of `WARNING: Garmin oauth/exchange rate-limited (429)` log lines per activity-poll cycle. Pre-fix: ~24 per cycle (3 athletes × 8 calls). Post-fix: ≤3 per cycle (only one attempt per athlete, fail-fast).
3. Eventually (could be hours), look for `Garmin oauth_exchange endpoint recovered from rate limit` — confirms the cache clears on a successful call.

If a week passes with zero recovery messages, Garmin's IP block on this Railway instance is effectively permanent and we'd add the residential-proxy follow-up.

### 5. Open questions

- **Cooldown default of 15 min** — guess based on Garmin's typical short-windowed rate limit. Could be too short (we'd keep triggering it) or too long (we'd miss recovery windows). Configurable via `GARMIN_RATE_LIMIT_COOLDOWN_SECONDS` so it can be tuned without redeploy.
- **No per-IP cache key** — Railway might rotate IPs across redeploys; in-memory cache resetting on redeploy handles this. If Railway pins a single IP per service (likely), the cache lives as long as the deployment.
- **Activity poll behaviour during cooldown** — currently logs at ERROR level on each 429 (`activity_poll failed for athlete X: 429 ...`). With the cache, post-cooldown the log will be `GarminRateLimited: oauth_exchange suppressed until ...` which is more informative. Worth a follow-up to lower the log level if these become noisy.

## Files touched

| File | Change |
|---|---|
| `running_coach_ai/garmin/client.py` | Add `_rate_limited`/`_mark_rate_limited`/`_clear_rate_limit` helpers; new `GarminRateLimited` exception; integrate into `_ensure_fresh_oauth2` and `_login_with_rate_limit_retry`. |
| `tests/unit/test_garmin_rate_limit_cache.py` | NEW: 7 unit tests covering cache behavior and integration. |
| `docker-compose.refresher.yml` | DELETE. |
| `scripts/refresh_and_push_tokens.py` | DELETE. |
| `scripts/seed_garmin_tokens.py` | DELETE (after grep-confirming no other callers). |
| `running_coach_ai/web/api/admin.py` | Remove `upload-garmin-session` route + DB_UPLOAD_TOKEN handling. |
| `.env.example` | Remove `DB_UPLOAD_TOKEN`. |
| `CLAUDE.md` | Remove NAS-worker references; note that token refresh is lazy + rate-limit-cached. |

## Verification before merge

- `pytest tests/unit/test_garmin_rate_limit_cache.py -v` — all pass
- `ruff check running_coach_ai/garmin/client.py running_coach_ai/web/api/admin.py tests/unit/test_garmin_rate_limit_cache.py` — clean
- `grep -rn "refresh_and_push_tokens\|upload-garmin-session\|DB_UPLOAD_TOKEN\|docker-compose.refresher" .` returns no source-code matches (docs/specs may still reference them as history; that's fine)
