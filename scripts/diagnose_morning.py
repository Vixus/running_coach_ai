#!/usr/bin/env python3
"""Diagnose why morning check-ins aren't firing.

Run inside the scheduler container so it sees the same DB the running
scheduler reads from:

    docker-compose exec coach python scripts/diagnose_morning.py

Reports, per onboarded athlete:
  - configured timezone (and whether ZoneInfo can resolve it)
  - current athlete-local clock time
  - last_morning_checkin_date (dedup state)
  - today's HealthSnapshot presence and which fields are populated
  - whether a morning_checkin Notification already exists for athlete-local today
  - the morning-data gate verdict (would the next tick wait, or proceed?)

This won't tell us if APScheduler's in-memory job table is healthy — for
that, grep the scheduler logs from container startup for:
    "Registered morning check-in for athlete"
If those lines are absent, the job wasn't registered and we'd need to
look at why register_jobs() didn't see this athlete.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app")

from running_coach_ai.coach.adapter import (  # noqa: E402
    _garmin_morning_data_complete,
    _should_wait_for_morning_data,
)
from running_coach_ai.database.models import (  # noqa: E402
    Athlete,
    HealthSnapshot,
    Notification,
)
from running_coach_ai.database.session import get_session  # noqa: E402


def _fmt_field(snap, attr):
    val = getattr(snap, attr, None)
    return "—" if val is None else val


def main() -> None:
    print("=== Morning Check-in Diagnostics ===\n")
    with get_session() as db:
        athletes = (
            db.query(Athlete)
            .filter(Athlete.allowed == True, Athlete.onboarding_complete == True)  # noqa: E712
            .order_by(Athlete.id)
            .all()
        )
        if not athletes:
            print("No allowed, onboarded athletes in DB — register_jobs() would register zero morning jobs.")
            return

        for a in athletes:
            print(f"--- Athlete #{a.id}: {a.name} ---")
            tz_name = a.timezone or "America/New_York"
            try:
                tz = ZoneInfo(tz_name)
            except Exception as e:
                print(f"  ⚠ BAD timezone '{tz_name}' — ZoneInfo error: {e}")
                print("  → IntervalTrigger would fail to register; check the athlete.timezone column.\n")
                continue

            now_local = datetime.now(tz)
            today_local = now_local.date()
            print(f"  timezone:      {tz_name}")
            print(f"  now local:     {now_local.strftime('%Y-%m-%d %H:%M %Z')}")
            print(f"  last_morning:  {a.last_morning_checkin_date}")
            if a.last_morning_checkin_date == today_local:
                print("  → dedup guard would SKIP today's tick (last_morning matches today_local)")

            snap = (
                db.query(HealthSnapshot)
                .filter(HealthSnapshot.athlete_id == a.id, HealthSnapshot.date == today_local)
                .first()
            )
            if snap is None:
                print(f"  health snap:   none for {today_local}")
            else:
                print(
                    f"  health snap:   HRV={_fmt_field(snap, 'hrv_score')} "
                    f"sleep_score={_fmt_field(snap, 'sleep_score')} "
                    f"sleep_dur_s={_fmt_field(snap, 'sleep_duration_seconds')} "
                    f"RHR={_fmt_field(snap, 'resting_hr')} "
                    f"BB={_fmt_field(snap, 'body_battery_start')} "
                    f"TR={_fmt_field(snap, 'training_readiness')}"
                )
                print(f"  morning-data complete: {_garmin_morning_data_complete(snap)}")

            # Athlete-local today bounds in naive UTC (matches Notification.created_at)
            day_start_local = datetime.combine(today_local, datetime.min.time(), tzinfo=tz)
            day_start_utc = day_start_local.astimezone(timezone.utc).replace(tzinfo=None)
            day_end_utc = (
                (day_start_local + timedelta(days=1))
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
            )
            todays_notif = (
                db.query(Notification)
                .filter(
                    Notification.athlete_id == a.id,
                    Notification.kind == "morning_checkin",
                    Notification.created_at >= day_start_utc,
                    Notification.created_at < day_end_utc,
                )
                .order_by(Notification.created_at.desc())
                .first()
            )
            if todays_notif is None:
                print("  notif today:   none — home card will fall back to persona greeting")
            else:
                print(
                    f"  notif today:   #{todays_notif.id} created {todays_notif.created_at.isoformat()} UTC"
                )

            # Gate verdict — what would the next tick decide?
            wait = _should_wait_for_morning_data(
                snap, garmin_fetch_failed=False, athlete=a, now_local=now_local
            )
            print(f"  next tick would: {'WAIT for health data' if wait else 'PROCEED to Claude'}")
            if not wait and a.last_morning_checkin_date == today_local:
                print("    (…but dedup-skipped before reaching the gate)")
            print()


if __name__ == "__main__":
    main()
