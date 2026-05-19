# Today Card — collapse OFF_PLAN into REST_DAY with recovery tips

**Branch:** `007-today-card`
**Status:** design approved 2026-05-19

## Problem

When an athlete has an active Goal but no `PlannedWorkout` row for today, the
Today Card displays the `OFF_PLAN` state with the headline *"Your plan needs
attention"* and a *"Review my plan"* CTA. This sounds alarming. In practice,
plans frequently skip days without writing an explicit `rest` row — a typical
5-day-per-week schedule has two zero-row days each week, and each one currently
trips this state.

The athlete reads "attention required" and thinks something broke. What they
should read is "it's a rest day — here's how to recover."

## Goal

Treat any day with no planned workout as a rest day with concrete recovery
guidance. Remove the `OFF_PLAN` state entirely from the Today Card state model.

## Non-goals

- Don't rebuild the rest-day cover layout. The change is text + a small visual
  affordance (cues strip), not a redesign.
- Don't add a new state for "between training blocks." The user explicitly
  accepted that athletes with a stale plan will see rest days until they chat —
  the in-chat coach already handles "let's pick the next race."
- Don't touch the `NO_PLAN` state (no active Goal). That path keeps its
  *"Pick a race"* CTA.
- Don't change the morning_checkin → coach_analysis → rule_based → placeholder
  rationale source ladder. Only the rule_based fallback differs on rest days.

## Design

### 1. State resolver — drop OFF_PLAN

`running_coach_ai/web/api/today.py` :: `_resolve_state` currently returns five
non-default states plus the `PRE_RUN` fallback. Remove the `OFF_PLAN` branch:

```python
# Before
if planned is None:
    return "OFF_PLAN", goal, None, None

# After — fall through; an active Goal with no planned row is a rest day.
if planned is None:
    return "REST_DAY", goal, None, None
```

The precedence order stays the same:
`NO_PLAN > RACE_DAY > COMPLETED > REST_DAY > PRE_RUN`. The five states are now
`PRE_RUN`, `COMPLETED`, `REST_DAY`, `RACE_DAY`, `NO_PLAN`.

Delete `_build_off_plan` and its dispatch branch in the `today()` endpoint.

### 2. `_build_rest_day` accepts `planned: PlannedWorkout | None`

The signature widens. The only `planned` reference inside the builder today is
passing it through to the rationale helper; we'll route to a new rest-specific
helper instead (next section), so the builder no longer needs `planned` at all
beyond passing `None` through. Headline copy is unchanged for both cases:

```
eyebrow:  "Rest Day"
ribbon:   "Rest Day"
title:    "Recovery is the workout"
subtitle: None
```

Cover lines stay as the existing 4-up morning grid (HRV / Body Battery / Sleep
/ RHR) from `_morning_cover_lines`. New: a small `cues` block on the payload
(see §5).

### 3. Rest-aware rationale — `rule_based_rest()` in `coach/today_rationale.py`

The current `rule_based_morning()` snapshot templates all close with "run it
as written" or "run the easy end of the pace range" — wrong for a rest day.
Today's REST_DAY path already hits this via `rule_based_morning(snap, planned=rest_row, ...)`,
producing nonsense rationale (a latent bug, not just an OFF_PLAN side effect).

Add a new pure function next to `rule_based_morning`:

```python
def rule_based_rest(
    snap: "HealthSnapshot | None",
    plan: "TrainingPlan | None",
    goal: "Goal | None",
    athlete_name: str,
) -> str:
    """Coach-voice paragraph for a rest day. No 'run it' framing."""
```

Three templates, picked by `_hrv_bucket` + `_sleep_hours`:

| Branch | Trigger | Voice |
|---|---|---|
| **low-recovery** | HRV `low` OR sleep < 6.5 h | "Your body is asking for room. Today is full recovery — sleep early, eat real food, stay off the legs except for an easy walk if you're stiff. Nothing more." |
| **recharged** | HRV `high` AND sleep ≥ 7.0 h | "Numbers look good — that's last week's work landing. Don't rush the next session. Sleep, fuel, walk if you want to move. The fitness shows up after rest, not in spite of it." |
| **steady** | Everything else (including missing snapshot) | "Today isn't about doing less, it's about doing the right less. Sleep early, hydrate to clear-pale-yellow, and give the hips and ankles 10 minutes of mobility. Walk if you want; don't run." |

Each template:
- Addresses the athlete by first name.
- References observed metric values numerically when snapshot is present.
- Names rest framing explicitly ("absorption", "recharge", "the right less").
- Closes with concrete, scannable tips that mirror the cues strip.
- Periodization clause (`week N of base block on the road to Race`) is
  appended when `plan` and `goal` are available, same shape as
  `rule_based_morning`.

`_build_rest_day` calls `rule_based_rest(snap, plan_view, goal, athlete.name)`
on the rule-based branch. The morning_checkin and placeholder branches above
it are unchanged.

### 4. Cues strip — payload + frontend

The Today Card payload gets one new optional field, populated only for
`REST_DAY`:

```jsonc
{
  "state": "REST_DAY",
  ...,
  "cues": [
    {"label": "Sleep",  "copy": "in bed early"},
    {"label": "Fuel",   "copy": "carbs + protein"},
    {"label": "Move",   "copy": "walk or mobility"}
  ]
}
```

For v1 the cues are static (same three for every rest day). The backend writes
them; the frontend renders them only when present. Future iterations can make
them snapshot-driven (e.g. "hydrate" when sleep was short, "mobility" after a
long-run week) without a contract change.

### 5. Frontend — `magazine.html` / `magazine.css` / `magazine.js`

**HTML** (`magazine.html`, between `td-on-watch` and `td-cta`):

```html
<div class="td-cues" id="td-cues" hidden>
  <span><strong></strong></span><span><strong></strong></span><span><strong></strong></span>
</div>
```

**CSS** (`magazine.css`, added to the Today Card block):

```css
.td-cues {
  display: flex; gap: 16px; flex-wrap: wrap;
  font-size: 10px; color: rgba(255,255,255,.65); margin: 6px 0 14px;
  padding: 8px 12px;
  background: rgba(184,255,79,.06);
  border-left: 1px solid rgba(184,255,79,.4);
  border-radius: 0 3px 3px 0;
  position: relative; z-index: 3;
}
.td-cues span strong {
  color: rgba(184,255,79,.85); font-weight: 600;
  margin-right: 4px; letter-spacing: .08em;
  text-transform: uppercase; font-size: 9px;
}
```

Per Option D from the visual companion mockup: lime accent stays (no separate
sage/stone treatment), watch stats stay, cues strip slots between the
rationale and the stats grid.

**JS** (`magazine.js`, in the Today Card hydration function): populate
`#td-cues` when `payload.cues` is a non-empty array, otherwise leave hidden.
Remove the `OFF_PLAN: 'Plan Check'` byline mapping. Remove the CTA-rendering
branch's special case for OFF_PLAN (the CTA is now only ever populated on
`NO_PLAN`, so the existing `payload.actions.cta` guard handles it).

**CSS cleanup** — delete the OFF_PLAN selectors:

```css
/* DELETE */
#today[data-state="NO_PLAN"] .td-stats,
#today[data-state="OFF_PLAN"] .td-stats{display:none;}
#today[data-state="NO_PLAN"] .td-headline,
#today[data-state="OFF_PLAN"] .td-headline{cursor:default;}
#today[data-state="NO_PLAN"] .td-rationale,
#today[data-state="OFF_PLAN"] .td-rationale{cursor:default;border-left-color:rgba(255,255,255,.2);}
```

Replace with the NO_PLAN-only versions. Also drop the `off-plan` reference in
the magazine.html section comment (line 94) and in the CSS comment on line 119.

### 6. Tests — `tests/integration/web/test_today_api.py`

Rewrite the two US6 tests in place (same file location, no new test file):

| Old name | New name | What it asserts |
|---|---|---|
| `test_off_plan_state_regression` | `test_no_planned_row_treated_as_rest_day` | `state == "REST_DAY"`, headline `"Recovery is the workout"`, `cover_lines` present (the 4-up morning grid), `actions.cta is None`, `cues` is a 3-element list. |
| `test_off_plan_transitions_to_completed_on_bonus_run` | `test_no_planned_row_transitions_to_completed_on_bonus_run` | Same shape as today (active Goal + no planned row + completed bonus run → `state == "COMPLETED"`, `is_bonus == True`), just renamed since the precondition is no longer named OFF_PLAN. |

Update the module docstring header at the top of the file:

- Line 1: `"""Integration tests for GET /api/today across all 6 states."""` →
  `"""Integration tests for GET /api/today across all 5 states."""`
- Line 9: drop the `US6 (OFF_PLAN, P3) — T057–T058` bullet entirely.
- Re-section the `# US6 — OFF_PLAN` divider comment to `# US6 — no-planned-row → REST_DAY`.

Two new tests:

- `test_rest_day_rationale_avoids_run_framing` — seeds an active rest row plus
  a high-readiness snapshot, asserts the rationale does **not** contain
  `"run it"`, `"as written"`, `"target pace"`, or `"easy end"`. Guards against
  the latent bug where `rule_based_morning` was driving REST_DAY rationale.
- `test_rest_day_low_recovery_template` — seeds low HRV + short sleep, asserts
  the rationale text matches the low-recovery branch (substring match on a
  distinctive phrase like `"asking for room"`).

The existing `test_rest_day_state_regression` test (US3) continues to pass
unchanged.

### 7. Spec reconciliation — `specs/007-today-card/`

The shipped spec documents OFF_PLAN as a first-class state. Update inline:

- `spec.md` — FR-003 (state list), FR-007b (OFF_PLAN rationale), FR-012
  (OFF_PLAN CTA), FR-033 (the explicit zero-row regression), US6 (P3 user
  story), acceptance scenarios that mention OFF_PLAN.
- `tasks.md` — rewrite T057 / T058 to describe the new REST_DAY-fallback
  behavior; rename the US6 header to "no-planned-row → REST_DAY"; do not
  renumber other tasks (leave the numbering as-is to keep git history
  readable).
- `contracts/today-api.md` — remove the OFF_PLAN response example, drop it
  from the state enum, add the `cues` field to the REST_DAY response.
- `data-model.md` — remove OFF_PLAN from the state diagram / table.
- `plan.md` — adjust the state-count phrasing ("six states" → "five states").
- `quickstart.md` — adjust the test-matrix table.

The spec edits above happen in the same change as the code edits (one
commit, or one PR with multiple commits — same branch). Optionally run
`speckit.reconcile.run` afterwards as a sweep to catch anything missed by the
hand edits.

### 8. `CLAUDE.md`

In the **Today Card** section:

- "Six states (`PRE_RUN`, `COMPLETED`, `REST_DAY`, `RACE_DAY`, `NO_PLAN`,
  `OFF_PLAN`) per FR-003 precedence" → drop `OFF_PLAN`, say five states.
- Adjust the rationale-source-ladder sentence to clarify REST_DAY uses
  `rule_based_rest` (not the morning/with-snapshot path).

## Files touched

| File | Change |
|---|---|
| `running_coach_ai/web/api/today.py` | Drop OFF_PLAN from `_resolve_state` + dispatch; delete `_build_off_plan`; widen `_build_rest_day` signature; route to `rule_based_rest`; emit `cues` field. |
| `running_coach_ai/coach/today_rationale.py` | Add `rule_based_rest()` with three templates + helper. |
| `running_coach_ai/web/static/magazine.html` | Add `#td-cues` markup; update section comment. |
| `running_coach_ai/web/static/magazine.css` | Add `.td-cues` styles; delete OFF_PLAN selectors and comments. |
| `running_coach_ai/web/static/magazine.js` | Hydrate `#td-cues`; drop `OFF_PLAN` byline mapping. |
| `tests/integration/web/test_today_api.py` | Rewrite US6 tests; add two rest-day rationale tests. |
| `specs/007-today-card/spec.md` + 5 others | Strike OFF_PLAN. |
| `CLAUDE.md` | Update Today Card section to five states. |

## Open questions

None — the visual direction (Option D) is selected; the rest-rationale
template count, content, and trigger thresholds are specified above; the
test plan is concrete.

## Verification

Before claiming complete:

1. `pytest tests/integration/web/test_today_api.py` — all rewritten + new
   tests pass.
2. `ruff check .` — clean.
3. Manual: with a real athlete in the dev environment, delete today's planned
   workout row (or pick a date with no row), load `/app#today`, confirm:
   - State pill / byline says "Recovery Note" not "Plan Check".
   - Headline reads "Recovery is the workout".
   - Cues strip renders below the rationale, above the stats.
   - No "Review my plan" CTA appears.
   - The rationale paragraph reads as rest-appropriate (no "run it" / "as
     written").
