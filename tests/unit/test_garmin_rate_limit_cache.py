"""Unit tests for the Garmin OAuth rate-limit cache."""

from __future__ import annotations

from datetime import datetime, timedelta

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
    from running_coach_ai.garmin.client import _ensure_fresh_oauth2
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
