"""Slack Bolt event handlers and proactive DM helper."""

import logging
import os
import shutil

from slack_bolt import App
from slack_sdk import WebClient
from sqlalchemy.orm import Session

from running_coach_ai.config import settings
from running_coach_ai.database.models import Athlete
from running_coach_ai.database.session import get_session
from running_coach_ai.garmin.client import encrypt_password, is_garmin_auth_error

logger = logging.getLogger(__name__)

_ACTION_ID = "open_garmin_creds_modal"
_ACTION_ID_SKIP = "skip_garmin_creds"
_MODAL_CALLBACK_ID = "garmin_creds_modal"

_MODAL_VIEW = {
    "type": "modal",
    "callback_id": _MODAL_CALLBACK_ID,
    "title": {"type": "plain_text", "text": "Garmin Credentials"},
    "submit": {"type": "plain_text", "text": "Connect"},
    "close": {"type": "plain_text", "text": "Cancel"},
    "blocks": [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    ":lock: Your credentials are encrypted immediately on receipt "
                    "and never stored in plain text. This form is not visible in "
                    "your chat history."
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    ":warning: *The password field below does not hide what you type.* "
                    "Type your password somewhere private before clicking Connect."
                ),
            },
        },
        {
            "type": "input",
            "block_id": "garmin_email_block",
            "label": {"type": "plain_text", "text": "Garmin Connect Email"},
            "element": {
                "type": "plain_text_input",
                "action_id": "garmin_email_input",
                "placeholder": {"type": "plain_text", "text": "you@example.com"},
            },
        },
        {
            "type": "input",
            "block_id": "garmin_password_block",
            "label": {"type": "plain_text", "text": "Garmin Connect Password"},
            "element": {
                "type": "plain_text_input",
                "action_id": "garmin_password_input",
                "placeholder": {"type": "plain_text", "text": "Your Garmin password"},
            },
        },
    ],
}

_EXPIRY_MESSAGE = (
    "Your session has expired — it's been more than 24 hours since we set up your profile. "
    "Please message me to start fresh and I'll walk you through it again."
)


def _handle_open_garmin_creds_modal(ack, body, client) -> None:
    """Handle the 'Enter Garmin Credentials' button click — open the credentials modal."""
    ack()

    slack_user_id = body["user"]["id"]
    trigger_id = body["trigger_id"]

    with get_session() as db_session:
        from running_coach_ai.slack.admin import get_test_athlete_id
        test_athlete_id = get_test_athlete_id(slack_user_id)
        if test_athlete_id:
            athlete = db_session.query(Athlete).filter(Athlete.id == test_athlete_id).first()
        else:
            athlete = (
                db_session.query(Athlete)
                .filter(Athlete.slack_user_id == slack_user_id)
                .first()
            )

        if not athlete or not athlete.allowed:
            logger.info("Action %s: athlete %s not found or not allowed — ignoring", _ACTION_ID, slack_user_id)
            return

        if not athlete.onboarding_complete:
            from running_coach_ai.slack.onboarding import _is_pending_data_expired
            if _is_pending_data_expired(athlete):
                logger.info("Action %s: pending data expired for athlete %d", _ACTION_ID, athlete.id)
                client.chat_postMessage(channel=athlete.slack_dm_channel_id, text=_EXPIRY_MESSAGE)
                return

        logger.info("Action %s: opening credentials modal for athlete %d", _ACTION_ID, athlete.id)
        client.views_open(trigger_id=trigger_id, view=_MODAL_VIEW)


def _handle_skip_garmin_creds(ack, body, client) -> None:
    """Handle the 'Skip for now' button — complete onboarding without Garmin credentials."""
    ack()

    slack_user_id = body["user"]["id"]

    with get_session() as db_session:
        from running_coach_ai.slack.admin import get_test_athlete_id
        test_athlete_id = get_test_athlete_id(slack_user_id)
        if test_athlete_id:
            athlete = db_session.query(Athlete).filter(Athlete.id == test_athlete_id).first()
        else:
            athlete = (
                db_session.query(Athlete)
                .filter(Athlete.slack_user_id == slack_user_id)
                .first()
            )

        if not athlete or athlete.onboarding_complete:
            return

        from running_coach_ai.slack.onboarding import _is_pending_data_expired
        if athlete.pending_onboarding_data is None or _is_pending_data_expired(athlete):
            client.chat_postMessage(channel=athlete.slack_dm_channel_id, text=_EXPIRY_MESSAGE)
            return

        pending = dict(athlete.pending_onboarding_data)

        athlete.pending_onboarding_data = None
        athlete.pending_onboarding_data_created_at = None
        db_session.flush()

        def say_fn(text: str) -> None:
            client.chat_postMessage(channel=athlete.slack_dm_channel_id, text=text)

        say_fn(
            "No problem! Setting up your plan now — you can connect Garmin anytime to unlock "
            "watch sync, daily health check-ins, and post-run feedback."
        )

        from running_coach_ai.slack.onboarding import _complete_onboarding, _send_garmin_credential_button
        _complete_onboarding(athlete, pending, db_session, say_fn)
        logger.info("Skip action: onboarding completed without Garmin for athlete %d", athlete.id)

        # Persist the Connect button so the athlete can add Garmin whenever they're ready
        _send_garmin_credential_button(athlete.slack_dm_channel_id, client)


def _handle_garmin_creds_modal_view(ack, body, view, client) -> None:
    """Handle Garmin credentials modal submission — validate credentials, then complete onboarding or update stored creds."""
    ack()

    slack_user_id = body["user"]["id"]

    with get_session() as db_session:
        from running_coach_ai.slack.admin import get_test_athlete_id
        test_athlete_id = get_test_athlete_id(slack_user_id)
        if test_athlete_id:
            athlete = db_session.query(Athlete).filter(Athlete.id == test_athlete_id).first()
        else:
            athlete = (
                db_session.query(Athlete)
                .filter(Athlete.slack_user_id == slack_user_id)
                .first()
            )

        if not athlete or not athlete.allowed:
            logger.info("View %s: athlete %s not found or not allowed — discarding", _MODAL_CALLBACK_ID, slack_user_id)
            return

        values = view["state"]["values"]
        garmin_email = values["garmin_email_block"]["garmin_email_input"]["value"]
        garmin_password = values["garmin_password_block"]["garmin_password_input"]["value"]

        def say_fn(text: str) -> None:
            client.chat_postMessage(channel=athlete.slack_dm_channel_id, text=text)

        # Clear any cached session so validation always uses the submitted credentials,
        # not stale OAuth tokens that may mask a wrong password.
        token_dir = os.path.join(settings.GARMIN_SESSION_DIR, str(athlete.id))
        if os.path.isdir(token_dir):
            shutil.rmtree(token_dir)

        # Validate credentials before making any state changes.
        # Any exception here means we can't authenticate — block and ask the user to retry.
        try:
            from running_coach_ai.garmin.client import get_garmin_client
            get_garmin_client(athlete.id, garmin_email, encrypt_password(garmin_password))
        except Exception as e:
            logger.warning("View %s: Garmin credential validation failed for athlete %d: %s", _MODAL_CALLBACK_ID, athlete.id, e)
            if is_garmin_auth_error(e):
                say_fn(
                    "Those Garmin credentials didn't work — please check your email and password and try again. "
                    "(Make sure MFA is disabled on your Garmin Connect account.)"
                )
            else:
                say_fn(
                    "Couldn't connect to Garmin right now — please check your credentials and try again. "
                    "If the problem keeps happening, make sure MFA is disabled on your Garmin Connect account."
                )
            from running_coach_ai.slack.onboarding import _send_garmin_credential_button
            _send_garmin_credential_button(
                athlete.slack_dm_channel_id, client,
                include_skip=not athlete.onboarding_complete,
            )
            return

        if athlete.onboarding_complete:
            # Post-onboarding: credentials update only — no plan regeneration needed
            athlete.garmin_email = garmin_email
            athlete.garmin_password_encrypted = encrypt_password(garmin_password)
            db_session.commit()
            say_fn("Garmin credentials updated — everything should be working again.")
            logger.info("View %s: credentials updated for athlete %d", _MODAL_CALLBACK_ID, athlete.id)
            return

        # Onboarding path
        from running_coach_ai.slack.onboarding import _is_pending_data_expired
        if athlete.pending_onboarding_data is None or _is_pending_data_expired(athlete):
            logger.info("View %s: pending data null/expired for athlete %d — sending expiry DM", _MODAL_CALLBACK_ID, athlete.id)
            client.chat_postMessage(channel=athlete.slack_dm_channel_id, text=_EXPIRY_MESSAGE)
            return

        say_fn("Got it! Setting up your account, this may take a minute\u2026")

        merged_data = dict(athlete.pending_onboarding_data)
        merged_data["garmin_email"] = garmin_email
        merged_data["garmin_password"] = garmin_password

        athlete.garmin_email = garmin_email
        athlete.garmin_password_encrypted = encrypt_password(garmin_password)

        athlete.pending_onboarding_data = None
        athlete.pending_onboarding_data_created_at = None
        db_session.flush()

        from running_coach_ai.slack.onboarding import _complete_onboarding
        _complete_onboarding(athlete, merged_data, db_session, say_fn)
        logger.info("View %s: onboarding completed for athlete %d", _MODAL_CALLBACK_ID, athlete.id)




def _post_thinking(client, channel: str) -> str | None:
    """Post a visible 'thinking' placeholder and return its ts for later update/delete."""
    try:
        result = client.chat_postMessage(channel=channel, text="_…_")
        return result.get("ts")
    except Exception:
        return None


def _finish_thinking(client, channel: str, thinking_ts: str | None, reply: str) -> bool:
    """Replace the thinking placeholder with the real reply in-place.

    Returns True if the update succeeded so the caller knows not to say() again.
    Falls back silently — caller is responsible for posting reply on False.
    """
    if not thinking_ts:
        return False
    try:
        client.chat_update(channel=channel, ts=thinking_ts, text=reply)
        return True
    except Exception:
        return False


def _delete_thinking(client, channel: str, thinking_ts: str | None) -> None:
    """Delete the thinking placeholder (used when the reply is posted separately)."""
    if not thinking_ts:
        return
    try:
        client.chat_delete(channel=channel, ts=thinking_ts)
    except Exception:
        pass


def send_dm(client: WebClient, athlete: Athlete, text: str, db_session: Session | None = None) -> None:
    """Send a proactive DM to an athlete.

    Uses cached slack_dm_channel_id if available, otherwise opens a new DM.
    """
    channel_id = athlete.slack_dm_channel_id

    if not channel_id:
        result = client.conversations_open(users=[athlete.slack_user_id])
        channel_id = result["channel"]["id"]
        if db_session:
            athlete.slack_dm_channel_id = channel_id
            db_session.commit()

    client.chat_postMessage(channel=channel_id, text=text, mrkdwn=True)


def register_handlers(app: App) -> None:
    """Register all Slack event handlers on the app."""

    app.action(_ACTION_ID)(_handle_open_garmin_creds_modal)
    app.action(_ACTION_ID_SKIP)(_handle_skip_garmin_creds)
    app.view(_MODAL_CALLBACK_ID)(_handle_garmin_creds_modal_view)

    @app.event("message")
    def handle_message(event, client, say):
        # Guard: ignore bot's own messages
        if event.get("bot_id"):
            return
        # Guard: ignore message subtypes (edits, deletes, etc.)
        if event.get("subtype"):
            return
        # Only handle DMs
        if event.get("channel_type") != "im":
            return

        slack_user_id = event["user"]
        text = event.get("text", "").strip()
        channel = event["channel"]

        with get_session() as db_session:
            # Check for admin commands first
            if slack_user_id == settings.ADMIN_SLACK_USER_ID and text.startswith("!admin"):
                from running_coach_ai.slack.admin import handle_admin_command
                response = handle_admin_command(
                    slack_user_id, text, db_session, channel=channel, slack_client=client
                )
                if response:
                    say(response)
                return

            # Test mode: if admin is in test mode, route messages to the shadow test athlete
            if slack_user_id == settings.ADMIN_SLACK_USER_ID:
                from running_coach_ai.slack.admin import get_test_athlete_id
                test_athlete_id = get_test_athlete_id(slack_user_id)
                if test_athlete_id:
                    test_athlete = (
                        db_session.query(Athlete)
                        .filter(Athlete.id == test_athlete_id)
                        .first()
                    )
                    if test_athlete:
                        # Mirror the first-contact welcome flow if no conversation history yet
                        from running_coach_ai.database.models import ConversationMessage
                        has_history = db_session.query(ConversationMessage).filter(
                            ConversationMessage.athlete_id == test_athlete.id
                        ).count() > 0
                        if not has_history:
                            from running_coach_ai.slack.onboarding import generate_welcome
                            thinking_ts = _post_thinking(client, channel)
                            welcome = generate_welcome()
                            if not _finish_thinking(client, channel, thinking_ts, welcome):
                                _delete_thinking(client, channel, thinking_ts)
                                say(welcome)
                            db_session.add(ConversationMessage(athlete_id=test_athlete.id, role="user", content="(start)"))
                            db_session.add(ConversationMessage(athlete_id=test_athlete.id, role="assistant", content=welcome))
                            db_session.commit()
                            return
                        if not test_athlete.onboarding_complete:
                            from running_coach_ai.slack.onboarding import handle
                            thinking_ts = _post_thinking(client, channel)
                            handle(test_athlete, text, db_session, say, client=client,
                                   channel=channel, msg_ts=event.get("ts"))
                            _delete_thinking(client, channel, thinking_ts)
                        else:
                            from running_coach_ai.slack.conversation import handle_message as conv_handle
                            thinking_ts = _post_thinking(client, channel)
                            reply = conv_handle(test_athlete, text, db_session)
                            if not _finish_thinking(client, channel, thinking_ts, reply):
                                _delete_thinking(client, channel, thinking_ts)
                                say(reply)
                        return

            # Resolve athlete
            athlete = (
                db_session.query(Athlete)
                .filter(Athlete.slack_user_id == slack_user_id)
                .first()
            )

            # Check allowed list — create athlete record on first contact.
            # If ALLOWED_SLACK_USER_IDS is empty, open enrollment: any user can self-onboard.
            allowed = (
                not settings.allowed_user_ids
                or slack_user_id in settings.allowed_user_ids
            )
            if athlete is None:
                if allowed:
                    athlete = Athlete(
                        slack_user_id=slack_user_id,
                        slack_dm_channel_id=channel,
                        allowed=True,
                        onboarding_complete=False,
                        onboarding_step=0,
                    )
                    db_session.add(athlete)
                    db_session.flush()

                    # Generate a dynamic first greeting via Claude.
                    # Also save a synthetic "(start)" user turn so the messages array
                    # is always valid (Anthropic API requires starting with role="user").
                    from running_coach_ai.database.models import ConversationMessage
                    from running_coach_ai.slack.onboarding import generate_welcome
                    thinking_ts = _post_thinking(client, channel)
                    welcome = generate_welcome()
                    if not _finish_thinking(client, channel, thinking_ts, welcome):
                        _delete_thinking(client, channel, thinking_ts)
                        say(welcome)
                    db_session.add(ConversationMessage(
                        athlete_id=athlete.id,
                        role="user",
                        content="(start)",
                    ))
                    db_session.add(ConversationMessage(
                        athlete_id=athlete.id,
                        role="assistant",
                        content=welcome,
                    ))
                    db_session.commit()
                    return
                else:
                    say("This coaching service is private — ask the admin to add you.")
                    return
            elif not athlete.allowed:
                say("This coaching service is private — ask the admin to add you.")
                return

            # Cache DM channel ID
            if not athlete.slack_dm_channel_id:
                athlete.slack_dm_channel_id = channel
                db_session.commit()

            # Route to onboarding or conversation
            if not athlete.onboarding_complete:
                from running_coach_ai.slack.onboarding import handle
                thinking_ts = _post_thinking(client, channel)
                handle(athlete, text, db_session, say, client=client,
                       channel=channel, msg_ts=event.get("ts"))
                _delete_thinking(client, channel, thinking_ts)
            else:
                from running_coach_ai.slack.conversation import handle_message as conv_handle
                thinking_ts = _post_thinking(client, channel)
                reply = conv_handle(athlete, text, db_session)
                if not _finish_thinking(client, channel, thinking_ts, reply):
                    _delete_thinking(client, channel, thinking_ts)
                    say(reply)

    @app.event("app_mention")
    def handle_app_mention(event, client, say):
        """Handle @mentions — route identically to DM messages."""
        slack_user_id = event["user"]
        text = event.get("text", "").strip()

        with get_session() as db_session:
            athlete = (
                db_session.query(Athlete)
                .filter(Athlete.slack_user_id == slack_user_id)
                .first()
            )

            if not athlete or not athlete.allowed:
                say("This coaching service is private — ask the admin to add you.")
                return

            if not athlete.onboarding_complete:
                say("Let's continue your onboarding in our DM — send me a message there!")
                return

            from running_coach_ai.slack.conversation import handle_message as conv_handle
            thinking_ts = _post_thinking(client, event["channel"])
            reply = conv_handle(athlete, text, db_session)
            if not _finish_thinking(client, event["channel"], thinking_ts, reply):
                _delete_thinking(client, event["channel"], thinking_ts)
                say(reply)
