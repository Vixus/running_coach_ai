# Contract: Slack Event Handling

**Branch**: `001-ai-running-coach` | **Date**: 2026-03-31

The Slack bot operates in Socket Mode. All inbound interactions arrive as events via a persistent WebSocket connection — no public URL required.

---

## Inbound Events

### DM Message (`message.im`)

Triggered when any user sends a direct message to the bot.

```
Event type:     message
Channel type:   im
Required scopes: im:history

Payload fields consumed:
  event.user          → Slack user ID (resolve to Athlete)
  event.text          → Message content
  event.ts            → Message timestamp
  event.channel       → DM channel ID (cache as athlete.slack_dm_channel_id)
  event.bot_id        → Present if message is from a bot; MUST be ignored
  event.subtype       → If present (e.g. "message_changed"), ignore
```

**Routing logic**:
1. Ignore if `event.bot_id` is set (bot's own messages echo back).
2. Ignore if `event.subtype` is set.
3. Resolve `event.user` → Athlete row.
4. If user not in allowed list → polite decline.
5. If athlete not yet onboarded → resume onboarding flow.
6. Otherwise → coaching conversation flow.

### App Mention (`app_mention`)

Triggered when the bot is @-mentioned in any channel. Route identically to DM message after extracting the user and text.

```
Required scopes: app_mentions:read
Payload: event.user, event.text, event.channel, event.ts
```

---

## Outbound Messages

### Proactive DM (check-ins, feedback, weekly review)

```
Method: client.conversations_open(users=[slack_user_id])
→ channel_id = result["channel"]["id"]

Method: client.chat_postMessage(
  channel=channel_id,
  text=message_text,    # plain text or mrkdwn
  mrkdwn=True
)
```

### Event Response (conversation reply)

```
Method: say(text=reply_text, mrkdwn=True)
# or equivalently:
client.chat_postMessage(channel=event["channel"], text=reply_text)
```

---

## Admin Commands

Admin commands arrive as **DM messages** from `ADMIN_SLACK_USER_ID` with the `!admin` prefix. They are intercepted before the general conversation router. Slash commands (`/`) are **not** used — this is a socket-mode-only architecture.

| Command pattern | Action |
|----------------|--------|
| `!admin add <user_id>` | Add user to allowed list (create Athlete row if absent; set `allowed=True`) |
| `!admin remove <user_id>` | Set `allowed=False` on Athlete row; retain all data |
| `!admin list` | Reply with list of allowed athlete names + Slack user IDs |
| `!admin resync-garmin [<user_id>]` | Re-upload all upcoming workouts to Garmin (all athletes if no user_id given) |
| `!admin clean-garmin [<user_id>]` | Wipe entire Garmin workout library for athlete, clear DB IDs, re-sync fresh |
| `!admin verify-garmin [<user_id>]` | Compare DB-planned workouts vs live Garmin Connect calendar and report drift |

Admin commands MUST be silently ignored (or politely declined without revealing the command vocabulary) if sent by a non-admin user.

### Revision: Implementation Sync 2026-04-04
- Reason: Command prefix corrected from `/admin` (slash command) to `!admin` (DM text prefix). Added resync-garmin, clean-garmin, and verify-garmin commands that are implemented but were missing from this contract.

---

## Slash Commands (Optional)

| Command | Handler | Response |
|---------|---------|----------|
| `/status` | Return today's planned session + last health snapshot | Ephemeral reply |
| `/plan` | Return this week's training schedule | Ephemeral reply |
| `/skip-today` | Mark today's planned session as skipped | Confirm in DM |

These are optional and lower priority than the core conversation flow.

---

## Rate Limiting & Error Handling

- Slack API rate limits: `chat.postMessage` tier 1 (1 msg/sec per channel). For bulk proactive DMs across multiple athletes, add a small delay between messages.
- If `chat_postMessage` fails, log and retry once after 2 seconds. Do not crash the scheduler job.
- Socket Mode reconnects automatically on disconnect; Bolt handles this internally.
