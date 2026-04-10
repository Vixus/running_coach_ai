"""Tests for Garmin credentials modal handlers (US1) and pending-state guard (US2).

Covers:
  T003: open_garmin_creds_modal action handler (4 cases)
  T004: garmin_creds_modal view handler (6 cases incl. SC-001)
  T013: pending-state chat guard in handle() (3 cases)
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

_BOT = "running_coach_ai.slack.bot"
_OB = "running_coach_ai.slack.onboarding"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PENDING_DATA = {
    "name": "Alice",
    "age": 30,
    "race_type": "marathon",
    "race_date": "2026-10-01",
    "target_time_seconds": 14400,
    "weekly_mileage_km": 40.0,
    "training_days": 5,
    "experience_level": "intermediate",
    "injuries": "none",
    "city": "london",
    "coach_key": "classic",
}


def _make_athlete(
    *,
    onboarding_complete=False,
    pending_data=None,
    pending_created_at=None,
    allowed=True,
    athlete_id=1,
):
    a = MagicMock()
    a.id = athlete_id
    a.slack_user_id = "U123"
    a.slack_dm_channel_id = "D123"
    a.onboarding_complete = onboarding_complete
    a.allowed = allowed
    a.pending_onboarding_data = pending_data
    a.pending_onboarding_data_created_at = pending_created_at
    return a


def _fresh_ts():
    """Created 1 hour ago — not expired."""
    return datetime.utcnow() - timedelta(hours=1)


def _expired_ts():
    """Created 25 hours ago — beyond the 24 h TTL."""
    return datetime.utcnow() - timedelta(hours=25)


def _db_with_athlete(athlete):
    """Return a mock DB session that yields the given athlete from any query."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = athlete
    return db


def _view_state(email="alice@garmin.com", password="s3cret"):
    """Minimal Slack view state payload."""
    return {
        "state": {
            "values": {
                "garmin_email_block": {
                    "garmin_email_input": {"value": email}
                },
                "garmin_password_block": {
                    "garmin_password_input": {"value": password}
                },
            }
        }
    }


# ---------------------------------------------------------------------------
# T003: Action handler — open_garmin_creds_modal
# ---------------------------------------------------------------------------

class TestOpenGarminCredsModalAction:
    """Tests for the @app.action('open_garmin_creds_modal') handler."""

    def _call(self, *, athlete, trigger_id="trigger123"):
        from running_coach_ai.slack.bot import _handle_open_garmin_creds_modal

        ack = MagicMock()
        client = MagicMock()
        body = {"user": {"id": "U123"}, "trigger_id": trigger_id}
        db = _db_with_athlete(athlete)

        with patch(f"{_BOT}.get_session") as mock_get_session:
            mock_get_session.return_value.__enter__.return_value = db
            _handle_open_garmin_creds_modal(ack=ack, body=body, client=client)

        return ack, client

    def test_ack_is_called(self):
        """T003-1: ack() must be called regardless of outcome."""
        athlete = _make_athlete(pending_data=PENDING_DATA, pending_created_at=_fresh_ts())
        ack, _ = self._call(athlete=athlete)
        ack.assert_called_once()

    def test_views_open_called_with_correct_payload(self):
        """T003-2: views_open uses the trigger_id and modal has both required block IDs."""
        athlete = _make_athlete(pending_data=PENDING_DATA, pending_created_at=_fresh_ts())
        _, client = self._call(athlete=athlete, trigger_id="trig_abc")

        client.views_open.assert_called_once()
        call_kwargs = client.views_open.call_args
        assert call_kwargs.kwargs.get("trigger_id") == "trig_abc" or (
            len(call_kwargs.args) > 0 and call_kwargs.args[0] == "trig_abc"
        )
        view_arg = call_kwargs.kwargs.get("view") or call_kwargs.args[1]
        view_str = json.dumps(view_arg)
        assert "garmin_email_block" in view_str
        assert "garmin_password_block" in view_str
        assert "garmin_creds_modal" in view_str

    def test_expired_pending_data_sends_dm_not_opens_modal(self):
        """T003-3: If pending data is TTL-expired, send DM and do NOT call views_open."""
        athlete = _make_athlete(
            pending_data=PENDING_DATA,
            pending_created_at=_expired_ts(),
        )
        _, client = self._call(athlete=athlete)

        client.views_open.assert_not_called()
        client.chat_postMessage.assert_called_once()
        msg_text = client.chat_postMessage.call_args.kwargs.get("text", "")
        assert msg_text  # some DM was sent

    def test_onboarding_complete_is_silent_noop(self):
        """T003-4: Already onboarded athlete → no DM, no modal."""
        athlete = _make_athlete(
            onboarding_complete=True,
            pending_data=PENDING_DATA,
            pending_created_at=_fresh_ts(),
        )
        _, client = self._call(athlete=athlete)

        client.views_open.assert_not_called()
        client.chat_postMessage.assert_not_called()


# ---------------------------------------------------------------------------
# T004: View handler — garmin_creds_modal submission
# ---------------------------------------------------------------------------

class TestGarminCredsModalView:
    """Tests for the @app.view('garmin_creds_modal') handler."""

    def _call(self, *, athlete, email="alice@garmin.com", password="s3cret"):
        from running_coach_ai.slack.bot import _handle_garmin_creds_modal_view

        ack = MagicMock()
        client = MagicMock()
        body = {"user": {"id": "U123"}}
        view = _view_state(email=email, password=password)
        db = _db_with_athlete(athlete)

        with (
            patch(f"{_BOT}.get_session") as mock_get_session,
            patch(f"{_OB}._complete_onboarding") as mock_complete,
            patch(f"{_OB}._send_garmin_credential_button") as mock_button,
            patch(f"{_BOT}.encrypt_password", return_value=b"encrypted"),
        ):
            mock_get_session.return_value.__enter__.return_value = db
            _handle_garmin_creds_modal_view(ack=ack, body=body, view=view, client=client)

        return ack, client, mock_complete, mock_button

    def test_ack_is_called(self):
        """T004-1: ack() is called immediately."""
        athlete = _make_athlete(pending_data=PENDING_DATA, pending_created_at=_fresh_ts())
        ack, _, _, _ = self._call(athlete=athlete)
        ack.assert_called_once()

    def test_already_onboarded_silently_discards(self):
        """T004-2: onboarding_complete=True → no DM, _complete_onboarding NOT called."""
        athlete = _make_athlete(
            onboarding_complete=True,
            pending_data=PENDING_DATA,
            pending_created_at=_fresh_ts(),
        )
        _, client, mock_complete, _ = self._call(athlete=athlete)

        mock_complete.assert_not_called()
        client.chat_postMessage.assert_not_called()

    def test_expired_pending_sends_expiry_dm(self):
        """T004-3: Null or expired pending data → expiry DM; _complete_onboarding NOT called."""
        for pending_ts in (None, _expired_ts()):
            athlete = _make_athlete(
                pending_data=PENDING_DATA if pending_ts else None,
                pending_created_at=pending_ts,
            )
            _, client, mock_complete, _ = self._call(athlete=athlete)

            mock_complete.assert_not_called()
            client.chat_postMessage.assert_called()

    def test_happy_path_calls_complete_onboarding_with_merged_data(self):
        """T004-4: Happy path: ack DM sent, _complete_onboarding called with garmin creds merged,
        pending columns cleared."""
        athlete = _make_athlete(pending_data=PENDING_DATA, pending_created_at=_fresh_ts())

        from running_coach_ai.slack.bot import _handle_garmin_creds_modal_view

        ack = MagicMock()
        client = MagicMock()
        body = {"user": {"id": "U123"}}
        view = _view_state(email="alice@garmin.com", password="s3cret")
        db = _db_with_athlete(athlete)

        with (
            patch(f"{_BOT}.get_session") as mock_get_session,
            patch(f"{_OB}._complete_onboarding") as mock_complete,
            patch(f"{_OB}._send_garmin_credential_button"),
            patch(f"{_BOT}.encrypt_password", return_value=b"encrypted"),
        ):
            mock_get_session.return_value.__enter__.return_value = db
            _handle_garmin_creds_modal_view(ack=ack, body=body, view=view, client=client)

        # Ack DM was sent
        dm_texts = [
            str(c.kwargs.get("text", "") or (c.args[1] if len(c.args) > 1 else ""))
            for c in client.chat_postMessage.call_args_list
        ]
        assert any("Setting up your account" in t or "Got it" in t for t in dm_texts)

        # _complete_onboarding called with merged data that includes garmin creds
        mock_complete.assert_called_once()
        merged_data = mock_complete.call_args.args[1]
        assert merged_data.get("garmin_email") == "alice@garmin.com"
        assert merged_data.get("garmin_password") == "s3cret"

        # pending columns cleared on athlete
        assert athlete.pending_onboarding_data is None
        assert athlete.pending_onboarding_data_created_at is None

    def test_garmin_auth_failure_sends_error_dm_and_resends_button(self):
        """T004-5: On GarminConnectAuthenticationError → error DM sent, button re-presented."""
        import garminconnect

        athlete = _make_athlete(pending_data=PENDING_DATA, pending_created_at=_fresh_ts())

        from running_coach_ai.slack.bot import _handle_garmin_creds_modal_view

        ack = MagicMock()
        client = MagicMock()
        body = {"user": {"id": "U123"}}
        view = _view_state()
        db = _db_with_athlete(athlete)

        with (
            patch(f"{_BOT}.get_session") as mock_get_session,
            patch(f"{_OB}._complete_onboarding", side_effect=garminconnect.GarminConnectAuthenticationError("bad creds")),
            patch(f"{_OB}._send_garmin_credential_button") as mock_button,
            patch(f"{_BOT}.encrypt_password", return_value=b"encrypted"),
        ):
            mock_get_session.return_value.__enter__.return_value = db
            _handle_garmin_creds_modal_view(ack=ack, body=body, view=view, client=client)

        # Error DM sent
        client.chat_postMessage.assert_called()
        dm_texts = [
            str(c.kwargs.get("text", "") or (c.args[1] if len(c.args) > 1 else ""))
            for c in client.chat_postMessage.call_args_list
        ]
        assert any(
            any(word in t.lower() for word in ("invalid", "incorrect", "credentials", "password", "try again"))
            for t in dm_texts
        )

        # Button re-sent
        mock_button.assert_called()

    def test_sc001_no_garmin_creds_in_conversation_messages(self):
        """T004-6: SC-001 — After handle() stores the onboarding_complete response,
        no ConversationMessage row contains 'garmin_email' or 'garmin_password' as a substring."""
        from running_coach_ai.slack.onboarding import handle

        from running_coach_ai.database.models import ConversationMessage

        # Build an athlete that has NOT completed onboarding
        athlete = _make_athlete()

        # Simulate Claude responding with an onboarding_complete tag that has NO garmin creds
        onboarding_json = json.dumps({
            "name": "Alice",
            "age": 30,
            "race_type": "marathon",
            "race_date": "2026-10-01",
            "target_time_seconds": 14400,
            "weekly_mileage_km": 40.0,
            "training_days": 5,
            "experience_level": "intermediate",
            "injuries": "none",
            "city": "london",
            "coach_key": "classic",
        })
        claude_response = (
            "Great, I've got everything I need! Let me get your plan ready.\n\n"
            f"<onboarding_complete>{onboarding_json}</onboarding_complete>"
        )

        stored_messages = []

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.flush.return_value = None

        def fake_add(obj):
            if isinstance(obj, ConversationMessage):
                stored_messages.append(obj)

        db.add.side_effect = fake_add

        say_fn = MagicMock()
        client = MagicMock()

        with (
            patch(f"{_OB}.call_claude", return_value=claude_response),
            patch(f"{_OB}._send_garmin_credential_button") as mock_btn,
        ):
            handle(athlete, "Yes, that looks right!", db, say_fn, client=client, channel="D123")

        # No stored messages should contain credential field names
        for msg in stored_messages:
            assert "garmin_email" not in msg.content, (
                f"ConversationMessage contains 'garmin_email': {msg.content!r}"
            )
            assert "garmin_password" not in msg.content, (
                f"ConversationMessage contains 'garmin_password': {msg.content!r}"
            )

        # Button was sent (confirming the new flow ran)
        mock_btn.assert_called_once()


# ---------------------------------------------------------------------------
# T013: US2 — pending-state chat guard in handle()
# ---------------------------------------------------------------------------

class TestPendingStateChatGuard:
    """Tests for the guard at the top of handle() that intercepts messages when
    the athlete has pending onboarding data awaiting modal submission."""

    def _call_handle(self, athlete, text="hello"):
        from running_coach_ai.slack.onboarding import handle

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.flush.return_value = None
        say_fn = MagicMock()
        client = MagicMock()

        with (
            patch(f"{_OB}.call_claude", return_value="How's training going?") as mock_claude,
            patch(f"{_OB}._send_garmin_credential_button") as mock_btn,
        ):
            handle(athlete, text, db, say_fn, client=client, channel="D123")

        return say_fn, client, mock_claude, mock_btn

    def test_pending_not_expired_sends_reminder_and_button(self):
        """T013-1: Non-expired pending data → reminder DM + button re-sent; Claude NOT called."""
        athlete = _make_athlete(
            pending_data=PENDING_DATA,
            pending_created_at=_fresh_ts(),
        )

        say_fn, client, mock_claude, mock_btn = self._call_handle(athlete)

        mock_claude.assert_not_called()
        mock_btn.assert_called_once()
        # some acknowledgement was sent
        assert say_fn.called or client.chat_postMessage.called

    def test_pending_expired_sends_expiry_dm_not_button(self):
        """T013-2: Expired pending data → expiry DM sent; button is NOT re-sent; Claude NOT called."""
        athlete = _make_athlete(
            pending_data=PENDING_DATA,
            pending_created_at=_expired_ts(),
        )

        say_fn, client, mock_claude, mock_btn = self._call_handle(athlete)

        mock_claude.assert_not_called()
        mock_btn.assert_not_called()
        # expiry message was sent
        assert say_fn.called or client.chat_postMessage.called

    def test_no_pending_data_falls_through_to_normal_flow(self):
        """T013-3: pending_onboarding_data=None → guard does not trigger; call_claude IS called."""
        athlete = _make_athlete(pending_data=None, pending_created_at=None)

        _, _, mock_claude, _ = self._call_handle(athlete)

        mock_claude.assert_called_once()
