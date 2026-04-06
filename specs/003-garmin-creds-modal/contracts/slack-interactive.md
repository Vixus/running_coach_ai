# Contract: Slack Interactive Components — Garmin Credentials Modal

**Branch**: `003-garmin-creds-modal` | **Date**: 2026-04-05  
**Extends**: `specs/001-ai-running-coach/contracts/slack-events.md`

All interactive component traffic arrives via Socket Mode (existing WebSocket connection). No new Slack API scopes or public URLs are required.

---

## Required Slack App Scopes

Existing scopes already granted; no new scopes needed:

| Scope        | Already present | Reason           |
| ------------ | --------------- | ---------------- |
| `im:history` | Yes             | Read inbound DMs |
| `chat:write` | Yes             | Send DMs         |
| `im:write`   | Yes             | Open DM channels |

Interactive components (modals) use the same Socket Mode WebSocket — no additional permissions are required.

**Interactivity must be enabled** in the Slack app settings under "Interactivity & Shortcuts." This is a one-time configuration change in the Slack app dashboard.

---

## New Inbound Events

### Block Action: Open Garmin Credentials Modal (`block_actions`)

Triggered when the athlete clicks the "Enter Garmin Credentials" button in the credential button message.

```
Event type:       block_actions
Constant:         _ACTION_ID = "open_garmin_creds_modal"

Payload fields consumed:
  body["trigger_id"]          → Passed to client.views_open(); expires in 3 seconds
  body["user"]["id"]          → Slack user ID (used to look up Athlete, validate allowed)
```

**Handler behaviour**:

1. Call `ack()` immediately.
2. Look up athlete by `body["user"]["id"]`; verify `athlete.allowed`.
3. Check TTL: if `pending_onboarding_data` is expired, send DM explaining session expired; return.
4. Check state: if `athlete.onboarding_complete`, silently return (duplicate click after already done).
5. Call `client.views_open(trigger_id=body["trigger_id"], view=<modal_definition>)`.

---

### View Submission: Garmin Credentials Modal (`view_submission`)

Triggered when the athlete submits the modal form.

```
Event type:       view_submission
Constant:         _MODAL_CALLBACK_ID = "garmin_creds_modal"

Payload fields consumed:
  body["user"]["id"]          → Slack user ID (look up Athlete)
  view["state"]["values"]
    ["garmin_email_block"]["garmin_email_input"]["value"]     → Garmin Connect email
    ["garmin_password_block"]["garmin_password_input"]["value"] → Garmin Connect password
```

**Handler behaviour** (all after `ack()`):

1. Call `ack()` immediately.
2. Look up athlete by `body["user"]["id"]`.
3. Guard: if `athlete.onboarding_complete == True`, silently discard (FR-012).
4. Guard: if `pending_onboarding_data` is expired or null, send DM explaining session expired; return.
5. Send immediate acknowledgement DM: `"Got it! Setting up your account, this may take a minute…"`
6. Read `pending_onboarding_data`; merge in `garmin_email` + `garmin_password`.
7. Clear `pending_onboarding_data` and `pending_onboarding_data_created_at` on the Athlete.
8. Encrypt and set `athlete.garmin_email` and `athlete.garmin_password_encrypted`.
9. Call `_complete_onboarding(athlete, merged_data, db_session, say_fn)`.
   - On Garmin auth failure: catch the exception, send a DM with the error, re-send the button message.
   - On success: `_complete_onboarding` sends the week 1 summary DM.

---

## New Outbound Components

### Button Message — Garmin Credential Prompt

Sent by `_send_garmin_credential_button(channel, client)` after profile confirmation.

```json
{
  "channel": "<athlete_dm_channel_id>",
  "text": "Your profile is all set! One last step — I need your Garmin Connect credentials to build your plan.",
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "Your profile is all set! One last step — I need your Garmin Connect credentials to sync your training plan and pull your activity history."
      }
    },
    {
      "type": "actions",
      "elements": [
        {
          "type": "button",
          "text": {
            "type": "plain_text",
            "text": "Enter Garmin Credentials"
          },
          "action_id": "open_garmin_creds_modal",
          "style": "primary"
        }
      ]
    }
  ]
}
```

---

### Modal Definition — Garmin Credentials Form

Passed as `view` to `client.views_open()`.

```json
{
  "type": "modal",
  "callback_id": "garmin_creds_modal",
  "title": {
    "type": "plain_text",
    "text": "Garmin Credentials"
  },
  "submit": {
    "type": "plain_text",
    "text": "Connect"
  },
  "close": {
    "type": "plain_text",
    "text": "Cancel"
  },
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": ":lock: Your credentials are encrypted immediately on receipt and never stored in plain text. This form is not visible in your chat history."
      }
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": ":warning: *The password field below does not hide what you type.* Type your password somewhere private before clicking Connect."
      }
    },
    {
      "type": "input",
      "block_id": "garmin_email_block",
      "label": {
        "type": "plain_text",
        "text": "Garmin Connect Email"
      },
      "element": {
        "type": "plain_text_input",
        "action_id": "garmin_email_input",
        "placeholder": {
          "type": "plain_text",
          "text": "you@example.com"
        }
      }
    },
    {
      "type": "input",
      "block_id": "garmin_password_block",
      "label": {
        "type": "plain_text",
        "text": "Garmin Connect Password"
      },
      "element": {
        "type": "plain_text_input",
        "action_id": "garmin_password_input",
        "placeholder": {
          "type": "plain_text",
          "text": "Your Garmin password"
        }
      }
    }
  ]
}
```

---

## Slack App Dashboard Configuration

One-time change required in the Slack app settings at api.slack.com/apps:

1. Navigate to **Interactivity & Shortcuts**
2. Enable **Interactivity** (toggle on)
3. In Socket Mode apps, no Request URL is required — the existing WebSocket handles it
4. Save changes

No other configuration changes are needed.
