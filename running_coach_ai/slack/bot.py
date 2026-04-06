"""Slack Bolt event handlers and proactive DM helper."""

import logging

from slack_bolt import App
from slack_sdk import WebClient
from sqlalchemy.orm import Session

from running_coach_ai.config import settings
from running_coach_ai.database.models import Athlete
from running_coach_ai.database.session import get_session

logger = logging.getLogger(__name__)


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
                response = handle_admin_command(slack_user_id, text, db_session)
                if response:
                    say(response)
                return

            # Resolve athlete
            athlete = (
                db_session.query(Athlete)
                .filter(Athlete.slack_user_id == slack_user_id)
                .first()
            )

            # Check allowed list — create athlete record on first contact
            if athlete is None:
                if slack_user_id in settings.allowed_user_ids:
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
                    welcome = generate_welcome()
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
                    say(welcome)
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
                handle(athlete, text, db_session, say, client=client,
                       channel=channel, msg_ts=event.get("ts"))
            else:
                from running_coach_ai.slack.conversation import handle_message as conv_handle
                reply = conv_handle(athlete, text, db_session)
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
            reply = conv_handle(athlete, text, db_session)
            say(reply)
