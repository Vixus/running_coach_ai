# Research: Multi-Coach Personas

**Feature**: 004-multi-coach-personas  
**Date**: 2026-04-05  
**Status**: Complete — all NEEDS CLARIFICATION items resolved

---

## 1. Coach Persona Definitions (launch roster)

### Decision

Define 3 coaches at launch: **Coach Alex** ("classic"), **Coach Sofia** ("sofia"), and **Coach Miles** ("miles").

### Rationale

- 3 coaches satisfy FR-001 and SC-004 (distinguishable without label).
- The existing `COACH_PERSONA` string in `coach/persona.py` becomes Coach Alex ("classic") — the default. No text is lost; it is simply moved to `coach/personas.py` as `persona_block`.
- Sofia offers a science-first, data-forward coaching voice — appeals to analytically inclined athletes.
- Miles offers a high-energy motivational style — appeals to athletes who need encouragement and accountability over analysis.

### Coach Profiles

| Key       | Name        | Personality Summary                                                                                                                                                                                                                                                                                      |
| --------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `classic` | Coach Alex  | The original: elite endurance veteran, 30+ years D1 experience, polarized training purist, HRV-obsessed, warm-but-demanding, data-precise. The current `COACH_PERSONA` text verbatim. **Default coach.**                                                                                                 |
| `sofia`   | Coach Sofia | Evidence-based sports scientist turned coach. PhD-level knowledge delivered accessibly. Methodical, calm, and thorough. Explains the physiology behind every decision. Uses research citations sparingly but memorably ("studies on deload timing show..."). Less warm than Alex but deeply trustworthy. |
| `miles`   | Coach Miles | High-energy, motivational, competitive. Former collegiate miler. Pushes athletes to embrace discomfort. Short punchy sentences. Uses athletic shorthand ("let's get after it", "dig in"). Just as rigorous on data but frames everything as a challenge to rise to, not a problem to avoid.              |

### Alternatives Considered

- **5 coaches at launch**: Rejected — too many options for a small athlete roster; harder to make each feel meaningfully different.
- **User-customizable personas**: Rejected per spec (FR must remain operator-defined).

---

## 2. Persona Block Structure

### Decision

Each `CoachPersona` has a `persona_block: str` — a complete system prompt persona section. For "classic", this is the existing `COACH_PERSONA` string verbatim (including all side-effect tag instructions). For "sofia" and "miles", the block is a tailored version with identical structural sections (philosophy, units, style, XML tags) but different language.

### Rationale

- All athletes use the same XML side-effect tags and unit conventions. Duplicating the tag instructions in each persona block (rather than in a shared suffix) keeps each persona self-contained and avoids assembly complexity.
- The unit/tag instructions are anchored to the persona voice, so copying them into each coach block is appropriate (e.g., Miles delivers the tag rules punchily; Sofia delivers them methodically).
- **`<coach_switch>` tag instruction** is included in each coach's persona block, so Claude can always emit it regardless of active coach.

### Alternatives Considered

- **Shared suffix for tag instructions**: Would require prompt assembly to combine persona block + shared block. Adds complexity with marginal benefit given only 3 coaches.

---

## 3. `get_persona()` Lookup — Fallback Strategy

### Decision

```python
def get_persona(coach_key: str | None) -> CoachPersona:
    if coach_key and coach_key in PERSONAS:
        return PERSONAS[coach_key]
    return PERSONAS[DEFAULT_COACH_KEY]
```

### Rationale

- `None` (existing athletes before migration) and unrecognised keys both fall back to default — satisfies FR-009, FR-011, and Constitution Principle V.
- No exception raised — callers do not need try/except around persona lookup.
- `extract_coach_switch()` separately validates the key before writing to DB, so invalid keys never reach the fallback in normal operation.

---

## 4. `extract_coach_switch()` Integration Point

### Decision

Called in `handle_message()` in `conversation.py` **after** `extract_and_save_memories()`, before persisting messages to DB:

```python
response = extract_and_apply_plan(athlete.id, response, db_session)
_reconcile_cancelled_garmin_workouts(athlete.id, db_session)
response, sync_note = extract_and_sync_garmin(...)
response = extract_and_save_memories(athlete.id, response, db_session)
response = extract_coach_switch(athlete, response, db_session)   # NEW
```

### Rationale

- Coach switch has no dependency on plan or Garmin state — ordering after `extract_and_save_memories()` is natural.
- The function strips the `<coach_switch>` tag from response before the message is persisted and sent to the athlete (consistent with how all other side-effect tags are handled).
- If the key is invalid, the tag is stripped silently and a warning is logged — Claude's accompanying explanation still reaches the athlete.

---

## 5. Onboarding `_SYSTEM_PROMPT` Modification

### Decision

The `_SYSTEM_PROMPT` string in `onboarding.py` is modified to:

1. Import coach list from `coach/personas.py` at call time (not at module load) to avoid circular imports.
2. Add a `{coaches_block}` template placeholder (or build the system prompt dynamically in `generate_welcome()` and `handle()`).
3. Instruct Claude to present coaches, get athlete's selection, and include `coach_key` in `<onboarding_complete>` JSON.

**Preferred approach**: Build `_SYSTEM_PROMPT` as a function `_build_onboarding_system_prompt()` that inserts the coach list at call time — avoids module-level import of personas and keeps the system prompt always up-to-date with the registry.

### Rationale

- `_SYSTEM_PROMPT` is currently a module-level constant. Converting it to a function has minimal cost (called once per onboarding message) and avoids tight coupling between module load order.
- The coach list is short (3 items) and does not change at runtime, so regenerating it per call is acceptable.

---

## 6. Alembic Migration Strategy

### Decision

Single migration: `add_coach_key_to_athletes`

```sql
ALTER TABLE athletes ADD COLUMN coach_key TEXT;
UPDATE athletes SET coach_key = 'classic' WHERE coach_key IS NULL;
```

- `coach_key` is `nullable=True` in the SQLAlchemy model (existing rows start as NULL until migration runs).
- Runtime `get_persona()` handles NULL gracefully (falls back to default), so service can start even if migration hasn't run yet.

### Rationale

- SQLite does not support `NOT NULL` with `ALTER TABLE ADD COLUMN` unless a `DEFAULT` is provided. Adding `DEFAULT 'classic'` directly is also valid and simpler — either approach works.
- The "NULL is handled at runtime" approach means no data loss risk if migration is delayed.

---

## 7. Files Requiring `COACH_PERSONA` → `get_persona()` Migration

All call sites identified via grep:

| File                    | Line                    | Current call                      | Change required                                                  |
| ----------------------- | ----------------------- | --------------------------------- | ---------------------------------------------------------------- |
| `coach/adapter.py`      | 170                     | `call_claude(COACH_PERSONA, ...)` | `call_claude(get_persona(athlete.coach_key).persona_block, ...)` |
| `coach/adapter.py`      | 196, 252                | `call_claude(COACH_PERSONA, ...)` | Same — `athlete` is in scope                                     |
| `coach/feedback.py`     | 312, 359                | `call_claude(COACH_PERSONA, ...)` | Same — `athlete` is in scope                                     |
| `slack/conversation.py` | `build_system_prompt()` | `sections = [COACH_PERSONA]`      | `sections = [get_persona(athlete.coach_key).persona_block]`      |
| `slack/onboarding.py`   | `_SYSTEM_PROMPT`        | N/A — separate onboarding prompt  | No change to persona (onboarding is coach-neutral by design)     |

`onboarding.py` uses a separate `_SYSTEM_PROMPT` that is onboarding-specific — it does **not** need to use the selected coach's persona block. The athlete hasn't picked a coach yet. Coach selection happens **within** the onboarding conversation.

---

## Summary of All Decisions

| #   | Question                          | Decision                                                                          |
| --- | --------------------------------- | --------------------------------------------------------------------------------- |
| 1   | Coach personas at launch          | 3: classic (Alex), sofia (Sofia), miles (Miles)                                   |
| 2   | Persona block structure           | Full self-contained block per coach (incl. tag instructions)                      |
| 3   | Fallback strategy                 | `get_persona(None)` → `PERSONAS[DEFAULT_COACH_KEY]`; no exception                 |
| 4   | `extract_coach_switch()` position | After `extract_and_save_memories()` in `handle_message()`                         |
| 5   | Onboarding prompt                 | Convert `_SYSTEM_PROMPT` constant to `_build_onboarding_system_prompt()` function |
| 6   | Migration                         | ADD COLUMN `coach_key TEXT`, UPDATE existing rows to `'classic'`                  |
| 7   | Call site migration               | 5 call sites in adapter.py, feedback.py, conversation.py — all `athlete` in scope |
