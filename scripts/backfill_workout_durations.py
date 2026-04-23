#!/usr/bin/env python3
"""Backfill target_duration_seconds for time-based planned workouts.

Finds planned workouts that were prescribed by time (no target_distance_km)
but have a NULL target_duration_seconds. Parses the duration from the
workout description and populates the field.

Run:
    python scripts/backfill_workout_durations.py            # apply changes
    python scripts/backfill_workout_durations.py --dry-run  # preview only
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from running_coach_ai.database.session import get_session
from running_coach_ai.database.models import PlannedWorkout

# Types that are prescribed by time rather than distance
TIME_BASED_TYPES = {"easy", "strides", "cross_train"}


def parse_duration_seconds(text: str) -> int | None:
    """Parse a duration in seconds from natural language text."""
    if not text:
        return None

    # Hours + minutes: "1 hour 30 min", "1h30m", "1h 30m"
    m = re.search(r'(\d+)\s*h(?:our)?s?\s*(?:and\s*)?(\d+)\s*m(?:in)?', text, re.IGNORECASE)
    if m:
        return (int(m.group(1)) * 60 + int(m.group(2))) * 60

    # Hours only: "1 hour", "2 hours", "1.5 hours"
    m = re.search(r'(\d+(?:\.\d+)?)\s*h(?:our)?s?\b', text, re.IGNORECASE)
    if m:
        return round(float(m.group(1)) * 3600)

    # Minutes: "30 min", "30 minutes", "45-minute", "45min"
    m = re.search(r'(\d+)\s*-?\s*m(?:in(?:utes?)?)?\b', text, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        if 10 <= val <= 240:
            return val * 60

    return None


def main(dry_run: bool = False) -> None:
    with get_session() as db:
        workouts = (
            db.query(PlannedWorkout)
            .filter(
                PlannedWorkout.target_distance_km.is_(None),
                PlannedWorkout.target_duration_seconds.is_(None),
                PlannedWorkout.workout_type.in_(TIME_BASED_TYPES),
            )
            .order_by(PlannedWorkout.athlete_id, PlannedWorkout.scheduled_date)
            .all()
        )

        print(f"Found {len(workouts)} time-based workouts with no duration set")
        if dry_run:
            print("DRY RUN — no changes will be written\n")

        updated = 0
        skipped = 0
        for w in workouts:
            duration = parse_duration_seconds(w.description)
            if duration:
                status = "[DRY RUN] would set" if dry_run else "set"
                print(f"  {status} {duration // 60}min"
                      f" | athlete={w.athlete_id} [{w.scheduled_date}] {w.workout_type}"
                      f" | {(w.description or '')[:80]!r}")
                if not dry_run:
                    w.target_duration_seconds = duration
                updated += 1
            else:
                print(f"  SKIP (no parseable duration)"
                      f" | athlete={w.athlete_id} [{w.scheduled_date}] {w.workout_type}"
                      f" | {(w.description or '')[:80]!r}")
                skipped += 1

        if not dry_run:
            db.commit()

        verb = "Would update" if dry_run else "Updated"
        print(f"\n{verb} {updated} workouts, skipped {skipped}")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
