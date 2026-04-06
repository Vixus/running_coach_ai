# Data Model: Multi-Coach Personas

**Feature**: 004-multi-coach-personas  
**Date**: 2026-04-05

---

## Overview

This feature introduces one new Python-level domain object (`CoachPersona`) and one new database column (`Athlete.coach_key`). No new DB tables. No new external APIs.

---

## New Domain Object: `CoachPersona` (Python, not DB)

**Location**: `running_coach_ai/coach/personas.py`

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class CoachPersona:
    key: str           # Registry key, e.g. "classic", "sofia", "miles"
    name: str          # Display name, e.g. "Coach Alex"
    description: str   # Athlete-facing description shown during coach selection
    persona_block: str # Full system prompt persona section injected into build_system_prompt()
```

**Registry**:

```python
PERSONAS: dict[str, CoachPersona] = {
    "classic": CoachPersona(key="classic", name="Coach Alex", description="...", persona_block=...),
    "sofia":   CoachPersona(key="sofia",   name="Coach Sofia", description="...", persona_block=...),
    "miles":   CoachPersona(key="miles",   name="Coach Miles", description="...", persona_block=...),
}
DEFAULT_COACH_KEY = "classic"
```

**Lookup function**:

```python
def get_persona(coach_key: str | None) -> CoachPersona:
    """Return the persona for the given key, falling back to the default."""
    if coach_key and coach_key in PERSONAS:
        return PERSONAS[coach_key]
    return PERSONAS[DEFAULT_COACH_KEY]
```

**Validation function** (used by `extract_coach_switch()`):

```python
def is_valid_coach_key(key: str) -> bool:
    return key in PERSONAS
```

---

## Modified Entity: `Athlete`

**Table**: `athletes`  
**File**: `running_coach_ai/database/models.py`

### New Column

| Column      | Type   | Nullable | Default                                    | Description                                                                                                  |
| ----------- | ------ | -------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `coach_key` | `TEXT` | Yes      | `NULL` → treated as `"classic"` at runtime | Key into the `PERSONAS` registry in `coach/personas.py`. No FK constraint — registry is the source of truth. |

### SQLAlchemy Model Change

```python
class Athlete(Base):
    # ... existing columns ...
    coach_key = Column(Text, nullable=True)  # NEW — NULL treated as DEFAULT_COACH_KEY
```

### Validation Rules

- `coach_key` is never written to DB without first validating it exists in `PERSONAS` (enforced in `extract_coach_switch()` and `_complete_onboarding()`).
- `NULL` is valid storage state — `get_persona()` handles it gracefully.
- Max cardinality values: `"classic"`, `"sofia"`, `"miles"` at launch.

---

## New Alembic Migration

**File**: `running_coach_ai/database/migrations/versions/XXXX_add_coach_key_to_athletes.py`

```python
"""add coach_key to athletes

Revision ID: <generated>
Revises: <previous head>
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

def upgrade() -> None:
    op.add_column("athletes", sa.Column("coach_key", sa.Text(), nullable=True))
    # Backfill existing athletes with the default coach key
    op.execute("UPDATE athletes SET coach_key = 'classic' WHERE coach_key IS NULL")

def downgrade() -> None:
    op.drop_column("athletes", "coach_key")
```

---

## New Side-Effect Tag: `<coach_switch>`

**Processed by**: `extract_coach_switch()` in `conversation.py`  
**Emitted by**: Claude, during post-onboarding coach switching flow

### Format

```xml
<coach_switch>miles</coach_switch>
```

### Processing Logic

```python
def extract_coach_switch(athlete: Athlete, response: str, db_session: Session) -> str:
    match = re.search(r"<coach_switch>(.*?)</coach_switch>", response, re.DOTALL)
    if not match:
        return response
    new_key = match.group(1).strip()
    if is_valid_coach_key(new_key):
        athlete.coach_key = new_key
        db_session.commit()
        logger.info("Coach switch for athlete %d: → %s", athlete.id, new_key)
    else:
        logger.warning("Invalid coach_key '%s' in <coach_switch> for athlete %d — ignored", new_key, athlete.id)
    return re.sub(r"<coach_switch>.*?</coach_switch>", "", response, flags=re.DOTALL).strip()
```

### Behaviour on Invalid Key

- Tag is stripped from response (so athlete never sees raw XML).
- Switch is not applied — athlete retains their current coach.
- Warning is logged.

---

## State Diagram: Athlete → Coach Assignment

```
New athlete → (onboarding) → coach_key set from onboarding_complete JSON
                                 └── if absent → DEFAULT_COACH_KEY ("classic")

Existing athlete (deploy) → migration sets coach_key = 'classic'

Any athlete → (post-onboarding message) → Claude emits <coach_switch>key</coach_switch>
                                             └── extract_coach_switch() validates & persists
                                             └── invalid key → no change, warning logged
```

---

## What Does NOT Change

- No new DB tables.
- No changes to `Goal`, `TrainingPlan`, `PlannedWorkout`, `CompletedWorkout`, `CoachMemory`, `HealthSnapshot`, or `ConversationMessage`.
- Garmin sync state is completely unaffected by coach changes.
- Training plan, goals, and history are completely unaffected by coach changes.
- `onboarding.py`'s `_SYSTEM_PROMPT` (the onboarding intake prompt) is NOT replaced by the selected coach's persona — it remains a neutral onboarding facilitator that introduces coaches and solicits a selection.
