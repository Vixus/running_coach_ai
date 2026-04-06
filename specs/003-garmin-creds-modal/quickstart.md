# Quickstart: Garmin Credentials via Slack Modal

**Branch**: `003-garmin-creds-modal` | **Date**: 2026-04-05

Developer guide for implementing and testing this feature end-to-end.

---

## Prerequisites

- Existing running_coach_ai dev environment set up (`.env` populated, venv active)
- Slack app with Socket Mode enabled
- A test Slack workspace with the bot installed and at least one test athlete user ID in `ALLOWED_SLACK_USER_IDS`

---

## Step 1: Enable Interactivity in the Slack App Dashboard (One-Time)

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → select your app
2. Navigate to **Interactivity & Shortcuts** in the left sidebar
3. Toggle **Interactivity** on
4. Click **Save Changes**

> For Socket Mode apps, no Request URL is needed. The existing WebSocket connection handles all interactive payloads.

---

## Step 2: Apply the Database Migration

```powershell
# Activate venv
& .venv\Scripts\Activate.ps1

# Generate the migration (run from repo root)
alembic revision --autogenerate -m "add_pending_onboarding_data"

# Inspect the generated file in running_coach_ai/database/migrations/versions/
# Verify it adds pending_onboarding_data (JSON) and pending_onboarding_data_created_at (DateTime)

# Apply
alembic upgrade head
```

---

## Step 3: Implementation Order

Follow this order to keep tests green throughout development:

1. **`models.py`** — Add `pending_onboarding_data` and `pending_onboarding_data_created_at` columns to `Athlete`
2. **Alembic migration** — Generate and verify
3. **`onboarding.py`** — Modify `_SYSTEM_PROMPT` (remove Garmin credential question + JSON fields); modify `handle()` guard + completion handler; add `_send_garmin_credential_button()`; delete `_scrub_credentials()`
4. **`bot.py`** — Add `@app.action("open_garmin_creds_modal")` and `@app.view("garmin_creds_modal")` handlers; add constants `_ACTION_ID` and `_MODAL_CALLBACK_ID`
5. **`admin.py`** — Add `reset-onboarding` regex branch; implement `_reset_onboarding_athlete()`
6. **Tests** — `tests/unit/test_garmin_creds_modal.py` and `tests/unit/test_admin_reset_onboarding.py`

---

## Step 4: Running Tests

```powershell
# All unit tests (includes existing tests that must remain green)
pytest tests/unit/ -v

# Only the new tests for this feature
pytest tests/unit/test_garmin_creds_modal.py tests/unit/test_admin_reset_onboarding.py -v

# Verify existing onboarding tests still pass
pytest tests/unit/test_onboarding_garmin_sync.py tests/unit/test_onboarding_timezone.py -v
```

---

## Step 5: Manual End-to-End Test

### New athlete onboarding (the happy path)

1. Start the bot: `python main.py`
2. From the test athlete's Slack account, send a DM to the bot
3. Complete the onboarding conversation — confirm that at **no point** does the bot ask for a Garmin email or password in chat
4. After confirming the profile, the bot should send a message containing an **"Enter Garmin Credentials"** button
5. Click the button — a modal should open with:
   - A security disclaimer section
   - A warning that the password field is not masked
   - "Garmin Connect Email" input field
   - "Garmin Connect Password" input field
   - "Connect" submit button and "Cancel" close button
6. Enter valid Garmin Connect credentials; click **Connect**
7. The modal should close
8. Within a few seconds, you should receive a DM: _"Got it! Setting up your account, this may take a minute…"_
9. After 30–60 seconds, the bot should send the Week 1 training summary DM
10. Verify no messages in the conversation contain the Garmin email or password
11. Check `data/garmin_sessions/<athlete_id>/oauth1_token.json` exists

### Verify Slack message history is clean

```sql
-- Connect to coach.db with sqlite3
SELECT content FROM conversation_messages WHERE athlete_id = <id> ORDER BY created_at;
-- Should contain NO lines with the Garmin email or password
```

### Test: athlete sends a message while awaiting credentials

1. Complete steps 1–4 above (button message received)
2. Send a chat message before clicking the button
3. Bot should reply with a reminder and re-send the button message
4. Verify Claude was **not** called (no new conversation turn created)

### Test: wrong Garmin password

1. Follow steps 1–6 but enter an incorrect password
2. Modal closes; acknowledgement DM arrives
3. Bot should send a DM explaining the credentials were not accepted
4. The credential button should be re-sent

### Test: `!admin reset-onboarding <uid>` (send as admin)

```
!admin reset-onboarding U123ABC
```

Expected response: confirmation that the athlete has been reset and must re-onboard.
Verify in DB: `pending_onboarding_data = NULL`, `onboarding_complete = 0`, `onboarding_step = 0`, all `conversation_messages` deleted for that athlete.

### Test: TTL expiry (24 hours)

For testing without waiting 24 hours, temporarily modify `PENDING_DATA_TTL_HOURS = 0` in code or backdate `pending_onboarding_data_created_at` directly in the DB:

```sql
UPDATE athletes SET pending_onboarding_data_created_at = '2020-01-01 00:00:00' WHERE id = <id>;
```

Then click the credential button — bot should send a DM explaining the session expired and prompt the athlete to restart.

---

## Checklist Before Merging

- [ ] `pytest tests/unit/` passes with no failures
- [ ] `ruff check .` reports no lint errors
- [ ] Manual happy path test completed: no credentials in Slack history, Garmin session created, Week 1 plan received
- [ ] `!admin reset-onboarding` tested and working
- [ ] Alembic migration tested on a copy of the production DB (`alembic upgrade head` with no errors)
- [ ] Slack app Interactivity enabled in dashboard
