"""Unit tests for trigger detection + priority resolver (T005, T023)."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from running_coach_ai.coach import story
from running_coach_ai.coach.story import (
    INTERVIEW_TRIGGERS,
    TRIGGER_PRIORITY,
    detect_pr_set,
)


# ---------------------------------------------------------------------------
# T005 — priority order
# ---------------------------------------------------------------------------


def test_trigger_priority_order():
    assert TRIGGER_PRIORITY["race_complete"] > TRIGGER_PRIORITY["race_upcoming"]
    assert TRIGGER_PRIORITY["race_upcoming"] > TRIGGER_PRIORITY["pr_set"]
    assert TRIGGER_PRIORITY["pr_set"] > TRIGGER_PRIORITY["pace_recalibration"]
    assert TRIGGER_PRIORITY["pace_recalibration"] > TRIGGER_PRIORITY["difficult_week"]


def test_all_triggers_have_priority():
    for kind in INTERVIEW_TRIGGERS:
        assert kind in TRIGGER_PRIORITY


# ---------------------------------------------------------------------------
# T023 — detect_pr_set
# ---------------------------------------------------------------------------


def _cw(*, athlete_id=1, distance_km=5.0, pace=5.0, activity_type="running",
        cw_id=100):
    """Minimal CompletedWorkout-shaped MagicMock."""
    cw = MagicMock()
    cw.id = cw_id
    cw.athlete_id = athlete_id
    cw.distance_km = distance_km
    cw.avg_pace_min_per_km = pace
    cw.activity_type = activity_type
    cw.date = date(2026, 5, 1)
    return cw


def test_pr_set_returns_true_when_strictly_faster():
    current = _cw(distance_km=5.0, pace=4.5, cw_id=50)
    prior = [_cw(distance_km=5.1, pace=5.0, cw_id=10),
             _cw(distance_km=4.95, pace=4.7, cw_id=11)]
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = prior

    assert detect_pr_set(current, db) is True


def test_pr_set_false_when_no_prior_runs():
    current = _cw(distance_km=5.0, pace=4.5, cw_id=50)
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []
    assert detect_pr_set(current, db) is False


def test_pr_set_false_when_within_1_percent():
    current = _cw(distance_km=5.0, pace=4.96, cw_id=50)  # 0.8% faster
    prior = [_cw(distance_km=5.0, pace=5.0, cw_id=10)]
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = prior
    assert detect_pr_set(current, db) is False


def test_pr_set_false_for_non_running_activity():
    current = _cw(activity_type="cycling")
    db = MagicMock()
    assert detect_pr_set(current, db) is False


def test_pr_set_false_when_distance_or_pace_missing():
    db = MagicMock()
    assert detect_pr_set(_cw(distance_km=None), db) is False
    assert detect_pr_set(_cw(pace=None), db) is False


# ---------------------------------------------------------------------------
# detect_pace_recalibration
# ---------------------------------------------------------------------------


def test_pace_recalibration_triggers_above_thresholds():
    db = MagicMock()
    assert story.detect_pace_recalibration(1, applied_session_count=3,
                                           avg_pace_shift_sec_per_mi=12.0, db=db) is True


def test_pace_recalibration_blocked_below_thresholds():
    db = MagicMock()
    assert story.detect_pace_recalibration(1, applied_session_count=2,
                                           avg_pace_shift_sec_per_mi=20.0, db=db) is False
    assert story.detect_pace_recalibration(1, applied_session_count=4,
                                           avg_pace_shift_sec_per_mi=8.0, db=db) is False
