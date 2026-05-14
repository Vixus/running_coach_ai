"""Unit tests for scripts/refresh_and_push_tokens.py

The worker runs on a residential-IP host and refreshes Garmin OAuth tokens
on behalf of the Railway deployment. The tests mock both garth (so we
don't actually call Garmin) and requests (so we don't actually call
Railway) and verify:
  * athletes are discovered from the filesystem layout
  * refresh + upload happens for each one
  * one athlete failing doesn't block the others
  * whitelist filtering works
  * missing required env vars exits with code 2
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make the script importable as a module
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import refresh_and_push_tokens as worker  # noqa: E402


def _seed_token_dir(base: Path, athlete_id: int) -> Path:
    d = base / str(athlete_id)
    d.mkdir(parents=True)
    (d / "oauth1_token.json").write_text('{"token":"oauth1-stub"}')
    (d / "oauth2_token.json").write_text('{"token":"oauth2-stub"}')
    return d


# ─── discover_athletes ──────────────────────────────────────────────────────


def test_discover_finds_numeric_dirs_with_both_token_files(tmp_path):
    _seed_token_dir(tmp_path, 1)
    _seed_token_dir(tmp_path, 2)

    assert worker.discover_athletes(tmp_path, whitelist=None) == [1, 2]


def test_discover_skips_dirs_missing_either_token_file(tmp_path):
    _seed_token_dir(tmp_path, 1)
    (tmp_path / "2").mkdir()  # empty dir
    (tmp_path / "3").mkdir()
    (tmp_path / "3" / "oauth1_token.json").write_text("{}")  # only one file

    assert worker.discover_athletes(tmp_path, whitelist=None) == [1]


def test_discover_skips_non_numeric_dirs(tmp_path):
    _seed_token_dir(tmp_path, 1)
    bogus = tmp_path / "garmin_backup"
    bogus.mkdir()
    (bogus / "oauth1_token.json").write_text("{}")
    (bogus / "oauth2_token.json").write_text("{}")

    assert worker.discover_athletes(tmp_path, whitelist=None) == [1]


def test_discover_respects_whitelist(tmp_path):
    _seed_token_dir(tmp_path, 1)
    _seed_token_dir(tmp_path, 2)
    _seed_token_dir(tmp_path, 7)

    assert worker.discover_athletes(tmp_path, whitelist=[1, 7]) == [1, 7]


def test_discover_handles_missing_dir(tmp_path):
    assert worker.discover_athletes(tmp_path / "nope", whitelist=None) == []


# ─── refresh_athlete ────────────────────────────────────────────────────────


@patch("refresh_and_push_tokens.requests.post")
@patch("refresh_and_push_tokens.garth.Client")
def test_refresh_athlete_calls_garth_then_uploads_both_files(
    mock_garth_client, mock_post, tmp_path
):
    path = _seed_token_dir(tmp_path, 42)
    mock_post.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
    fake_client = MagicMock()
    mock_garth_client.return_value = fake_client

    worker.refresh_athlete(
        42, tmp_path,
        railway_url="https://example.com",
        upload_token="topsecret",
    )

    # Cascade in order: load → refresh → dump
    fake_client.load.assert_called_once_with(str(path))
    fake_client.refresh_oauth2.assert_called_once_with()
    fake_client.dump.assert_called_once_with(str(path))

    # Both files POSTed to the Railway upload endpoint with the shared secret
    assert mock_post.call_count == 2
    posted_urls = sorted(c.kwargs["url"] if "url" in c.kwargs else c.args[0]
                         for c in mock_post.call_args_list)
    assert posted_urls == [
        "https://example.com/admin/upload-garmin-session/42/oauth1_token.json",
        "https://example.com/admin/upload-garmin-session/42/oauth2_token.json",
    ]
    for call in mock_post.call_args_list:
        assert call.kwargs["headers"] == {"X-Upload-Token": "topsecret"}


@patch("refresh_and_push_tokens.requests.post")
@patch("refresh_and_push_tokens.garth.Client")
def test_refresh_athlete_strips_trailing_slash_from_url(
    mock_garth_client, mock_post, tmp_path
):
    _seed_token_dir(tmp_path, 1)
    mock_post.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
    mock_garth_client.return_value = MagicMock()

    worker.refresh_athlete(
        1, tmp_path,
        railway_url="https://example.com/",  # trailing slash
        upload_token="t",
    )

    for call in mock_post.call_args_list:
        url = call.kwargs.get("url") or call.args[0]
        # No double slash before /admin/
        assert "example.com/admin/" in url
        assert "example.com//admin/" not in url


@patch("refresh_and_push_tokens.requests.post")
@patch("refresh_and_push_tokens.garth.Client")
def test_refresh_athlete_raises_when_garmin_refresh_fails(
    mock_garth_client, mock_post, tmp_path
):
    _seed_token_dir(tmp_path, 1)
    fake = MagicMock()
    fake.refresh_oauth2.side_effect = RuntimeError("429 from Garmin")
    mock_garth_client.return_value = fake

    with pytest.raises(RuntimeError, match="429"):
        worker.refresh_athlete(1, tmp_path, "https://example.com", "t")

    # No upload should happen if refresh failed
    mock_post.assert_not_called()


@patch("refresh_and_push_tokens.requests.post")
@patch("refresh_and_push_tokens.garth.Client")
def test_refresh_athlete_raises_when_upload_fails(
    mock_garth_client, mock_post, tmp_path
):
    _seed_token_dir(tmp_path, 1)
    mock_garth_client.return_value = MagicMock()
    bad = MagicMock(status_code=403)
    bad.raise_for_status.side_effect = RuntimeError("403 Forbidden")
    mock_post.return_value = bad

    with pytest.raises(RuntimeError):
        worker.refresh_athlete(1, tmp_path, "https://example.com", "t")


# ─── run_once ───────────────────────────────────────────────────────────────


@patch("refresh_and_push_tokens.refresh_athlete")
def test_run_once_iterates_every_athlete(mock_refresh, tmp_path):
    _seed_token_dir(tmp_path, 1)
    _seed_token_dir(tmp_path, 2)

    failures = worker.run_once(tmp_path, "https://x", "t", whitelist=None)

    assert failures == 0
    assert mock_refresh.call_count == 2
    called_ids = sorted(c.args[0] for c in mock_refresh.call_args_list)
    assert called_ids == [1, 2]


@patch("refresh_and_push_tokens.refresh_athlete")
def test_run_once_continues_after_per_athlete_failure(mock_refresh, tmp_path):
    """Critical: one athlete failing (e.g. their oauth1 expired) must not
    skip the rest. Otherwise a single broken account silently breaks
    everyone else."""
    _seed_token_dir(tmp_path, 1)
    _seed_token_dir(tmp_path, 2)
    _seed_token_dir(tmp_path, 3)

    def fake_refresh(aid, *a, **kw):
        if aid == 2:
            raise RuntimeError("simulated failure")
    mock_refresh.side_effect = fake_refresh

    failures = worker.run_once(tmp_path, "https://x", "t", whitelist=None)

    assert failures == 1
    # All three were attempted despite #2 failing
    assert mock_refresh.call_count == 3


@patch("refresh_and_push_tokens.refresh_athlete")
def test_run_once_no_athletes_is_not_an_error(mock_refresh, tmp_path):
    failures = worker.run_once(tmp_path, "https://x", "t", whitelist=None)

    assert failures == 0
    mock_refresh.assert_not_called()


# ─── main() — env var validation ────────────────────────────────────────────


def test_main_exits_2_when_railway_url_missing(monkeypatch):
    monkeypatch.delenv("RAILWAY_URL", raising=False)
    monkeypatch.setenv("DB_UPLOAD_TOKEN", "t")

    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 2


def test_main_exits_2_when_upload_token_missing(monkeypatch):
    monkeypatch.setenv("RAILWAY_URL", "https://x")
    monkeypatch.delenv("DB_UPLOAD_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 2


@patch("refresh_and_push_tokens.run_once")
def test_main_run_once_mode_exits_with_failure_count(mock_run_once, monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_URL", "https://x")
    monkeypatch.setenv("DB_UPLOAD_TOKEN", "t")
    monkeypatch.setenv("GARMIN_SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("RUN_ONCE", "1")
    mock_run_once.return_value = 2  # 2 failures

    rc = worker.main()
    assert rc == 1  # nonzero so cron picks it up


@patch("refresh_and_push_tokens.run_once")
def test_main_run_once_mode_zero_failures_returns_zero(mock_run_once, monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_URL", "https://x")
    monkeypatch.setenv("DB_UPLOAD_TOKEN", "t")
    monkeypatch.setenv("GARMIN_SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("RUN_ONCE", "1")
    mock_run_once.return_value = 0

    rc = worker.main()
    assert rc == 0


@patch("refresh_and_push_tokens.run_once")
def test_main_parses_athlete_ids_whitelist(mock_run_once, monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_URL", "https://x")
    monkeypatch.setenv("DB_UPLOAD_TOKEN", "t")
    monkeypatch.setenv("GARMIN_SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("RUN_ONCE", "1")
    monkeypatch.setenv("ATHLETE_IDS", " 1 , 2 , 7 ")
    mock_run_once.return_value = 0

    worker.main()

    args = mock_run_once.call_args
    assert args.args[3] == [1, 2, 7]
