# Garmin OAuth rate-limit cache + NAS retirement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the OAuth 429 storm by short-circuiting subsequent refresh attempts during a known rate-limit window, and retire the NAS-worker scaffolding that turned out unworkable due to VPN/DNS issues.

**Architecture:** Module-level in-memory cache in `garmin/client.py` keyed by endpoint (`oauth_exchange`, `sso_login`) tracks "rate-limited until <utc>". Three pure helpers (`_rate_limited`, `_mark_rate_limited`, `_clear_rate_limit`) wrap the dict with a `threading.Lock`. The two existing entry points (`_ensure_fresh_oauth2`, `_login_with_rate_limit_retry`) check the cache at entry (fail fast with new `GarminRateLimited` exception) and update it on 429/success. Cooldown defaults to 15 min, tunable via `GARMIN_RATE_LIMIT_COOLDOWN_SECONDS`. NAS scaffolding (compose file, refresh script, its tests, a stale comment) gets deleted.

**Tech Stack:** Python 3.11, threading, pytest, in-memory SQLite for any incidental fixtures.

**Reference spec:** `docs/superpowers/specs/2026-05-24-garmin-rate-limit-cache-design.md`

---

## File Structure

| Path | Responsibility | Action |
|---|---|---|
| `running_coach_ai/garmin/client.py` | Per-athlete Garmin client + OAuth auth | **Modify:** add cache infra (Task 1), integrate into `_ensure_fresh_oauth2` (Task 2), integrate into `_login_with_rate_limit_retry` (Task 3) |
| `tests/unit/test_garmin_rate_limit_cache.py` | New unit tests for cache + integration | **Create** (Task 1) |
| `docker-compose.refresher.yml` | NAS-worker compose file | **DELETE** (Task 4) |
| `scripts/refresh_and_push_tokens.py` | NAS-worker script | **DELETE** (Task 4) |
| `tests/unit/test_refresh_and_push_tokens.py` | NAS-worker tests | **DELETE** (Task 4) |
| `running_coach_ai/scheduler/jobs.py` | Scheduler registration | **Modify:** update stale comment at line 114 (Task 5) |

**Files explicitly NOT touched** (verified during planning):

- `running_coach_ai/web/api/admin.py` — no `upload-garmin-session` route exists despite the worker pushing to one
- `running_coach_ai/config.py`, `.env.example` — no `DB_UPLOAD_TOKEN` exists
- `scripts/seed_garmin_tokens.py` — independent utility for one-time DB seeding
- `docker-compose.yml` — main-app deployment compose (separate from the deleted refresher)
- `CLAUDE.md` — its NAS reference is for the main docker-compose, not the refresher

---

## Task 1: Add rate-limit cache infrastructure (TDD)

**Files:**
- Modify: `running_coach_ai/garmin/client.py`
- Create: `tests/unit/test_garmin_rate_limit_cache.py`

### Goal

A small thread-safe module-level cache with three pure helpers (`_rate_limited`, `_mark_rate_limited`, `_clear_rate_limit`) and one new exception (`GarminRateLimited`). No integration with `_ensure_fresh_oauth2` yet — that's Task 2.

- [ ] **Step 1.1: Write the failing tests**

Create `tests/unit/test_garmin_rate_limit_cache.py` with this exact content:

```python
"""Unit tests for the Garmin OAuth rate-limit cache."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear the module-level cache between tests."""
    from running_coach_ai.garmin import client
    client._rate_limit_until.clear()
    yield
    client._rate_limit_until.clear()


def test_rate_limited_returns_false_when_cache_empty():
    from running_coach_ai.garmin.client import _rate_limited
    is_limited, until = _rate_limited("oauth_exchange")
    assert is_limited is False
    assert until is None


def test_mark_rate_limited_sets_cooldown_per_endpoint():
    from running_coach_ai.garmin.client import _mark_rate_limited, _rate_limited

    _mark_rate_limited("oauth_exchange")

    is_limited_oauth, until_oauth = _rate_limited("oauth_exchange")
    is_limited_sso, until_sso = _rate_limited("sso_login")

    assert is_limited_oauth is True
    assert until_oauth is not None
    # other endpoint unaffected
    assert is_limited_sso is False
    assert until_sso is None


def test_rate_limited_returns_true_within_cooldown_window():
    from running_coach_ai.garmin.client import _mark_rate_limited, _rate_limited

    _mark_rate_limited("oauth_exchange")
    is_limited, until = _rate_limited("oauth_exchange")
    assert is_limited is True
    # cooldown is at least a few seconds into the future
    assert until > datetime.utcnow() + timedelta(seconds=60)


def test_rate_limited_returns_false_after_cooldown_expires():
    from running_coach_ai.garmin import client
    from running_coach_ai.garmin.client import _rate_limited

    # Bypass _mark_rate_limited so we can stash a past-expiry timestamp
    client._rate_limit_until["oauth_exchange"] = datetime.utcnow() - timedelta(seconds=1)

    is_limited, until = _rate_limited("oauth_exchange")
    assert is_limited is False
    assert until is None


def test_clear_rate_limit_removes_cache_entry():
    from running_coach_ai.garmin.client import (
        _clear_rate_limit,
        _mark_rate_limited,
        _rate_limited,
    )

    _mark_rate_limited("oauth_exchange")
    assert _rate_limited("oauth_exchange")[0] is True

    _clear_rate_limit("oauth_exchange")
    assert _rate_limited("oauth_exchange") == (False, None)


def test_clear_rate_limit_is_noop_when_no_entry():
    from running_coach_ai.garmin.client import _clear_rate_limit
    # Must not raise
    _clear_rate_limit("oauth_exchange")


def test_garmin_rate_limited_is_an_exception():
    from running_coach_ai.garmin.client import GarminRateLimited
    assert issubclass(GarminRateLimited, Exception)
    # Round-trip the message
    e = GarminRateLimited("suppressed")
    assert "suppressed" in str(e)


def test_cooldown_seconds_respects_env_var(monkeypatch):
    """The cooldown duration is read from GARMIN_RATE_LIMIT_COOLDOWN_SECONDS."""
    monkeypatch.setenv("GARMIN_RATE_LIMIT_COOLDOWN_SECONDS", "60")
    # Force re-import so the module reads the env var fresh
    import importlib
    from running_coach_ai.garmin import client as _client
    importlib.reload(_client)

    _client._rate_limit_until.clear()
    _client._mark_rate_limited("oauth_exchange")
    _, until = _client._rate_limited("oauth_exchange")
    # 60s cooldown ± a few seconds for timing jitter
    delta = (until - datetime.utcnow()).total_seconds()
    assert 50 <= delta <= 65

    # Reload back to default so subsequent tests aren't affected
    monkeypatch.delenv("GARMIN_RATE_LIMIT_COOLDOWN_SECONDS")
    importlib.reload(_client)
```

- [ ] **Step 1.2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_garmin_rate_limit_cache.py -v`

Expected: all 8 fail with `AttributeError: module 'running_coach_ai.garmin.client' has no attribute '_rate_limit_until'` (and similar for the helpers).

- [ ] **Step 1.3: Add cache infrastructure to `garmin/client.py`**

Open `running_coach_ai/garmin/client.py`. Add `threading` and `datetime` imports near the existing imports (top of file). Then add this block **immediately after the existing top-level imports** (currently around line 14, after `logger = logging.getLogger(__name__)`). Use this exact code:

```python
# ---------------------------------------------------------------------------
# OAuth rate-limit cache
#
# Garmin's oauth/exchange endpoint and SSO login both rate-limit shared cloud
# IPs (Railway etc) with 429s. Each retry stamps the per-IP counter and
# prolongs the throttle window. This module-level cache lets us fail fast on
# subsequent calls within a known cooldown so the rate limiter has a chance
# to drain. In-memory only — resets on every Railway redeploy, which is fine
# because a fresh deploy gets a fresh start at Garmin's window.
# ---------------------------------------------------------------------------
import threading
from datetime import datetime, timedelta

_rate_limit_until: dict[str, datetime] = {}
_rate_limit_lock = threading.Lock()
_COOLDOWN_SECONDS = int(os.environ.get("GARMIN_RATE_LIMIT_COOLDOWN_SECONDS", "900"))


class GarminRateLimited(Exception):
    """Raised when an OAuth endpoint is in a known-rate-limited cooldown."""


def _rate_limited(endpoint: str) -> tuple[bool, datetime | None]:
    """Return (is_limited, until_utc) for the named endpoint."""
    with _rate_limit_lock:
        until = _rate_limit_until.get(endpoint)
        if until is None or datetime.utcnow() >= until:
            # Clean up stale entries lazily so the dict doesn't grow.
            if until is not None:
                _rate_limit_until.pop(endpoint, None)
            return False, None
        return True, until


def _mark_rate_limited(endpoint: str) -> None:
    """Record a 429 on the named endpoint; cooldown set from env (default 15 min)."""
    until = datetime.utcnow() + timedelta(seconds=_COOLDOWN_SECONDS)
    with _rate_limit_lock:
        _rate_limit_until[endpoint] = until
    logger.warning(
        "Garmin %s endpoint rate-limited; suppressing further calls until %s UTC",
        endpoint, until.isoformat(timespec="seconds"),
    )


def _clear_rate_limit(endpoint: str) -> None:
    """Clear cooldown after a successful call (Garmin cleared the IP block)."""
    with _rate_limit_lock:
        existed = _rate_limit_until.pop(endpoint, None) is not None
    if existed:
        logger.info("Garmin %s endpoint recovered from rate limit", endpoint)
```

Make sure `threading` and `datetime` are imported (Python doesn't auto-import them). The block above includes the imports inline, which is fine for a contained module section.

- [ ] **Step 1.4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_garmin_rate_limit_cache.py -v`

Expected: 8 passed.

If `test_cooldown_seconds_respects_env_var` fails because the reload doesn't pick up the new env var, double-check that `_COOLDOWN_SECONDS = int(os.environ.get(...))` runs at module import time (not in a function body) — that's required for the reload to refresh it.

- [ ] **Step 1.5: Lint**

Run: `ruff check running_coach_ai/garmin/client.py tests/unit/test_garmin_rate_limit_cache.py`

Expected: clean. Pre-existing warnings in `client.py` from other code are acceptable; flag any new ones.

- [ ] **Step 1.6: Commit**

```bash
git add running_coach_ai/garmin/client.py tests/unit/test_garmin_rate_limit_cache.py
git commit -m "Add Garmin OAuth rate-limit cache infrastructure

Module-level dict + lock + three helpers + new GarminRateLimited
exception. No integration yet — Tasks 2 and 3 wire this into
_ensure_fresh_oauth2 and _login_with_rate_limit_retry."
```

---

## Task 2: Integrate cache into `_ensure_fresh_oauth2`

**Files:**
- Modify: `running_coach_ai/garmin/client.py`
- Modify: `tests/unit/test_garmin_rate_limit_cache.py`

### Goal

Short-circuit `_ensure_fresh_oauth2` when the `oauth_exchange` endpoint is in cooldown. Mark on 429 after retries exhausted. Clear on success.

- [ ] **Step 2.1: Write the failing integration tests**

Append these two tests to `tests/unit/test_garmin_rate_limit_cache.py`:

```python
def test_ensure_fresh_oauth2_short_circuits_when_cached_429(monkeypatch):
    """When oauth_exchange is in cooldown, _ensure_fresh_oauth2 raises
    GarminRateLimited immediately with zero retries / zero network calls."""
    from unittest.mock import MagicMock
    from running_coach_ai.garmin.client import (
        _ensure_fresh_oauth2,
        _mark_rate_limited,
        GarminRateLimited,
    )

    # Pre-set cooldown
    _mark_rate_limited("oauth_exchange")

    # Build a mock garmin client whose oauth2 token is expired
    garmin = MagicMock()
    garmin.garth.oauth2_token = MagicMock(expired=True)
    garmin.garth.refresh_oauth2 = MagicMock(side_effect=AssertionError(
        "refresh_oauth2 must NOT be called when cooldown is active"
    ))

    # Patch sleep so test is fast (defensive — short-circuit shouldn't sleep)
    monkeypatch.setattr("running_coach_ai.garmin.client.time.sleep", lambda s: None)

    with pytest.raises(GarminRateLimited) as exc_info:
        _ensure_fresh_oauth2(garmin, athlete_id=1)
    assert "oauth_exchange" in str(exc_info.value)
    # refresh_oauth2 was never invoked
    garmin.garth.refresh_oauth2.assert_not_called()


def test_ensure_fresh_oauth2_marks_cache_on_429_after_retries(monkeypatch):
    """A real 429 from refresh_oauth2 (after all retries exhausted) sets the
    oauth_exchange cooldown so subsequent calls short-circuit."""
    from unittest.mock import MagicMock
    from running_coach_ai.garmin.client import (
        _ensure_fresh_oauth2,
        _rate_limited,
    )

    garmin = MagicMock()
    garmin.garth.oauth2_token = MagicMock(expired=True)
    # Every retry raises 429
    garmin.garth.refresh_oauth2 = MagicMock(
        side_effect=Exception("429 Client Error: Too Many Requests for url: ...")
    )

    monkeypatch.setattr("running_coach_ai.garmin.client.time.sleep", lambda s: None)

    with pytest.raises(Exception) as exc_info:
        _ensure_fresh_oauth2(garmin, athlete_id=1)
    assert "429" in str(exc_info.value)

    # Cooldown is now active
    is_limited, until = _rate_limited("oauth_exchange")
    assert is_limited is True
    assert until is not None


def test_ensure_fresh_oauth2_clears_cache_on_success(monkeypatch):
    """A successful refresh_oauth2 clears any prior cooldown."""
    from unittest.mock import MagicMock
    from running_coach_ai.garmin.client import (
        _ensure_fresh_oauth2,
        _mark_rate_limited,
        _rate_limited,
    )
    from running_coach_ai.garmin import client as _client

    # Stash a cooldown in the past so _rate_limited returns False but the dict
    # entry still exists — verify _clear_rate_limit is called on success.
    _client._rate_limit_until["oauth_exchange"] = datetime.utcnow() - timedelta(seconds=1)

    garmin = MagicMock()
    garmin.garth.oauth2_token = MagicMock(expired=True)
    garmin.garth.refresh_oauth2 = MagicMock(return_value=None)

    monkeypatch.setattr("running_coach_ai.garmin.client.time.sleep", lambda s: None)

    _ensure_fresh_oauth2(garmin, athlete_id=1)

    # No cooldown entry remains
    assert "oauth_exchange" not in _client._rate_limit_until
```

- [ ] **Step 2.2: Run to verify failures**

Run: `pytest tests/unit/test_garmin_rate_limit_cache.py::test_ensure_fresh_oauth2_short_circuits_when_cached_429 tests/unit/test_garmin_rate_limit_cache.py::test_ensure_fresh_oauth2_marks_cache_on_429_after_retries tests/unit/test_garmin_rate_limit_cache.py::test_ensure_fresh_oauth2_clears_cache_on_success -v`

Expected: 3 failures. `test_ensure_fresh_oauth2_short_circuits_when_cached_429` fails its `refresh_oauth2 must NOT be called` assertion because the cache check doesn't exist yet. The other two will fail too because mark/clear aren't called.

- [ ] **Step 2.3: Wire the cache into `_ensure_fresh_oauth2`**

In `running_coach_ai/garmin/client.py`, find the existing `_ensure_fresh_oauth2` function (around line 111). Replace its body with this version (changes annotated with `# NEW:` comments):

```python
def _ensure_fresh_oauth2(garmin: Garmin, athlete_id: int, max_attempts: int = 4) -> None:
    """Refresh OAuth2 access token if expired, with retry on 429.

    Garmin's `oauth/exchange/user/2.0` endpoint rate-limits shared cloud IPs
    (Railway etc.). Many of those 429s are short-lived (per-minute windows),
    so we retry with exponential backoff before giving up. Total max wait
    is ~31s, keeping admin HTTP requests responsive.

    If the OAuth2 token is still valid, returns immediately without any
    network call. Raises on non-429 errors or after all retries exhausted.

    Rate-limit cache: if a prior call already received a 429 within the
    cooldown window (default 15 min), raise GarminRateLimited immediately
    without any network attempt. This prevents the retry storm from
    prolonging Garmin's per-IP throttle.
    """
    from garth.auth_tokens import OAuth2Token as _OAuth2Token

    tok = getattr(garmin.garth, "oauth2_token", None)
    if isinstance(tok, _OAuth2Token) and not tok.expired:
        return  # already fresh

    # NEW: short-circuit if we're in a known cooldown
    limited, until = _rate_limited("oauth_exchange")
    if limited:
        raise GarminRateLimited(
            f"oauth_exchange suppressed until {until.isoformat(timespec='seconds')} UTC "
            f"(cached 429; tune via GARMIN_RATE_LIMIT_COOLDOWN_SECONDS)"
        )

    delays = [3, 8, 20]
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            garmin.garth.refresh_oauth2()
            _clear_rate_limit("oauth_exchange")  # NEW: success clears prior cooldown
            logger.info(
                "Garmin OAuth2 refreshed for athlete %s (attempt %d/%d)",
                athlete_id, attempt + 1, max_attempts,
            )
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
            logger.warning(
                "Garmin oauth/exchange rate-limited (429) for athlete %s "
                "[attempt %d/%d] — retrying in %ds",
                athlete_id, attempt + 1, max_attempts, wait,
            )
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc
```

Note: the test for `test_ensure_fresh_oauth2_clears_cache_on_success` works with a *non-expired* OAuth2 token mock (`MagicMock(expired=True)` triggers the refresh path, but then refresh succeeds). Confirm by re-reading the test — `expired=True` makes the early-return at line 5 *not* fire, so we proceed through the refresh loop where `_clear_rate_limit` runs on success.

- [ ] **Step 2.4: Re-run the tests**

Run: `pytest tests/unit/test_garmin_rate_limit_cache.py -v`

Expected: all 11 tests pass (8 original + 3 integration).

- [ ] **Step 2.5: Lint**

Run: `ruff check running_coach_ai/garmin/client.py tests/unit/test_garmin_rate_limit_cache.py`

Expected: clean (or only pre-existing warnings).

- [ ] **Step 2.6: Commit**

```bash
git add running_coach_ai/garmin/client.py tests/unit/test_garmin_rate_limit_cache.py
git commit -m "Wire rate-limit cache into _ensure_fresh_oauth2

Short-circuit with GarminRateLimited when oauth_exchange is in
cached cooldown. Mark cache on 429 after retries exhausted. Clear
cache on success."
```

---

## Task 3: Integrate cache into `_login_with_rate_limit_retry`

**Files:**
- Modify: `running_coach_ai/garmin/client.py`
- Modify: `tests/unit/test_garmin_rate_limit_cache.py`

### Goal

Same pattern as Task 2 but for the SSO login path, using the `sso_login` endpoint key.

- [ ] **Step 3.1: Write the failing tests**

Append to `tests/unit/test_garmin_rate_limit_cache.py`:

```python
def test_login_short_circuits_when_cached_429(monkeypatch):
    from unittest.mock import MagicMock
    from running_coach_ai.garmin.client import (
        _login_with_rate_limit_retry,
        _mark_rate_limited,
        GarminRateLimited,
    )

    _mark_rate_limited("sso_login")

    garmin = MagicMock()
    garmin.login = MagicMock(side_effect=AssertionError(
        "garmin.login must NOT be called when sso_login is in cooldown"
    ))

    monkeypatch.setattr("running_coach_ai.garmin.client.time.sleep", lambda s: None)

    with pytest.raises(GarminRateLimited) as exc_info:
        _login_with_rate_limit_retry(garmin, athlete_id=1)
    assert "sso_login" in str(exc_info.value)
    garmin.login.assert_not_called()


def test_login_marks_cache_on_429(monkeypatch):
    from unittest.mock import MagicMock
    from running_coach_ai.garmin.client import (
        _login_with_rate_limit_retry,
        _rate_limited,
    )

    garmin = MagicMock()
    garmin.login = MagicMock(
        side_effect=Exception("429 Client Error: Too Many Requests")
    )

    monkeypatch.setattr("running_coach_ai.garmin.client.time.sleep", lambda s: None)

    with pytest.raises(Exception) as exc_info:
        _login_with_rate_limit_retry(garmin, athlete_id=1)
    assert "429" in str(exc_info.value)

    is_limited, _ = _rate_limited("sso_login")
    assert is_limited is True


def test_login_clears_cache_on_success(monkeypatch):
    from unittest.mock import MagicMock
    from running_coach_ai.garmin import client as _client
    from running_coach_ai.garmin.client import _login_with_rate_limit_retry

    _client._rate_limit_until["sso_login"] = datetime.utcnow() - timedelta(seconds=1)

    garmin = MagicMock()
    garmin.login = MagicMock(return_value=None)

    monkeypatch.setattr("running_coach_ai.garmin.client.time.sleep", lambda s: None)

    _login_with_rate_limit_retry(garmin, athlete_id=1)

    assert "sso_login" not in _client._rate_limit_until
```

- [ ] **Step 3.2: Run to verify failures**

Run: `pytest tests/unit/test_garmin_rate_limit_cache.py -v -k "login"`

Expected: 3 failures (mirror of Task 2 failures, on the sso_login endpoint).

- [ ] **Step 3.3: Wire the cache into `_login_with_rate_limit_retry`**

In `running_coach_ai/garmin/client.py`, find `_login_with_rate_limit_retry` (around line 155). Replace its body with:

```python
def _login_with_rate_limit_retry(garmin: Garmin, athlete_id: int, max_attempts: int = 2) -> None:
    """Call garmin.login() with a single short retry for transient failures.

    429 rate-limit responses from Garmin SSO are typically IP-based and last
    hours — retrying immediately doesn't help. We do one short retry (30s) for
    transient errors, then fail fast so the scheduler can retry naturally on
    the next 30-minute job tick.

    Rate-limit cache: if a prior call already received a 429 within the
    cooldown window (default 15 min), raise GarminRateLimited immediately
    without attempting login. SSO 429s typically last hours, so even our
    fail-fast retry adds to Garmin's per-IP counter; the cache prevents it.
    """
    # NEW: short-circuit if we're in a known cooldown
    limited, until = _rate_limited("sso_login")
    if limited:
        raise GarminRateLimited(
            f"sso_login suppressed until {until.isoformat(timespec='seconds')} UTC "
            f"(cached 429; tune via GARMIN_RATE_LIMIT_COOLDOWN_SECONDS)"
        )

    for attempt in range(max_attempts):
        try:
            garmin.login()
            _clear_rate_limit("sso_login")  # NEW: success clears prior cooldown
            return
        except Exception as e:
            is_rate_limited = "429" in str(e) or "too many requests" in str(e).lower()
            if is_rate_limited:
                _mark_rate_limited("sso_login")  # NEW: cache before raising
                logger.warning(
                    "Garmin SSO rate limited (429) for athlete %s — "
                    "Railway IP may be blocked. Skipping re-auth until next scheduler tick.",
                    athlete_id,
                )
                raise
            if attempt < max_attempts - 1:
                wait = 30
                logger.warning(
                    "Garmin login failed for athlete %s (attempt %d/%d): %s. Retrying in %ds...",
                    athlete_id, attempt + 1, max_attempts, e, wait,
                )
                time.sleep(wait)
            else:
                raise
```

- [ ] **Step 3.4: Re-run all tests**

Run: `pytest tests/unit/test_garmin_rate_limit_cache.py -v`

Expected: all 14 tests pass (8 cache + 3 oauth_exchange integration + 3 sso_login integration).

- [ ] **Step 3.5: Lint**

Run: `ruff check running_coach_ai/garmin/client.py`

Expected: clean (or only pre-existing warnings).

- [ ] **Step 3.6: Commit**

```bash
git add running_coach_ai/garmin/client.py tests/unit/test_garmin_rate_limit_cache.py
git commit -m "Wire rate-limit cache into _login_with_rate_limit_retry

Same fail-fast pattern as oauth_exchange, on the sso_login endpoint
key. SSO 429s typically last hours so the cache value is highest here."
```

---

## Task 4: Delete NAS worker scaffolding

**Files:**
- Delete: `docker-compose.refresher.yml`
- Delete: `scripts/refresh_and_push_tokens.py`
- Delete: `tests/unit/test_refresh_and_push_tokens.py`

### Goal

Remove the dead NAS-worker code now that we know it can't run on the user's NAS.

- [ ] **Step 4.1: Confirm no other code imports the worker**

Run: `grep -rn "refresh_and_push_tokens" --include="*.py" .`

Expected: matches only inside `tests/unit/test_refresh_and_push_tokens.py` and `scripts/refresh_and_push_tokens.py` themselves. If any other file imports it, stop and flag — that's an unexpected dependency.

Also run: `grep -rn "docker-compose.refresher\|garmin-refresher" --include="*.py" --include="*.md" --include="*.yml" .`

Expected: matches only in the three target files plus the design doc/plan markdown (which is historical record — leave it).

- [ ] **Step 4.2: Delete the three files**

```bash
git rm docker-compose.refresher.yml
git rm scripts/refresh_and_push_tokens.py
git rm tests/unit/test_refresh_and_push_tokens.py
```

- [ ] **Step 4.3: Verify the test suite still passes (no orphan imports)**

Run: `pytest tests/unit/ -q 2>&1 | tail -10`

Expected: zero collection errors. If any test fails with `ModuleNotFoundError` because it was importing the deleted worker, that test was dead code too — delete it.

- [ ] **Step 4.4: Verify grep is clean**

Run: `grep -rn "refresh_and_push_tokens\|docker-compose.refresher\|garmin-refresher" --include="*.py" --include="*.yml" .`

Expected: zero matches in source code (markdown design docs may still mention these as history; those are fine).

- [ ] **Step 4.5: Commit**

```bash
git commit -m "Delete NAS-worker scaffolding (compose, script, tests)

The residential-IP token refresher couldn't run on the user's NAS
because the host is behind a VPN whose DNS configuration is broken
at the system level (verified by socket.gethostbyname failing on the
host itself). Token refresh is now lazy and rate-limit-cached on
Railway directly."
```

---

## Task 5: Update stale scheduler/jobs.py comment

**Files:**
- Modify: `running_coach_ai/scheduler/jobs.py:114`

### Goal

The comment at line 114 references the deleted NAS worker. Replace with a comment that accurately describes the current lazy-refresh-only behavior.

- [ ] **Step 5.1: Find and read the existing comment**

Run: `grep -n -B 2 -A 4 "refresh_and_push_tokens" running_coach_ai/scheduler/jobs.py`

You should see something like (line numbers approximate):

```python
    # and scripts/refresh_and_push_tokens.py) handles token refresh externally
```

Read the surrounding 5-10 lines to understand the full sentence the comment is part of. The `DISABLE_GARMIN_TOKEN_REFRESH` env var gate is nearby — that part stays (Railway still uses it to suppress the proactive-refresh job).

- [ ] **Step 5.2: Replace the comment**

In `running_coach_ai/scheduler/jobs.py`, find the comment block around line 114 and rewrite it to remove the NAS-worker reference. The replacement should explain that token refresh is now lazy (fires when an actual API call needs fresh tokens, not on a schedule) and that the rate-limit cache in `garmin/client.py` prevents the 429 storm.

Suggested wording (adapt to fit the existing comment's grammar/style — use the existing surrounding text as a guide):

```python
    # Token refresh runs lazily inside garmin/client.py:get_garmin_client when
    # an actual API call needs fresh OAuth2 tokens. A module-level rate-limit
    # cache (added 2026-05-24) short-circuits subsequent calls during a known
    # 429 cooldown so the per-IP throttle has a chance to drain.
```

If the existing surrounding sentence reads "scheduler skips garmin_token_refresh because <NAS comment>", change the `<NAS comment>` clause to "<new comment>". Don't rewrite the whole block — just swap the NAS reference for the lazy-refresh-with-cache description.

- [ ] **Step 5.3: Verify no other NAS references in jobs.py**

Run: `grep -n "refresh_and_push\|NAS\|docker-compose.refresher\|external.*refresh" running_coach_ai/scheduler/jobs.py`

Expected: no matches. If something remains, update it.

- [ ] **Step 5.4: Lint**

Run: `ruff check running_coach_ai/scheduler/jobs.py`

Expected: clean.

- [ ] **Step 5.5: Commit**

```bash
git add running_coach_ai/scheduler/jobs.py
git commit -m "Update scheduler/jobs.py comment to reflect lazy refresh + cache

The NAS worker is gone; token refresh runs lazily on demand inside
garmin/client.py. Mention the new rate-limit cache so future readers
understand why DISABLE_GARMIN_TOKEN_REFRESH is the default."
```

---

## Task 6: Final verification + push

- [ ] **Step 6.1: Run the full affected test surface**

```bash
pytest tests/unit/test_garmin_rate_limit_cache.py tests/unit/test_garmin_reconciliation.py tests/unit/scheduler/ -v
```

Expected: all pass. The `test_garmin_reconciliation.py` and scheduler tests are included as a sanity check — they exercise the same `client.py` we changed.

- [ ] **Step 6.2: Lint the touched files end-to-end**

```bash
ruff check running_coach_ai/garmin/client.py running_coach_ai/scheduler/jobs.py tests/unit/test_garmin_rate_limit_cache.py
```

Expected: clean.

- [ ] **Step 6.3: Verify working tree**

```bash
git status
```

Expected: no untracked or uncommitted changes related to this work. The only modifications outside the plan should be local-only `.claude/settings.local.json` (always modified) and `docs/product/` (always untracked).

- [ ] **Step 6.4: Push the branch**

```bash
git push -u origin 009-garmin-rate-limit-cache
```

- [ ] **Step 6.5: Merge to deploy branch (if desired)**

The user's deploy branch is `007-today-card` (Railway tracks that). To deploy:

```bash
git checkout 007-today-card
git pull --ff-only
git merge --ff-only 009-garmin-rate-limit-cache
git push
```

If the merge is not fast-forwardable (deploy branch has new commits we don't have), pull/rebase first or coordinate with the user.

- [ ] **Step 6.6: Verify in Railway logs (manual, post-deploy)**

Watch the Railway logs after the deploy. Within the first 30 minutes, you should see:

1. A `Garmin oauth_exchange endpoint rate-limited; suppressing further calls until X UTC` log line on the first 429.
2. **No more than 1** `oauth/exchange rate-limited` warning per 15-min cooldown window (vs. ~24 per 30-min cycle pre-fix).
3. Subsequent activity polls during the cooldown should log `GarminRateLimited: oauth_exchange suppressed until ...` and exit fast (no 8-call storm).

If after several hours you see `Garmin oauth_exchange endpoint recovered from rate limit`, that's the cache clearing on a successful call — Garmin's IP cooldown has drained.

If after a week you've seen zero recovery messages, Garmin's block on this Railway IP is effectively permanent and we should plan the residential-proxy follow-up.
