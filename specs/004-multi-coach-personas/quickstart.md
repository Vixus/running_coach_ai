# Quickstart: Multi-Coach Personas

**Feature**: 004-multi-coach-personas  
**Date**: 2026-04-05

---

## Overview

This document describes how the multi-coach system works end-to-end, how to add a new coach, and how to test the feature locally.

---

## How It Works

### Coach Registry

Coaches are defined in `running_coach_ai/coach/personas.py` as a `dict[str, CoachPersona]`:

```python
PERSONAS = {
    "classic": CoachPersona(key="classic", name="Coach Alex", ...),
    "sofia":   CoachPersona(key="sofia",   name="Coach Sofia", ...),
    "miles":   CoachPersona(key="miles",   name="Coach Miles", ...),
}
DEFAULT_COACH_KEY = "classic"
```

### Persona Injection

Every Claude call that generates athlete-facing output uses `get_persona(athlete.coach_key).persona_block` as the system prompt persona section:

| Call site                                | Before                            | After                                                            |
| ---------------------------------------- | --------------------------------- | ---------------------------------------------------------------- |
| `conversation.py: build_system_prompt()` | `sections = [COACH_PERSONA]`      | `sections = [get_persona(athlete.coach_key).persona_block]`      |
| `adapter.py: run_morning_checkin()`      | `call_claude(COACH_PERSONA, ...)` | `call_claude(get_persona(athlete.coach_key).persona_block, ...)` |
| `adapter.py: adapt_next_week()`          | `call_claude(COACH_PERSONA, ...)` | `call_claude(get_persona(athlete.coach_key).persona_block, ...)` |
| `feedback.py: generate_weekly_review()`  | `call_claude(COACH_PERSONA, ...)` | `call_claude(get_persona(athlete.coach_key).persona_block, ...)` |

### Coach Selection During Onboarding

`_build_onboarding_system_prompt()` (formerly `_SYSTEM_PROMPT`) dynamically builds the onboarding prompt with the coach roster injected. Claude asks the athlete to pick a coach. The selected `coach_key` is included in the `<onboarding_complete>` JSON:

```json
{
  "name": "Simon",
  "coach_key": "sofia",
  ...
}
```

`_complete_onboarding()` validates and persists `athlete.coach_key`. If absent or invalid, it defaults to `"classic"`.

### Coach Switching Post-Onboarding

Athlete: "I want to switch coaches."  
Claude: presents the available coaches, athlete picks one, Claude confirms and emits:

```xml
<coach_switch>miles</coach_switch>
```

`extract_coach_switch()` in `conversation.py` validates the key, persists it, strips the tag from the response.

---

## Adding a New Coach

1. **Define the persona** in `running_coach_ai/coach/personas.py`:

```python
PERSONAS["nova"] = CoachPersona(
    key="nova",
    name="Coach Nova",
    description="A mindfulness-first coach who treats running as moving meditation. "
                "Calm, patient, and focused on sustainable joy in the sport.",
    persona_block="""You are Coach Nova, a mindfulness-oriented endurance running coach...

    [full system prompt persona text — include all XML tag instructions]
    """,
)
```

2. **That's it.** The new coach is immediately:
   - Available to athletes during onboarding
   - Selectable via `<coach_switch>nova</coach_switch>`
   - Listed when athletes ask to browse coaches
   - Protected by `get_persona()` fallback (won't break if key is misspelled at DB level)

> ⚠️ Every persona block **must** include the full XML tag instruction section (`<plan>`, `<remember>`, `<garmin_sync/>`, `<coach_switch>`) — copy from the "classic" block and adapt the tone.

3. **No migration needed** for a new coach. The registry is the source of truth. Existing athletes with `coach_key = "classic"` are unaffected.

---

## Running Tests

```bash
# All new unit tests for this feature
pytest tests/unit/test_coach_personas.py
pytest tests/unit/test_coach_switch_tag.py
pytest tests/unit/test_onboarding_coach.py

# Run all unit tests
pytest tests/unit/
```

### What the tests cover

| Test file                  | Scenarios                                                                                                                                                                                            |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_coach_personas.py`   | Registry has 3 coaches; `get_persona()` returns correct persona; `get_persona(None)` returns default; `get_persona("unknown")` returns default; `is_valid_coach_key()` returns True/False correctly  |
| `test_coach_switch_tag.py` | Valid `<coach_switch>sofia</coach_switch>` → `athlete.coach_key` updated, tag stripped; invalid key → no DB write, tag stripped, warning logged; no tag → returns unchanged response                 |
| `test_onboarding_coach.py` | `onboarding_complete` JSON with `coach_key` → `athlete.coach_key` set; JSON without `coach_key` → `athlete.coach_key` defaults to `"classic"`; invalid `coach_key` in JSON → defaults to `"classic"` |

---

## Applying the Migration

```bash
# Generate the migration (if not already generated)
alembic revision --autogenerate -m "add coach_key to athletes"

# Apply
alembic upgrade head
```

For Docker / Synology NAS deployments, migration runs on `docker-compose up` (the startup sequence calls `alembic upgrade head`).

---

## Testing Coach Selection in Slack

1. **New athlete path**: Set up a test athlete in `ALLOWED_SLACK_USER_IDS`. Message the bot. During onboarding, Claude will present the 3 coaches. Select "sofia". After onboarding completes, trigger a morning check-in and verify the message is in Sofia's voice.

2. **Switch path**: As an existing onboarded athlete, send: "I'd like to switch coaches." Claude should list available coaches. Reply "Miles". Claude should confirm and emit `<coach_switch>miles</coach_switch>`. Verify `athlete.coach_key = "miles"` in the DB and that the next morning check-in uses Miles's voice.

3. **Browse path**: Send: "Can I see my coach options?" Claude should list all 3 coaches with descriptions but not switch unless you explicitly confirm.

4. **Database check**:

```bash
docker-compose exec coach python -c "
from running_coach_ai.database.session import get_session
from running_coach_ai.database.models import Athlete
with get_session() as db:
    for a in db.query(Athlete).all():
        print(a.name, a.coach_key)
"
```
