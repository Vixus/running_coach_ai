# Quickstart — Today Card

**Branch**: `007-today-card`
**Audience**: Engineer implementing the feature; reviewer verifying it.

This is the manual verification recipe. Test coverage is automated (FR-033 / FR-034 / FR-035); this doc is the smoke test for "does the user-visible behavior match the spec."

---

## Prereqs

- Local dev environment per `README.md` (Python 3.11, `pip install -r requirements.txt`).
- Local SQLite DB with at least one athlete row (`onboarding_complete=true`, `timezone="America/New_York"` or other valid TZ).
- Flask web app reachable at `http://localhost:8080` after `python web.py`.
- Optional: scheduler running (`python main.py`) to exercise transitions live.

---

## 1. Smoke-test the endpoint shape

```bash
# Verify the endpoint responds for the logged-in athlete
curl -s --cookie cookies.txt http://localhost:8080/api/today | python -m json.tool
```

Expected: a JSON object with top-level keys `state`, `today_iso`, `today_pretty`, `athlete`, `headline`, `rationale`, `modifiers`, `cover_lines`, `actions`. The `state` value must be one of `PRE_RUN`, `COMPLETED`, `REST_DAY`, `RACE_DAY`, `NO_PLAN`.

If you get a 401: log in via the dashboard first to populate the session cookie.

---

## 2. Verify each state manually

For each state, ensure the DB is in the matching configuration, then hit `/api/today` and the dashboard page:

| State | DB setup | Expected response |
|---|---|---|
| `NO_PLAN` | Athlete has no `Goal(active=True)` | `state="NO_PLAN"`, `cover_lines=null`, `actions.cta.label="Pick a race"` |
| `PRE_RUN` | Active Goal + `PlannedWorkout(scheduled_date=today, workout_type="tempo")` + no `CompletedWorkout(date=today)` | `state="PRE_RUN"`, headline shows distance/pace, 4 cover_lines from HealthSnapshot |
| `COMPLETED` | Add a `CompletedWorkout(date=today, planned_workout_id=<the planned id>)` with `coach_analysis="<text>"` | `state="COMPLETED"`, headline shows actuals, cover_lines from CompletedWorkout, rationale.source=`"coach_analysis"` |
| `REST_DAY` | `PlannedWorkout(scheduled_date=today, workout_type="rest")` | `state="REST_DAY"`, headline "Recovery is the workout", cover_lines from HealthSnapshot |
| `RACE_DAY` | `PlannedWorkout(scheduled_date=today, workout_type="race")` + `Goal.race_date=today` | `state="RACE_DAY"`, headline shows race name, cover_lines drill_to=null |
| `REST_DAY` (no planned row) | Active Goal + zero `PlannedWorkout(scheduled_date=today)` rows | `state="REST_DAY"`, headline "Recovery is the workout", 4 cover_lines from HealthSnapshot, `cues` array with Sleep/Fuel/Move, `actions.cta=null` |

To rapidly seed states without touching DB by hand, use the admin "force morning check-in" path or write a tiny seed script per state.

---

## 3. Verify the rationale ladder

For PRE_RUN state, confirm `rationale.source` resolves correctly through the ladder:

1. **morning_checkin path** — Insert a `Notification(kind="morning_checkin", body="...", created_at=now())` for the athlete. Hit `/api/today`. Expect `rationale.source="morning_checkin"` and the first paragraph of the body as `rationale.text`.
2. **placeholder path** — Delete the notification. Set system clock (or athlete-local timezone) so it's before 7:00am local. Hit `/api/today`. Expect `rationale.source="placeholder"` and the static "Coach is checking in soon" text.
3. **rule_based with snapshot** — System clock after 7am. HealthSnapshot present for today. Expect `rationale.source="rule_based"`, text references the athlete name + an observed metric value.
4. **rule_based without snapshot** — Delete today's HealthSnapshot (athlete didn't wear watch overnight). Expect `rationale.source="rule_based"`, text acknowledges "no overnight data" and gives a run-by-feel cue.

Every variant MUST mention the workout type from today's PlannedWorkout (the "why of the run" — FR-008/FR-034e).

---

## 4. Verify the dashboard renders the card

Visit `http://localhost:8080/app` after logging in. Expected:

- The Today Card is the first visible section at the top (above Morning Readiness, Last Run, Calendar, etc.).
- Athlete name appears as the largest type on the card.
- Workout headline + rationale paragraph + 4 cover-line stats are all present.
- The ribbon/rationale border-left/on-watch badge are all in the active persona's `accent_color` (classic = green `#b8ff4f`; switch to Maya for teal `#5a9a92`; switch to Jordan for amber `#c4a040`).

Mobile check: open Chrome DevTools, toggle device mode to "iPhone SE" (375px). Confirm:
- Cover lines stack 2×2 (not 4 across).
- Headline scales to fit on one line.
- All four stats are visible without horizontal scrolling.

---

## 5. Verify interactions

| Action | Expected |
|---|---|
| Tap workout headline (PRE_RUN) | Chat panel opens with input pre-filled `"Tell me about today's workout."` |
| Tap rationale paragraph (PRE_RUN) | Chat panel opens with input pre-filled `"I have a question about today's plan."` |
| Tap HRV cover-line (PRE_RUN) | Page smooth-scrolls to Morning Readiness section; section briefly highlights |
| Tap dimmed area outside chat panel | Chat panel closes, draft text discarded |
| Press Escape with chat panel open | Chat panel closes |
| Press Escape with workout-preview modal open | Workout-preview modal closes (existing behavior preserved) |
| Switch persona via the coach picker | Card refetches `/api/today` within ~100ms; accent color, byline, rationale source switch |
| New `morning_checkin` notification arrives | Card refetches; rationale updates to new body |

---

## 6. Verify failure modes

| Failure | Expected |
|---|---|
| `/api/today` returns 500 | Card renders previously cached payload (from localStorage); cover-line values show a small grey stale-dot icon |
| `/api/today` times out (>5s) | Same as 500 — render cached + stale dots |
| First-ever load + endpoint fails | Card renders a skeleton placeholder; polling continues silently |
| Successful response after a failure | Stale dots clear; ribbon-swap animation runs only if state actually transitioned |

Force a 500 quickly: add `raise Exception("test")` at the top of the endpoint handler, refresh the page, observe the card render the cached payload.

---

## 7. Verify the WebEvent emission

After provoking a state transition (e.g., insert a CompletedWorkout while a PRE_RUN page is open and the 60s poll fires), check the admin Events view:

- A new `today.state_transition` row appears for the athlete.
- `from_state` matches the prior state; `to_state` matches the new state.
- Hit `/api/today` again with no underlying data change — NO new WebEvent row appears (only transitions emit; repeated identical states don't).

---

## 8. Run the automated test suite

```bash
# Backend
pytest tests/integration/web/test_today_api.py -v
pytest tests/unit/test_today_rationale.py -v

# Lint
ruff check running_coach_ai/web/api/today.py
ruff check running_coach_ai/coach/today_rationale.py
ruff check running_coach_ai/coach/personas.py
ruff check running_coach_ai/coach/adapter.py
ruff check running_coach_ai/coach/feedback.py
```

All MUST pass before opening the PR. The integration tests run against in-memory SQLite (FR-035); no Garmin/Claude access is required.

---

## 9. Manual prompt-quality check (subjective)

After updating `MORNING_CHECKIN_PROMPT` and `POST_RUN_FEEDBACK_PROMPT` with the athlete-centered directive:

1. Trigger a real morning check-in (via the admin "force morning check-in" button) for a seeded athlete with at least 2 prior CompletedWorkout rows and a TrainingPlan with `current_week >= 3`.
2. Open the resulting notification and confirm the body:
   - Speaks in first person to the athlete ("I want you doing...", "you've been...").
   - References continuity (recent runs, week number, plan phase, race name).
   - Covers periodization + readiness + execution cue in one paragraph.
   - Is 2-3 sentences, not a 6-sentence essay.
   - Does NOT use third-person language ("athletes often...", "research suggests...").
3. Same check for the post-run path: trigger an activity, wait for the next 30-min poll, read the resulting `coach_analysis` text in the Last Run section.

This is the "feels like a personal coach" check — the rest of the test suite verifies shape and plumbing, but the voice can only be judged by reading the output.

---

## Done criteria

A successful implementation has:

- [x] `/api/today` returns valid responses for all five states, including the no-planned-row → REST_DAY variant (verified via Step 2).
- [x] The card renders correctly on desktop and at 375px mobile (Step 4).
- [x] All six interaction paths work (Step 5).
- [x] Failure modes degrade gracefully (Step 6).
- [x] State transitions emit WebEvent rows (Step 7).
- [x] All automated tests pass (Step 8).
- [x] Updated morning_checkin and post_run prompts produce athlete-centered prose (Step 9).
- [x] No new DB migrations (data-model.md confirms).
- [x] No new Claude calls at request time (verified by code review of `web/api/today.py`).

When all of the above are true, the feature is ready to merge.
