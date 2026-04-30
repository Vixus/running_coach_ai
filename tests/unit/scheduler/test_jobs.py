"""Unit test for coach_analysis persistence in the activity poll job (T027b)."""

from datetime import date
from unittest.mock import MagicMock, patch


def test_generate_post_run_feedback_persists_coach_analysis():
    """After generate_post_run_feedback runs, CompletedWorkout.coach_analysis is set."""
    from running_coach_ai.coach.feedback import generate_post_run_feedback

    athlete = MagicMock()
    athlete.id = 1
    athlete.name = "Sarah"
    athlete.coach_key = "classic"

    completed = MagicMock()
    completed.id = 42
    completed.athlete_id = 1
    completed.date = date.today()
    completed.planned_workout_id = None
    completed.duration_seconds = 3600
    completed.distance_km = 10.0
    completed.avg_pace_min_per_km = 5.5
    completed.avg_hr = 145
    completed.max_hr = 175
    completed.calories = 500
    completed.feedback_given = False
    completed.coach_analysis = None

    biomechanics = {
        "zone_confidence": "low",
        "zone_max_hr_run_count": 1,
        "zone_source": "computed_max_hr",
    }

    mock_analysis = "Strong effort today — your LT pace held well in the final mile."

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    with patch("running_coach_ai.coach.feedback.call_claude", return_value=mock_analysis), \
         patch("running_coach_ai.coach.conversation.extract_and_save_memories", return_value=mock_analysis), \
         patch("running_coach_ai.coach.notify.notify"):
        generate_post_run_feedback(athlete, completed, biomechanics, db, MagicMock())

    assert completed.coach_analysis == mock_analysis
