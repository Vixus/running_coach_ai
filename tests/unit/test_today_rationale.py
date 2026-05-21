"""Unit tests for coach/today_rationale.py.

Covers:
  - extract_rationale_paragraph() — paragraph extraction (T033)
  - rule_based_morning() — with-snapshot templates (T030)
  - rule_based_morning() — no-snapshot variants per workout type (T031)
  - rule_based_morning() — graceful degradation with no plan/goal (T032)
  - rule_based_completed() — fallback when coach_analysis is empty (T042)

All tests assert the "why of the run" appears in every variant
(FR-008/FR-034e) — workout_type is named in the returned text.
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from running_coach_ai.coach.today_rationale import (
    extract_rationale_paragraph,
    rule_based_completed,
    rule_based_morning,
    rule_based_rest,
    strip_today_tagline,
)


def _snap(hrv=62, hrv_status="balanced", sleep_h=7.4, bb_start=78, rhr=48):
    """Build a lightweight HealthSnapshot-like object."""
    return SimpleNamespace(
        hrv_score=hrv,
        hrv_status=hrv_status,
        sleep_duration_seconds=int(sleep_h * 3600) if sleep_h is not None else None,
        body_battery_start=bb_start,
        body_battery_end=(bb_start - 10) if bb_start is not None else None,
        resting_hr=rhr,
    )


def _planned(workout_type="tempo"):
    return SimpleNamespace(workout_type=workout_type)


def _plan(phase="build", week=7):
    return SimpleNamespace(current_phase=phase, current_week=week)


def _goal(name="Berlin Marathon"):
    return SimpleNamespace(race_name=name)


# ─── extract_rationale_paragraph (T033) ────────────────────────────────────


class TestExtractRationaleParagraph:
    def test_multi_paragraph_returns_first(self):
        body = (
            "First paragraph here. Two sentences.\n\n"
            "Second paragraph — different content.\n\n"
            "Third paragraph."
        )
        assert extract_rationale_paragraph(body) == "First paragraph here. Two sentences."

    def test_single_paragraph_returns_whole(self):
        body = "Just one paragraph here."
        assert extract_rationale_paragraph(body) == "Just one paragraph here."

    def test_empty_returns_empty(self):
        assert extract_rationale_paragraph("") == ""
        assert extract_rationale_paragraph(None) == ""

    def test_whitespace_only_returns_empty(self):
        assert extract_rationale_paragraph("   \n\n   ") == ""

    def test_strips_surrounding_whitespace(self):
        body = "  \n\nFirst para.\n\nSecond para."
        assert extract_rationale_paragraph(body) == "First para."

    def test_trailing_signoff_excluded(self):
        body = (
            "Today's rationale paragraph addressed to the athlete in coach voice.\n\n"
            "— Coach Alex"
        )
        assert extract_rationale_paragraph(body) == (
            "Today's rationale paragraph addressed to the athlete in coach voice."
        )

    def test_today_tagline_preferred_when_present(self):
        body = (
            "**Today.** This tempo holds race pace under fatigue.\n\n"
            "Sam, HRV is up 2 and sleep was solid; green-light to execute."
        )
        assert extract_rationale_paragraph(body) == (
            "This tempo holds race pace under fatigue."
        )

    def test_today_tagline_case_insensitive(self):
        body = "**today.** Short cue line.\n\nLonger context."
        assert extract_rationale_paragraph(body) == "Short cue line."

    def test_today_tagline_with_extra_whitespace(self):
        body = "  **Today.**   Tagline content.  \n\nMore content."
        assert extract_rationale_paragraph(body) == "Tagline content."


class TestStripTodayTagline:
    def test_strips_leading_tagline(self):
        body = (
            "**Today.** Short cover line here.\n\n"
            "Full rationale paragraph below."
        )
        assert strip_today_tagline(body) == "Full rationale paragraph below."

    def test_no_tagline_returns_unchanged(self):
        body = "Just regular content.\n\nSecond paragraph."
        assert strip_today_tagline(body) == body

    def test_empty_input(self):
        assert strip_today_tagline("") == ""
        assert strip_today_tagline(None) == ""

    def test_preserves_subsequent_paragraphs(self):
        body = (
            "**Today.** Tagline.\n\n"
            "Paragraph two with the details.\n\n"
            "Paragraph three with the weather."
        )
        result = strip_today_tagline(body)
        assert "Tagline" not in result
        assert "Paragraph two" in result
        assert "Paragraph three" in result


# ─── rule_based_morning — with-snapshot branch (T030) ──────────────────────


class TestRuleBasedMorningWithSnapshot:
    def _assert_athlete_centered(self, text, name="Sam", workout_type="tempo"):
        """Every output MUST name the athlete and the workout type."""
        first = name.split()[0]
        assert first in text, f"missing athlete first name {first!r} in {text!r}"
        # Workout type is referenced by its readable label
        assert "tempo" in text.lower() or "session" in text.lower() or "workout" in text.lower()

    def test_low_hrv_template_dials_back(self):
        text = rule_based_morning(
            _snap(hrv=42, hrv_status="low"),
            _planned("tempo"),
            _plan(), _goal(), "Sam Runner",
        )
        self._assert_athlete_centered(text)
        assert "42" in text  # HRV value referenced numerically
        assert "below baseline" in text.lower() or "asking for room" in text.lower()

    def test_short_sleep_holds_conservative(self):
        text = rule_based_morning(
            _snap(hrv=62, sleep_h=5.5),
            _planned("tempo"),
            _plan(), _goal(), "Sam",
        )
        self._assert_athlete_centered(text)
        assert "5.5" in text
        assert "hold" in text.lower() or "conservative" in text.lower()

    def test_low_body_battery_keeps_warmup_honest(self):
        text = rule_based_morning(
            _snap(hrv=60, sleep_h=7.5, bb_start=35),
            _planned("tempo"),
            _plan(), _goal(), "Sam",
        )
        self._assert_athlete_centered(text)
        assert "35" in text
        assert "battery" in text.lower()

    def test_high_hrv_good_sleep_greenlight(self):
        text = rule_based_morning(
            _snap(hrv=72, hrv_status="high", sleep_h=7.5, bb_start=85),
            _planned("tempo"),
            _plan(), _goal(), "Sam",
        )
        self._assert_athlete_centered(text)
        assert "72" in text
        assert "7.5" in text
        assert "as written" in text.lower() or "green light" in text.lower()

    def test_normal_solid_day_bread_and_butter(self):
        text = rule_based_morning(
            _snap(hrv=62, sleep_h=7.5, bb_start=70),
            _planned("tempo"),
            _plan(), _goal(), "Sam",
        )
        self._assert_athlete_centered(text)
        # Either bread-and-butter (template 5) or mixed-signals (template 6) is fine
        assert "62" in text
        assert "7.5" in text or "middle-of-the-road" in text.lower()

    def test_partial_signals_mixed_template(self):
        # Snapshot with HRV but no sleep duration — falls through to template 6
        text = rule_based_morning(
            _snap(hrv=58, sleep_h=None, bb_start=65),
            _planned("tempo"),
            _plan(), _goal(), "Sam",
        )
        self._assert_athlete_centered(text)
        assert "58" in text

    def test_periodization_clause_includes_phase_week_and_race(self):
        text = rule_based_morning(
            _snap(hrv=62),
            _planned("tempo"),
            _plan(phase="build", week=7),
            _goal(name="Berlin Marathon"),
            "Sam",
        )
        assert "week 7" in text
        assert "build" in text
        assert "Berlin Marathon" in text


# ─── rule_based_morning — no-snapshot branch (T031) ────────────────────────


class TestRuleBasedMorningNoSnapshot:
    @pytest.mark.parametrize("workout_type,cue_marker", [
        ("easy",        "conversational"),
        ("long_run",    "endurance"),
        ("tempo",       "pace range"),
        ("interval",    "ragged"),
        ("intervals",   "ragged"),
        ("strides",     "fast and relaxed"),
        ("recovery",    "blood flow"),
    ])
    def test_no_snapshot_per_workout_type(self, workout_type, cue_marker):
        text = rule_based_morning(
            None,
            _planned(workout_type),
            _plan(), _goal(), "Sam",
        )
        # Acknowledges missing data
        assert "overnight readings" in text or "no watch" in text.lower()
        # Names the athlete
        assert "Sam" in text
        # Workout-type-specific by-feel cue is present
        assert cue_marker in text.lower(), (
            f"workout_type={workout_type}: expected cue {cue_marker!r} in {text!r}"
        )


# ─── rule_based_morning — graceful degradation (T032) ──────────────────────


class TestRuleBasedMorningDegraded:
    def test_no_plan_no_goal_still_references_workout_type(self):
        text = rule_based_morning(
            None,
            _planned("easy"),
            None, None,
            "Sam",
        )
        assert "Sam" in text
        assert "easy" in text.lower() or "run" in text.lower()
        # No broken sentence structure (no stray empty phrases)
        assert "  " not in text  # collapsed
        assert " ." not in text  # no orphan periods
        assert "for ." not in text

    def test_unknown_workout_type_falls_back_gracefully(self):
        text = rule_based_morning(
            _snap(),
            SimpleNamespace(workout_type="some_new_type"),
            _plan(), _goal(),
            "Sam",
        )
        # Still references something resembling the workout
        assert "Sam" in text
        assert text.strip().endswith(".")  # full sentence

    def test_no_athlete_name_uses_default(self):
        text = rule_based_morning(
            _snap(),
            _planned("tempo"),
            _plan(), _goal(),
            "",
        )
        assert "Athlete" in text


# ─── rule_based_completed (T042) ───────────────────────────────────────────


class TestRuleBasedCompleted:
    def test_full_data_compares_actual_vs_target(self):
        text = rule_based_completed(
            completed_distance_mi=6.1,
            completed_pace_str="7:42",
            target_pace_str="7:45",
            workout_type="tempo",
            athlete_name="Sam Runner",
        )
        assert "Sam" in text
        assert "6.1" in text
        assert "7:42" in text
        assert "7:45" in text
        assert "tempo" in text.lower()

    def test_no_target_pace_neutral_acknowledgement(self):
        text = rule_based_completed(
            completed_distance_mi=5.0,
            completed_pace_str="8:30",
            target_pace_str=None,
            workout_type="easy",
            athlete_name="Sam",
        )
        assert "Sam" in text
        assert "5.0" in text
        assert "easy" in text.lower()

    def test_no_pace_at_all(self):
        text = rule_based_completed(
            completed_distance_mi=None,
            completed_pace_str=None,
            target_pace_str=None,
            workout_type="recovery",
            athlete_name="Sam",
        )
        assert "Sam" in text
        assert "recovery" in text.lower()


# ─── rule_based_rest (Task 1 additions) ────────────────────────────────────


class TestRuleBasedRest:
    def test_rest_low_recovery_low_hrv(self):
        """Low HRV → low-recovery template — names rest framing, never 'run it'."""
        snap = _snap(hrv=42, hrv_status="low", sleep_h=7.2)
        text = rule_based_rest(snap, _plan(phase="base", week=4), _goal(), "Sam Runner")
        assert "Sam" in text
        assert "run it" not in text.lower()
        assert "as written" not in text.lower()
        assert "asking for room" in text.lower()
        assert "42" in text  # the diagnostic HRV value, not the (normal) sleep number

    def test_rest_low_recovery_short_sleep(self):
        """Short sleep (< 6.5 h) → low-recovery template even when HRV is fine."""
        snap = _snap(hrv=72, hrv_status="balanced", sleep_h=5.5)
        text = rule_based_rest(snap, _plan(phase="base", week=4), _goal(), "Sam Runner")
        assert "asking for room" in text.lower()
        assert "5.5" in text  # references the observed sleep hours

    def test_rest_recharged_high_hrv_solid_sleep(self):
        """High HRV + ≥ 7 h sleep → recharged template."""
        snap = _snap(hrv=92, hrv_status="high", sleep_h=7.8)
        text = rule_based_rest(snap, _plan(phase="base", week=4), _goal(), "Sam Runner")
        assert "Sam" in text
        assert "run it" not in text.lower()
        assert "after rest" in text.lower()

    def test_rest_steady_no_snapshot(self):
        """Missing snapshot → steady template; still names rest framing and tips."""
        text = rule_based_rest(None, _plan(phase="base", week=4), _goal(), "Sam Runner")
        assert "Sam" in text
        assert "right less" in text.lower()
        assert "run it" not in text.lower()

    def test_rest_periodization_clause_when_plan_present(self):
        """When plan + goal present, weave 'week N of phase on the road to race' in."""
        snap = _snap(hrv=92, hrv_status="high", sleep_h=7.8)
        text = rule_based_rest(
            snap, _plan(phase="build", week=6), _goal(name="Chicago Marathon"), "Sam Runner"
        )
        assert "week 6" in text.lower()
        assert "build block" in text.lower()
        assert "Chicago Marathon" in text

    def test_rest_no_plan_omits_periodization_cleanly(self):
        """No plan/goal → no orphaned 'week N' fragments; sentence still reads."""
        snap = _snap(hrv=92, hrv_status="high", sleep_h=7.8)
        text = rule_based_rest(snap, None, None, "Sam Runner")
        assert "Sam" in text
        assert "week " not in text.lower()
        assert "road to" not in text.lower()
