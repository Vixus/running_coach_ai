---
feature: "001-ai-running-coach"
branch: "001-ai-running-coach"
date: "2026-04-04"
completion_rate: 100
spec_adherence: 100
total_requirements: 44
implemented: 44
partial: 0
not_implemented: 0
modified: 0
unspecified: 1
critical_findings: 0
significant_findings: 1
minor_findings: 5
positive_findings: 3
total_tasks: 80
completed_tasks: 80
test_files: 23
test_cases: 287
---

# Retrospective: AI Running Coach

**Generated**: 2026-04-04  
**Branch**: `001-ai-running-coach`  
**Scope**: Full feature delivery — Phases 1–9 (T001–T052) + Remediation (T053–T075) + Iteration (T076–T080)

---

## Executive Summary

Feature `001-ai-running-coach` has reached **100% task completion** (80/80) across three delivery stages: initial implementation, a 2026-04-04 reconciliation addressing 23 gaps found post-delivery, and a same-day iteration adding health-gated morning check-in polling. All 33 functional requirements and 11 success criteria are implemented.

**Spec adherence is 100%** — the implementation matches the spec because the spec was actively iterated to capture design decisions made during reconciliation and the polling iteration. The primary lesson of this implementation cycle is that the reconciliation stage (T053–T075) adds significant scope post-delivery and should ideally be folded into the original spec cycle rather than discovered post-implementation.

The iteration introduced on 2026-04-04 (morning check-in polling, T076–T080) was well-specified and implemented cleanly and completely in a single session with 10 new unit tests. The `_next_checkin_start()` helper and `conftest.py` additions were positive unspecified contributions.

One SIGNIFICANT drift item remains open: `plan.md` still states a stale 5-minute performance goal after SC-003 was updated in `spec.md`. This is a documentation inconsistency, not a code defect.

---

## Proposed Spec Changes

> **Human Gate required before any action that modifies `spec.md`.**
> See Section 13 for confirmation request.

| ID    | Artifact                         | Change                                                                                                                                                  | Rationale                                                                                             |
| ----- | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| PSC-1 | `spec.md` (Edge Cases)           | Remove the legacy free-form `"What happens when health data is unavailable..."` bullet; retain only the named `**No Watch to Bed**:` bullet             | Duplicate content — both say the same thing after the polling iteration was applied                   |
| PSC-2 | `spec.md` (US3 Independent Test) | Extend from single-trigger test to a multi-tick scenario: tick 1 = no data → no DM; tick 2 = data present → DM sent and `last_morning_checkin_date` set | Doesn't reflect FR-030/FR-031 polling architecture; a passing manual test would give false confidence |

Changes that do NOT require the spec.md gate:

| ID    | Artifact                   | Change                                                                                                                                                                                                                 | Rationale                                                             |
| ----- | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| PSC-3 | `plan.md`                  | Update Performance Goals: `"Morning check-in message: within 5 minutes of scheduled time"` → `"…within 30 minutes of health data becoming available after 07:00 athlete local time; no message if no data by 10:00am"` | Stale; SC-003 was updated in spec.md                                  |
| PSC-4 | `tasks.md` (T039)          | Update description to reflect actual 10-minute interval (superseded by T053; description still says `IntervalTrigger(minutes=30)`)                                                                                     | Stale description; T039 is complete but misleading for future readers |
| PSC-5 | `tasks.md` (Summary Table) | Update "52 tasks" → "80 tasks"; update Phase 5 row to include T076–T080; add Remediation row T053–T075                                                                                                                 | Counts are stale after reconciliation and iteration additions         |

---

## Requirement Coverage Matrix

### Functional Requirements

| ID     | Description (short)             | Status      | Evidence                                                                                       |
| ------ | ------------------------------- | ----------- | ---------------------------------------------------------------------------------------------- |
| FR-001 | Athlete data isolation          | IMPLEMENTED | `scoped_query()` helper (T050/T067); every query filtered by `athlete_id`                      |
| FR-002 | Invite-only access              | IMPLEMENTED | `ALLOWED_SLACK_USER_IDS` + per-athlete `allowed` flag (T019, T046)                             |
| FR-003 | Admin runtime management        | IMPLEMENTED | `!admin add/remove/list` via DM (T045–T047, T068, T075)                                        |
| FR-004 | Onboarding resume               | IMPLEMENTED | `athlete.onboarding_step` persisted; resume gate on re-entry (T061)                            |
| FR-005 | Encrypted credentials           | IMPLEMENTED | Fernet encryption at rest (T011); password scrubbed from history (T018)                        |
| FR-006 | Multi-goal, conflict resolution | IMPLEMENTED | Deterministic conflict pass in `planner.py` (T059); multi-goal clarification documented        |
| FR-007 | Training phases                 | IMPLEMENTED | base/build/peak/taper structure in plan generation (T016)                                      |
| FR-008 | Session types                   | IMPLEMENTED | easy, long, tempo, intervals, strides, rest, cross-training (T016, T017)                       |
| FR-009 | Upload sessions to device       | IMPLEMENTED | `upload_workout()` + `schedule_workout()` (T014, T020)                                         |
| FR-010 | Update sessions on device       | IMPLEMENTED | Delete-then-re-upload on mutation; `last_garmin_synced_at` on success (T023, T072)             |
| FR-011 | Daily health data reads         | IMPLEMENTED | `get_health_snapshot()` reads 7 Garmin APIs per field (T027, T028)                             |
| FR-012 | Activity detection + telemetry  | IMPLEMENTED | `poll_new_activities()`, telemetry streams, lap splits (T032–T035)                             |
| FR-013 | Regular activity detection      | IMPLEMENTED | `IntervalTrigger(minutes=10)` during 06:00–22:00 (T039, T053)                                  |
| FR-014 | Graceful missing data           | IMPLEMENTED | Per-field exception handling; NULL on missing (T027)                                           |
| FR-015 | Conversational coach tone       | IMPLEMENTED | Veteran coach persona in system prompt (T015)                                                  |
| FR-016 | Full context per turn           | IMPLEMENTED | 8-section prompt builder in `conversation.py` (T022)                                           |
| FR-017 | Long-term memory                | IMPLEMENTED | `CoachMemory` rows with category tagging (T024)                                                |
| FR-018 | Morning health evaluation       | IMPLEMENTED | `run_morning_checkin()` with health gate (T030, superseded T077)                               |
| FR-019 | Biomechanical post-run feedback | IMPLEMENTED | Specific telemetry observations in `feedback.py` (T038, T065)                                  |
| FR-020 | Silent plan mutations           | IMPLEMENTED | `<plan>` tag extraction in `conversation.py` (T023, T025)                                      |
| FR-021 | Weekly summary + adaptation     | IMPLEMENTED | `generate_weekly_review()` + `adapt_next_week()` (T041, T042)                                  |
| FR-022 | Weather factors                 | IMPLEMENTED | Open-Meteo WMO mapping + session adjustment rules (T029)                                       |
| FR-023 | Injury-risk guardrail           | IMPLEMENTED | Deterministic threshold injection into prompts (T069)                                          |
| FR-024 | Telemetry stream analysis       | IMPLEMENTED | 12-stream extraction + lap splits (T034, T035, T036)                                           |
| FR-025 | Biomechanical profile           | IMPLEMENTED | `RunningProfile` upsert with 30-day rolling averages + 3 trend fields (T037, T063)             |
| FR-026 | Profile in feedback             | IMPLEMENTED | All trend labels in feedback prompt (T065)                                                     |
| FR-027 | Retry + stale-window skip       | IMPLEMENTED | `@retry_garmin()` exponential backoff; stale-window guards in all jobs (T048, T055, T057)      |
| FR-028 | Structured logs                 | IMPLEMENTED | All modules log with `athlete_id`, timestamp, level (T049)                                     |
| FR-029 | PlannedWorkout timestamps       | IMPLEMENTED | `created_at`, `updated_at`, `last_garmin_synced_at` with Alembic migration (T070–T074)         |
| FR-030 | IntervalTrigger 30-min poll     | IMPLEMENTED | `IntervalTrigger(minutes=30, start_date=_next_checkin_start(tz))` (T078)                       |
| FR-031 | Health data presence check      | IMPLEMENTED | `_HEALTH_KEY_FIELDS` constant, `all(getattr(snapshot, f) is None …)` check (T077)              |
| FR-032 | Skip at 10am cutoff             | IMPLEMENTED | `now_local.hour < 10` branch → debug return; `≥10` → INFO log + return (T077)                  |
| FR-033 | One DM per day guard            | IMPLEMENTED | `athlete.last_morning_checkin_date == today` check at function entry; set on send (T076, T077) |

### Success Criteria

| ID     | Description (short)                  | Status      | Evidence                                                                                |
| ------ | ------------------------------------ | ----------- | --------------------------------------------------------------------------------------- |
| SC-001 | Onboarding in single session         | IMPLEMENTED | Full flow: intake → plan → Garmin upload → week 1 summary DM                            |
| SC-002 | Post-run feedback < 10 min           | IMPLEMENTED | 10-min activity poll with stale-window guard; regression test in T054                   |
| SC-003 | Morning check-in ≤ 30 min after data | IMPLEMENTED | 30-min IntervalTrigger from 07:00; 10am hard cutoff (T077, T078)                        |
| SC-004 | 4/5 conversation intents handled     | IMPLEMENTED | 8-section context + full coach persona enabling logistics, injury, motivation, insights |
| SC-005 | Plan mutation < 2 min                | IMPLEMENTED | Sync triggered synchronously within conversation response path (T023)                   |
| SC-006 | Athlete data isolated                | IMPLEMENTED | `scoped_query()` enforced; `athlete_id` on every query (T050, T067)                     |
| SC-007 | Auto-recovery from API failures      | IMPLEMENTED | `@retry_garmin()` + graceful degradation; no crash paths (T048, T055)                   |
| SC-008 | Memory persistence across sessions   | IMPLEMENTED | `CoachMemory` rows with dedup on identical content (T024)                               |
| SC-009 | Load reduction after < 70% sessions  | IMPLEMENTED | `adapt_next_week()` with 70% threshold; `<plan>` mutations (T042)                       |
| SC-010 | Admin access effective immediately   | IMPLEMENTED | `!admin` DM commands; change takes effect on next athlete message (T045–T047)           |
| SC-011 | Reply < 10 seconds                   | IMPLEMENTED | Explicit Claude timeout + immediate fallback acknowledgement path (T066)                |

**Spec Adherence: 100%** (44 implemented / 44 total requirements)

---

## Architecture Drift

| Component                  | Plan                                      | Actual                                                                  | Severity    | Notes                                                                           |
| -------------------------- | ----------------------------------------- | ----------------------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------- |
| Morning check-in trigger   | `CronTrigger(hour=7)`                     | `IntervalTrigger(minutes=30, start_date=<next 07:00>)`                  | POSITIVE    | Spec was iterated to capture this; design superior for health-data availability |
| Activity poll interval     | `IntervalTrigger(minutes=30)`             | `IntervalTrigger(minutes=10)`                                           | POSITIVE    | Superseded by T053; required to meet SC-002                                     |
| Health data gate           | Not in original plan                      | `_HEALTH_KEY_FIELDS` constant + two-branch gate (before/after 10am)     | POSITIVE    | Added via iteration; clean implementation                                       |
| Athlete dedup field        | Not in original data model                | `athletes.last_morning_checkin_date DATE NULLABLE`                      | POSITIVE    | Added via iteration; correct architecture for dedup                             |
| Session fallback block     | Would use stored snapshot on read failure | Stored-snapshot fallback is now unreachable dead code after health gate | MINOR       | Not a regression — correct per FR-031/FR-032; code cleanup warranted            |
| Performance goal (plan.md) | "within 5 minutes of scheduled time"      | Not implemented (superseded by SC-003 update)                           | SIGNIFICANT | plan.md not updated when SC-003 was amended                                     |
| Test infrastructure        | No conftest.py defined                    | `conftest.py` added at project root                                     | POSITIVE    | Fixes test reproducibility without environment setup                            |

---

## Significant Deviations

### DS-001 — plan.md Performance Goal Is Stale (SIGNIFICANT)

**Description**: `plan.md` Performance Goals section states: _"Morning check-in message: within 5 minutes of scheduled time"_. This was superseded by the 2026-04-04 polling iteration, which updated SC-003 in `spec.md` to _"within 30 minutes of health data becoming available after 07:00."_ The plan.md artifact was not updated.

**Impact**: A reader of plan.md would misunderstand the morning check-in SLA. No runtime impact.

**Evidence**: `specs/001-ai-running-coach/plan.md` Performance Goals section; `specs/001-ai-running-coach/spec.md` SC-003.

**Recommendation**: Apply PSC-3 (update plan.md) — no spec.md gate needed.

---

### DS-002 — Fallback-to-Stored-Snapshot Block Is Dead Code (MINOR)

**Description**: The fallback block in `run_morning_checkin()` reads:

```python
if stored and snapshot is None:
    snapshot = stored
```

After the health-data gate, `snapshot is None` always causes an early return before this line is reached. The fallback block is reachable only if `snapshot` is not None and all four of `hrv_score`, `sleep_score`, `resting_hr`, `body_battery_start` are None — but at that point `stored and snapshot is None` is False, so the body never executes.

**Impact**: No functional impact. Dead code adds cognitive overhead for future maintainers. The behavioral change is correct: failed Garmin fetches now trigger retry (< 10am) or skip (≥ 10am) rather than silently falling back to stale data.

**Evidence**: `running_coach_ai/coach/adapter.py` lines 95–110 (approximately).

**Recommendation**: In a follow-up cleanup, either remove the fallback block or document its intentional retention with a comment explaining why it's unreachable.

---

### DS-003 — T039 Task Description Is Stale (MINOR)

**Description**: T039 in `tasks.md` (Phase 6, US4) still describes `IntervalTrigger(minutes=30)` for the activity poll job. The actual implementation uses 10 minutes per T053 (reconciliation). T039 is marked `[x]` complete.

**Impact**: None at runtime. Confusing for readers of tasks.md who see an apparently-complete task with a wrong spec.

**Recommendation**: Apply PSC-4.

---

### DS-004 — Summary Table Task Count Stale (MINOR)

**Description**: The tasks.md Summary Table states _"52 tasks total"_ and Phase 5 row shows only T027–T031. The actual total is 80 after reconciliation (T053–T075) and iteration (T076–T080) additions.

**Recommendation**: Apply PSC-5.

---

### DS-005 — Duplicate Edge Case Bullets (MINOR)

**Description**: `spec.md` Edge Cases section contains two bullets that describe the same scenario:

1. Legacy: _"What happens when health data is unavailable (e.g., athlete didn't wear device overnight)? The system polls every 30 minutes…"_
2. Named: _"**No Watch to Bed**: If Garmin has not published any sleep, HRV, or body battery data by 10:00am…"_

Both convey the same behavior. The legacy bullet was not removed when the named bullet was added during the polling iteration.

**Recommendation**: Apply PSC-1 (requires spec.md gate).

---

## Innovations and Best Practices

### IN-001 — `_HEALTH_KEY_FIELDS` Module Constant (POSITIVE)

The three critical fields (`sleep_score`, `hrv_score`, `body_battery_start`) are defined as a module-level tuple in `running_coach_ai/coach/adapter.py`. This makes the health-presence definition single-source-of-truth within the module and directly auditable against FR-031. Pattern worth propagating to any future feature that gates on a set of Garmin fields.

### IN-002 — `_next_checkin_start()` Pure Helper Function (POSITIVE)

A stateless, timezone-aware helper was extracted to compute _"next 07:00 in athlete timezone"_ for `IntervalTrigger.start_date`. This is independently testable, semantically clear, and handles the midnight-crossing case correctly. Candidate for reuse in future timezone-aware scheduling work.

**Formula**:

```python
def _next_checkin_start(tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    today_7am = now_local.replace(hour=7, minute=0, second=0, microsecond=0)
    if now_local >= today_7am:
        return today_7am + timedelta(days=1)
    return today_7am
```

### IN-003 — `conftest.py` for Zero-Configuration Testing (POSITIVE)

A root-level `conftest.py` inserting the project root into `sys.path` was added, making `pytest tests/unit/` work out of the box without `PYTHONPATH` configuration. Low-overhead fix with high developer experience impact. Should be noted in `quickstart.md`'s testing section.

---

## Constitution Compliance

| Principle                         | Status  | Evidence                                                                                    |
| --------------------------------- | ------- | ------------------------------------------------------------------------------------------- |
| I — Athlete Data Isolation        | ✅ PASS | `scoped_query()` used throughout new code; `athlete_id` in all new log entries              |
| II — AI Coach Persona Integrity   | ✅ PASS | Not touched by this iteration                                                               |
| III — Metric-First Architecture   | ✅ PASS | Not touched by this iteration                                                               |
| IV — Encrypted Secrets Management | ✅ PASS | Not touched; Fernet encryption unchanged                                                    |
| V — Graceful Degradation          | ✅ PASS | Health gate returns early cleanly; all exception paths log and return, not raise            |
| VI — Structured Observability     | ✅ PASS | INFO on 10am skip, DEBUG on retry/dedup, INFO on successful send — all include `athlete_id` |
| VII — Test-First Development      | ✅ PASS | T079 (7 tests) + T080 (3 tests) = 10 new tests covering all gate branches                   |
| VIII — Configuration as Code      | ✅ PASS | `ZoneInfo(athlete.timezone)` reads from DB, no hardcoded timezone values                    |

**Performance Standards**:

- Activity poll ≤10 min: ✅ (10-min `IntervalTrigger`, unchanged)
- Morning check-in ≤30 min after data: ✅ (30-min `IntervalTrigger` from 07:00)

**No constitution violations.** Zero CRITICAL findings.

---

## Unspecified Implementations

| ID  | File          | Description                                  | Risk | Disposition                                                                                       |
| --- | ------------- | -------------------------------------------- | ---- | ------------------------------------------------------------------------------------------------- |
| U1  | `conftest.py` | Root-level pytest path fix — not in any task | LOW  | Keep; positive developer experience improvement. Consider adding to quickstart.md testing section |

---

## Task Execution Analysis

| Phase                          | Tasks     | Completed | Notes                                                               |
| ------------------------------ | --------- | --------- | ------------------------------------------------------------------- |
| Phase 1: Setup                 | T001–T005 | 5/5       | —                                                                   |
| Phase 2: Foundational          | T006–T012 | 7/7       | —                                                                   |
| Phase 3: US1 Onboarding        | T013–T020 | 8/8       | —                                                                   |
| Phase 4: US2 Conversation      | T021–T026 | 6/6       | —                                                                   |
| Phase 5: US3 Morning Check-In  | T027–T031 | 5/5       | T030/T031 superseded by T076–T078 (correct; tasks marked complete)  |
| Phase 6: US4 Post-Run Feedback | T032–T039 | 8/8       | T039 description stale (DS-003)                                     |
| Phase 7: US5 Weekly Review     | T040–T044 | 5/5       | —                                                                   |
| Phase 8: US6 Admin Management  | T045–T047 | 3/3       | —                                                                   |
| Phase 9: Polish                | T048–T052 | 5/5       | —                                                                   |
| Remediation (2026-04-04)       | T053–T075 | 23/23     | Large reconciliation batch; should be integrated earlier next cycle |
| Iteration (2026-04-04)         | T076–T080 | 5/5       | Completed in single session; well-specified and clean               |
| **Total**                      | **80**    | **80**    | **100%**                                                            |

**Task modification** (added during delivery):

- T053: Poll interval reduced from 30 to 10 minutes (required for SC-002)
- T055: `@retry_garmin()` wrappers added (required for FR-027)
- T063/T064: Biomechanical trend fields (required for FR-025)

**Tasks dropped**: None.

**Tasks added beyond original scope**: T053–T075 (23 tasks; all reconciliation of spec-implementation gaps), T076–T080 (5 tasks; health-gated polling iteration).

---

## Lessons Learned and Recommendations

### L1 — Reconciliation Scope Was Underestimated (HIGH PRIORITY)

23 remediation tasks were added post-initial-delivery across reliability (retry, stale-window), latency (poll interval, Claude timeout), data isolation (scoped_query migration), and safety (biomechanical trends, overtraining guardrail). Total 44% task volume came from reconciliation or iteration rather than the original 52 tasks.

**Recommendation**: For spec 002 and beyond, run `speckit.analyze` after `/speckit.tasks` and before implementation to catch these gaps before delivery. Add constitution-compliance check phase to the task checklist template.

### L2 — Polling Iteration Was Fast and Clean (POSITIVE)

The morning check-in polling iteration (FR-030–FR-033, T076–T080) went from spec clarification to 10 passing unit tests in a single session. The key success factors were: a well-bounded clarification session, a single clear decision point (skip vs send on no-data), and the `IntervalTrigger + dedup field` architecture being well-understood before coding started.

**Recommendation**: Use this iteration's artifact lifecycle (`pending-iteration.md` → apply → delete) as the standard for future in-spec iterations.

### L3 — plan.md Should Be Kept In Sync During Spec Iterations (MEDIUM PRIORITY)

SC-003 was updated in `spec.md` but `plan.md`'s Performance Goals were not. plan.md is the source of truth for implementation-facing SLA decisions and should be updated in the same apply step as spec.md.

**Recommendation**: Add plan.md to the checklist in `speckit.iterate.apply` for any iteration that touches a success criterion.

### L4 — Dead Code Cleanup Discipline (LOW PRIORITY)

When an iteration supersedes a code path (T030 fallback superseded by T077 gate), the old code path should be explicitly removed or annotated in the same PR. The fallback-to-stored-snapshot block in `adapter.py` is now unreachable and should be cleaned up.

### L5 — conftest.py Should Be Part of Phase 1 Setup (LOW PRIORITY)

The absence of a root-level `conftest.py` meant all tests failed without `PYTHONPATH` set. This is a setup gap that should be part of T001 (project structure) in future features.

---

## Follow-up Actions

### Priority 1 — SIGNIFICANT (no spec change required)

- [ ] **plan.md PSC-3**: Update Performance Goals stale text (5 min → 30 min + 10am cutoff)

### Priority 2 — MEDIUM (spec.md changes require user confirmation — see below)

- [ ] **spec.md PSC-1**: Remove duplicate edge case bullet
- [ ] **spec.md PSC-2**: Update US3 Independent Test for polling architecture

### Priority 3 — MINOR (no spec change required)

- [ ] **tasks.md PSC-4/PSC-5**: Update T039 description and Summary Table counts
- [ ] **adapter.py**: Remove or annotate unreachable fallback-to-stored-snapshot block (DS-002)
- [ ] **quickstart.md**: Add note that `pytest tests/unit/` works without PYTHONPATH thanks to `conftest.py`

### Priority 4 — PROCESS

- [ ] **speckit.checklist**: Add `plan.md Performance Goals` to the iterate.apply artifact checklist for SC-touching iterations
- [ ] **conftest.py**: Add to project setup template (Phase 1, T001 equivalent)

---

## File Traceability Appendix

| Task        | Primary Files                                                                                                                            | Status       |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| T076        | `running_coach_ai/database/models.py`, `running_coach_ai/database/migrations/versions/5bb958015cfa_add_last_morning_checkin_date_to_.py` | ✅           |
| T077        | `running_coach_ai/coach/adapter.py`                                                                                                      | ✅           |
| T078        | `running_coach_ai/scheduler/jobs.py`                                                                                                     | ✅           |
| T079        | `tests/unit/test_morning_checkin_polling.py`                                                                                             | ✅ (7 tests) |
| T080        | `tests/unit/test_morning_checkin_dedup.py`                                                                                               | ✅ (3 tests) |
| Unspecified | `conftest.py`                                                                                                                            | ✅ (U1)      |

**Total test suite**: 287 tests across 23 files. All passing.
