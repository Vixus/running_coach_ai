"""Live diagnostic script: health data + pace encoding write/read/verify.

Run from the project root:
    python scripts/diagnose.py
"""
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from running_coach_ai.database.session import SessionFactory
from running_coach_ai.database.models import Athlete
from running_coach_ai.garmin.client import get_garmin_client


def get_athlete(db):
    a = db.query(Athlete).filter(Athlete.onboarding_complete == True).first()
    if not a:
        print("ERROR: No onboarded athlete in DB")
        sys.exit(1)
    return a


# ─────────────────────────────────────────────────────────
# Section 1: Health data diagnostic
# ─────────────────────────────────────────────────────────

def diagnose_health(garmin, athlete_id: int):
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    print(f"\n{'='*60}")
    print(f"HEALTH DATA DIAGNOSTIC  (today={today})")
    print(f"{'='*60}")

    for label, d in [("TODAY", today), ("YESTERDAY", yesterday)]:
        print(f"\n--- Raw Garmin responses for {label} ({d}) ---")

        # Sleep
        try:
            sleep = garmin.get_sleep_data(d)
            dto = (sleep or {}).get("dailySleepDTO") or {}
            print(f"  sleep.dailySleepDTO keys: {list(dto.keys()) if dto else 'NO DTO'}")
            print(f"  sleep.dailySleepDTO.sleepTimeSeconds = {dto.get('sleepTimeSeconds')}")
            print(f"  sleep.dailySleepDTO.sleepScores = {dto.get('sleepScores')}")
        except Exception as e:
            print(f"  sleep FAILED: {e}")

        # HRV
        try:
            hrv = garmin.get_hrv_data(d)
            summary = (hrv or {}).get("hrvSummary") or {}
            print(f"  hrv.hrvSummary keys: {list(summary.keys()) if summary else 'NO SUMMARY'}")
            print(f"  hrv.hrvSummary.lastNight = {summary.get('lastNight')}")
            print(f"  hrv.hrvSummary.status    = {summary.get('status')}")
        except Exception as e:
            print(f"  hrv FAILED: {e}")

        # RHR
        try:
            rhr = garmin.get_rhr_day(d)
            if isinstance(rhr, dict):
                print(f"  rhr TOP-LEVEL keys: {list(rhr.keys())}")
                print(f"  rhr.restingHeartRate     = {rhr.get('restingHeartRate')}")
                print(f"  rhr.allDayHR             = {rhr.get('allDayHR')}")
            else:
                print(f"  rhr type={type(rhr).__name__}  value={rhr}")
        except Exception as e:
            print(f"  rhr FAILED: {e}")

        # Body battery
        try:
            bb = garmin.get_body_battery(d, d)
            if isinstance(bb, list) and bb:
                entry = bb[0]
                print(f"  bb[0] top-level keys: {list(entry.keys())}")
                print(f"  bb[0].charged        = {entry.get('charged')}")
                print(f"  bb[0].drained        = {entry.get('drained')}")
                stat_list = entry.get("bodyBatteryStatList") or []
                print(f"  bb[0].bodyBatteryStatList length: {len(stat_list)}")
                if stat_list:
                    print(f"  bb stat[0] keys: {list(stat_list[0].keys())}")
                    print(f"  bb stat[0].startBodyBatteryLevel = {stat_list[0].get('startBodyBatteryLevel')}")
                    print(f"  bb stat[0].endBodyBatteryLevel   = {stat_list[0].get('endBodyBatteryLevel')}")
                    end_levels = [s.get("endBodyBatteryLevel") for s in stat_list if s.get("endBodyBatteryLevel") is not None]
                    print(f"  bb all endBodyBatteryLevels: {end_levels}")
                    print(f"  bb MAX endBodyBatteryLevel (peak/wakeup): {max(end_levels) if end_levels else None}")
            else:
                print(f"  bb type={type(bb).__name__}  value={bb}")
        except Exception as e:
            print(f"  body_battery FAILED: {e}")

        # Stress
        try:
            stress = garmin.get_stress_data(d)
            if isinstance(stress, dict):
                print(f"  stress top-level keys: {list(stress.keys())}")
                print(f"  stress.avgStressLevel    = {stress.get('avgStressLevel')}")
                print(f"  stress.overallStressLevel= {stress.get('overallStressLevel')}")
            else:
                print(f"  stress type={type(stress).__name__}")
        except Exception as e:
            print(f"  stress FAILED: {e}")

        # Steps
        try:
            steps = garmin.get_steps_data(d)
            if isinstance(steps, list):
                total = sum(e.get("steps", 0) for e in steps if isinstance(e, dict))
                sample = steps[0] if steps else {}
                print(f"  steps: list of {len(steps)} entries, first keys={list(sample.keys()) if sample else '?'}")
                print(f"  steps total (sum of .steps field): {total}")
                if steps:
                    print(f"  steps[0] = {steps[0]}")
            elif isinstance(steps, dict):
                print(f"  steps dict keys: {list(steps.keys())}")
            else:
                print(f"  steps type={type(steps).__name__}  value={steps}")
        except Exception as e:
            print(f"  steps FAILED: {e}")


# ─────────────────────────────────────────────────────────
# Section 2: Pace encoding write/read/verify
# ─────────────────────────────────────────────────────────

def _sec_km_to_min_mi(sec_km: float) -> str:
    sec_mi = sec_km * 1.60934
    mins = int(sec_mi // 60)
    secs = int(sec_mi % 60)
    return f"{mins}:{secs:02d}/mi"


def _sec_km_to_min_km(sec_km: float) -> str:
    mins = int(sec_km // 60)
    secs = int(sec_km % 60)
    return f"{mins}:{secs:02d}/km"


def test_pace_encoding(garmin):
    """Upload a test workout with known pace, read it back, verify values."""
    print(f"\n{'='*60}")
    print("PACE ENCODING: write / read / verify")
    print(f"{'='*60}")

    # Target: 9:30/mi easy run = 5.905 min/km
    target_min_per_km = 5.905
    target_sec_km = round(target_min_per_km * 60)   # = 354
    zone_fast = round(target_sec_km * 0.95)          # = 336
    zone_slow = round(target_sec_km * 1.05)          # = 372

    print("\nSending to Garmin:")
    print(f"  targetValueOne (faster bound) = {zone_fast}  -> {_sec_km_to_min_km(zone_fast)} = {_sec_km_to_min_mi(zone_fast)}")
    print(f"  targetValueTwo (slower bound) = {zone_slow}  -> {_sec_km_to_min_km(zone_slow)} = {_sec_km_to_min_mi(zone_slow)}")
    print(f"  Expected display on device: ~{_sec_km_to_min_mi(zone_fast)} – {_sec_km_to_min_mi(zone_slow)}")

    test_workout = {
        "workoutName": "PACE_TEST_DELETE_ME",
        "description": "Diagnostic test workout — safe to delete",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [{
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                "endCondition": {"conditionTypeId": 3, "conditionTypeKey": "distance"},
                "endConditionValue": 8046.72,   # 5 miles in meters
                "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
                "targetValueOne": zone_fast,
                "targetValueTwo": zone_slow,
            }]
        }]
    }

    # Upload
    try:
        up_result = garmin.upload_workout(test_workout)
        workout_id = up_result.get("workoutId")
        print(f"\nUpload OK — workoutId={workout_id}")
    except Exception as e:
        print(f"\nUpload FAILED: {e}")
        return

    # Read back
    try:
        read_result = garmin.connectapi(f"/workout-service/workout/{workout_id}")
        print(f"\nRead-back workout name: {read_result.get('workoutName')}")
        steps = (read_result.get("workoutSegments") or [{}])[0].get("workoutSteps") or []
        if steps:
            step = steps[0]
            tv1 = step.get("targetValueOne")
            tv2 = step.get("targetValueTwo")
            print(f"\nReturned targetValueOne = {tv1}   (sent {zone_fast})")
            print(f"Returned targetValueTwo = {tv2}   (sent {zone_slow})")
            if tv1 is not None and tv2 is not None:
                print("\nGarmin stored them as:")
                print(f"  tv1 as sec/km: {tv1} → {_sec_km_to_min_km(tv1)} = {_sec_km_to_min_mi(tv1)}")
                print(f"  tv2 as sec/km: {tv2} → {_sec_km_to_min_km(tv2)} = {_sec_km_to_min_mi(tv2)}")
                if abs(tv1 - zone_fast) < 5 and abs(tv2 - zone_slow) < 5:
                    print("\n✓ PASS — values round-trip correctly. Formula is correct.")
                    print("  If device shows wrong pace, the issue is device unit settings, not encoding.")
                else:
                    print(f"\n✗ FAIL — values differ. We sent {zone_fast}/{zone_slow}, Garmin returned {tv1}/{tv2}.")
                    # Try interpreting as speed (m/s × 1000)
                    if tv1 is not None:
                        speed_ms = tv1 / 1000.0
                        pace_min_km = (1.0 / speed_ms) / 60
                        print(f"  If Garmin uses mm/s: tv1={tv1}mm/s = {speed_ms:.3f}m/s = {pace_min_km:.2f} min/km = {pace_min_km*1.60934:.2f} min/mi")
        else:
            print("No steps in read-back response")
    except Exception as e:
        print(f"Read-back FAILED: {e}")

    # Clean up
    try:
        garmin.garth.request("DELETE", "connectapi",
                             f"/workout-service/workout/{workout_id}", api=True)
        print(f"\nCleaned up test workout {workout_id}")
    except Exception as e:
        print(f"Cleanup warning: {e}")

    # Also show what the user sees: dump raw Garmin workout list to find any existing workouts
    print("\n--- Checking pace values on existing upcoming workouts ---")
    try:
        cal = garmin.connectapi("/workout-service/schedule/2026-04-01/2026-05-31")
        if isinstance(cal, list):
            for entry in cal[:5]:
                wid = entry.get("workoutId")
                wname = entry.get("workoutName", "?")
                wdate = entry.get("date", "?")
                print(f"  [{wdate}] {wname} (id={wid})")
                if wid:
                    try:
                        wd = garmin.connectapi(f"/workout-service/workout/{wid}")
                        segs = wd.get("workoutSegments") or []
                        for seg in segs:
                            for step in (seg.get("workoutSteps") or []):
                                tv1 = step.get("targetValueOne")
                                tv2 = step.get("targetValueTwo")
                                ttype = (step.get("targetType") or {}).get("workoutTargetTypeKey", "?")
                                if ttype == "pace.zone" and tv1 is not None:
                                    print(f"    pace.zone: tv1={tv1} ({_sec_km_to_min_km(tv1)} / {_sec_km_to_min_mi(tv1)})  tv2={tv2} ({_sec_km_to_min_km(tv2)} / {_sec_km_to_min_mi(tv2)})")
                    except Exception:
                        pass
    except Exception as e:
        print(f"  Calendar fetch failed: {e}")


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    db = SessionFactory()
    try:
        athlete = get_athlete(db)
        print(f"Athlete: {athlete.name}  (id={athlete.id})")
        g = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)
        print(f"Garmin auth OK: {g.get_full_name()}")
        print(f"display_name: {g.display_name}")

        diagnose_health(g, athlete.id)
        test_pace_encoding(g)

    finally:
        db.close()
