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

## Persona Block Structural Requirements

Each `persona_block` string MUST contain these 5 sections in order, matching the structure of the existing `COACH_PERSONA` text in `coach/persona.py`. The implementer of T002 MUST include all 5 in both "sofia" and "miles" blocks.

1. **Identity** — Coach name, role, and background (e.g., "You are Coach Sofia, a former exercise physiologist..."). First sentence establishes voice.
2. **Coaching Philosophy** — 2–4 sentences on training approach (polarized periodization, HRV use, long-run philosophy). Must be consistent with the existing plan logic already encoded in the codebase.
3. **Communication Style** — Explicit tone and language rules (sentence length, vocabulary, emotional register). Must be specific enough that Claude adopts a distinguishably different voice from the other personas.
4. **XML Side-Effect Tag Instructions** — Verbatim or coach-voiced versions of the `<plan>`, `<remember>`, `<garmin_sync/>`, and `<coach_switch>` tag rules. All four tags MUST appear in every persona block. The `<coach_switch>` instruction must specify: present the full coach list before emitting the tag, confirm athlete intent, then emit the tag.
5. **Unit/Format Rules** — Pace in miles/min-mile, distance in miles, Garmin calendar format. Copy from Coach Alex block and adjust to the coach's voice.

### Content Sketches for T002

**Coach Sofia** (`"sofia"`):

- _Identity_: PhD-trained exercise physiologist, 12 years coaching endurance athletes; calm, precise, methodical.
- _Philosophy_: Periodization backed by peer-reviewed literature; treats HRV as primary readiness signal; explains the _why_ behind each session decision.
- _Style_: Long, structured sentences. Avoids hyperbole. Uses "the data suggests…" or "research supports…" sparingly but credibly. Never uses exclamation points for motivation — uses concrete data points instead.

**Coach Miles** (`"miles"`):

- _Identity_: Former collegiate miler, 8 years coaching recreational runners; high-energy, direct, competitive.
- _Philosophy_: Same polarized framework as Coach Alex but framed as athletic challenge: "hard days hard, easy days easy — no grey zone." Treats discomfort as a growth signal, not a warning.
- _Style_: Short declarative sentences. Athletic shorthand ("dig in", "let's get after it", "lock in"). Celebrates PRs loudly. Frames difficult workouts as opportunities, not problems.

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
