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
