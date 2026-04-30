"""Post-run feedback and weekly review generation via Claude."""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from running_coach_ai.coach.persona import call_claude, format_miles, format_pace_mi, km_to_mi
from running_coach_ai.coach.personas import get_persona
from running_coach_ai.database.models import Athlete, CompletedWorkout, RunningProfile

logger = logging.getLogger(__name__)

POST_RUN_FEEDBACK_PROMPT = """You are giving post-run feedback to {name} after their {run_or_race} today. You love data and use it precisely — reference actual numbers, spot patterns, and tell the athlete something they couldn't see just by looking at their watch summary.

ATHLETE'S TRAINING GOALS:
{goal_context}

PLANNED SESSION:
{planned_session}

ACTUAL RUN SUMMARY:
- Distance: {distance_mi} mi | Duration: {duration_min} min
- Avg pace: {avg_pace} min/mi | Avg HR: {avg_hr} bpm | Max HR: {max_hr} bpm
- Calories: {calories} kcal | Effort distribution: {effort_distribution}

HR ZONE DISTRIBUTION ({zone_source_label}){zone_confidence_note}:
  Z1 (<{z2_lower} bpm):        {zone1_pct}%
  Z2 ({z2_lower}–{z3_lower} bpm):  {zone2_pct}%    ← aerobic base zone
  Z3 ({z3_lower}–{z4_lower} bpm):  {zone3_pct}%    ← tempo zone
  Z4 ({z4_lower}–{z5_lower} bpm):  {zone4_pct}%    ← threshold zone
  Z5 (>{z5_lower} bpm):        {zone5_pct}%    ← max effort

DECILE PROGRESSION TABLE (each row = ~10% of total run duration):
Seg | Pace (min/mi) | HR (bpm) | Cadence (spm) | Power (W) | HR×Pace (economy index↑=worse)
{decile_table}

FATIGUE INDICES — First Quarter → Last Quarter:
  Cadence:          {cadence_q1} → {cadence_q4} spm  ({cadence_drop} change)
  Stride length:    {stride_q1} → {stride_q4} m      ({stride_drop} change)
  Ground contact:   {gct_q1} → {gct_q4} ms           ({gct_change} change, higher=worse)
  Vertical ratio:   {vr_q1} → {vr_q4}%               ({vr_change} change, higher=worse)
  Vertical osc:     {vo_q1} → {vo_q4} cm              ({vo_change} change)
  Power:            {power_q1} → {power_q4} W         ({power_fade} change)

AEROBIC INDICATORS:
  HR drift (Q4 vs Q1):      {hr_drift_pct}%  (>5% = cardiac fatigue, >10% = significant)
  Aerobic decoupling:        {aerobic_decoupling}%  (>5% = running too hard for aerobic benefit)
  Zone 2 compliance:         {zone_compliance_pct}%  (target >80% for easy/long runs)
  Cadence consistency (CV):  {cadence_cv}%  (lower = more consistent mechanics)

PERFORMANCE CONDITION ARC:
  Start: {perf_start} | Peak: {perf_peak} | End: {perf_end} | Shape: {perf_shape}
  (Garmin's real-time fitness assessment; >0 = above baseline, <0 = below)

DETECTED FATIGUE PATTERNS:
{fatigue_markers}

RUNNING PROFILE TRENDS (30-day rolling averages):
{profile_trends}

Write post-run feedback in coach voice. Be a data nerd — cite the specific numbers from above. Use the decile table to pinpoint exactly when and how things changed during the run. Connect the dots between metrics (e.g., if cadence dropped AND GCT increased AND stride shortened, that's a textbook fatigue cascade — say so and explain what it means). Compare today to the 30-day profile trends where relevant.

Structure (no bullet lists — write in paragraphs like a real coach message). Separate paragraphs with a blank line. Lead paragraphs 2–4 with a short bold mini-heading (2–4 words, ending in a period) using markdown bold syntax — e.g. **The data.** or **Key finding.** — followed by the paragraph text on the same line. Paragraph 1 stays a clean opening hook with no heading.
1. Opening (no heading): overall picture of the run and how it went vs the plan
2. **The data.** — what the numbers actually reveal. Use the decile table to show where HR started to drift, when form started to break down, what the economy index tells us
3. **Key finding.** — the most important single insight from today (could be great or concerning)
4. **Next time.** — one actionable takeaway based on the patterns

Keep it to 3–4 paragraphs. If you spot a pattern worth tracking over time (e.g., form reliably breaks down after 70 min), add a <remember> tag.{overtraining_flag}"""

WEEKLY_REVIEW_PROMPT = """You are writing the weekly training review for {name}.

WEEK SUMMARY:
- Sessions: {completed}/{planned} completed ({completion_pct:.0f}%)
- Distance: {actual_mi} mi / {planned_mi} mi planned
- Quality sessions: {quality_completed}/{quality_planned}
- Training load: {training_load}
- Avg HRV: {avg_hrv}
- Avg sleep score: {avg_sleep_score}

RUNNING PROFILE TRENDS:
{profile_trends}

Write the weekly review in coach voice. Cover:
1. How the week went — honest, not sugar-coated
2. The key positive from the week
3. One thing to focus on next week
4. A brief preview of next week

Keep it conversational — 3-4 short paragraphs. This lands on Sunday evening so it should feel like a debrief from a real coach."""


def _format_profile(profile: RunningProfile | None) -> str:
    if not profile:
        return "Insufficient data for trends yet."
    lines = []
    if profile.typical_cadence_easy_spm:
        lines.append(f"- Easy cadence: {profile.typical_cadence_easy_spm} spm ({profile.cadence_trend or 'N/A'})")
    if profile.hr_drift_pct is not None:
        lines.append(f"- HR drift (avg long runs): {profile.hr_drift_pct}%"
                     + (f" trend: {profile.hr_drift_trend}" if profile.hr_drift_trend else ""))
    if profile.hr_pace_decoupling is not None:
        lines.append(f"- Aerobic decoupling (avg): {profile.hr_pace_decoupling}%"
                     + (f" trend: {profile.hr_pace_decoupling_trend}" if profile.hr_pace_decoupling_trend else ""))
    if profile.easy_hr_zone_compliance_pct is not None:
        lines.append(f"- Zone 2 compliance (avg easy runs): {profile.easy_hr_zone_compliance_pct}%"
                     + (f" trend: {profile.easy_hr_zone_compliance_trend}" if profile.easy_hr_zone_compliance_trend else ""))
    return "\n".join(lines) if lines else "Not enough runs yet to establish trends."


def _fmt(val, decimals=1, suffix=""):
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}{suffix}"


def _fmt_change(val):
    """Format a percentage change with sign and label."""
    if val is None:
        return "N/A"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.1f}%"


def _build_decile_table(bio: dict) -> str:
    """Render the decile progression as a compact text table for the prompt."""
    hr_d = bio.get("hr_deciles") or [None] * 10
    pace_d = bio.get("pace_deciles") or [None] * 10
    cad_d = bio.get("cadence_deciles") or [None] * 10
    pwr_d = bio.get("power_deciles") or [None] * 10
    eco_d = bio.get("eco_deciles") or [None] * 10

    lines = []
    for i in range(10):
        hr_s = f"{hr_d[i]:.0f}" if hr_d[i] else "  — "
        pace_s = format_pace_mi(pace_d[i]) if pace_d[i] else "  — "
        cad_s = f"{cad_d[i]:.0f}" if cad_d[i] else "  — "
        pwr_s = f"{pwr_d[i]:.0f}" if pwr_d[i] else "  — "
        eco_s = f"{eco_d[i]:.1f}" if eco_d[i] else "  — "
        lines.append(f"  {i+1:2d}  |   {pace_s}     |   {hr_s}   |    {cad_s}     |  {pwr_s}  |  {eco_s}")
    return "\n".join(lines)


def generate_post_run_feedback(
    athlete: Athlete,
    completed: CompletedWorkout,
    biomechanics_result: dict,
    db_session: Session,
    athlete_max_hr: int | None = None,
) -> None:
    """Generate and send post-run feedback for a completed workout."""
    from running_coach_ai.database.models import PlannedWorkout
    from running_coach_ai.coach.conversation import extract_and_save_memories

    from running_coach_ai.database.models import Goal

    # Check if today is a race day
    race_goal = (
        db_session.query(Goal)
        .filter(
            Goal.athlete_id == athlete.id,
            Goal.race_date == completed.date,
            Goal.active,
        )
        .first()
    )
    is_race_day = race_goal is not None
    run_or_race = "race" if is_race_day else "run"

    # Build goal context for the prompt (upcoming races only)
    future_goals = (
        db_session.query(Goal)
        .filter(
            Goal.athlete_id == athlete.id,
            Goal.active,
            Goal.race_date > completed.date,
        )
        .order_by(Goal.race_date)
        .all()
    )
    if future_goals:
        goal_lines = []
        for g in future_goals:
            days_away = (g.race_date - completed.date).days
            th = g.target_time_seconds // 3600
            tm = (g.target_time_seconds % 3600) // 60
            race_name = g.race_name or g.race_type
            goal_lines.append(
                f"- {race_name} ({g.race_type}) on {g.race_date} — {days_away} days away | target: {th}:{tm:02d}"
            )
        goal_context = "\n".join(goal_lines)
    else:
        goal_context = "No upcoming race goals set."

    # Get planned session if it exists
    if is_race_day:
        race_name = race_goal.race_name or race_goal.race_type
        th = race_goal.target_time_seconds // 3600
        tm = (race_goal.target_time_seconds % 3600) // 60
        planned_text = f"RACE DAY — {race_name} ({race_goal.race_type}) | target: {th}:{tm:02d}"
    else:
        planned_text = "Unplanned run (not in training schedule)."
        if completed.planned_workout_id:
            planned = db_session.query(PlannedWorkout).get(completed.planned_workout_id)
            if planned:
                dist_str = f" {format_miles(planned.target_distance_km)}" if planned.target_distance_km else ""
                pace_str = f" @ {format_pace_mi(planned.target_pace_min_per_km)}" if planned.target_pace_min_per_km else ""
                planned_text = (
                    f"{planned.workout_type}{dist_str}{pace_str}\n"
                    f"{planned.description or ''}"
                )

    # Get running profile
    profile = (
        db_session.query(RunningProfile)
        .filter(RunningProfile.athlete_id == athlete.id)
        .first()
    )

    duration_min = (completed.duration_seconds or 0) // 60

    distance_mi = format_miles(completed.distance_km) if completed.distance_km else "N/A"
    avg_pace_mi = format_pace_mi(completed.avg_pace_min_per_km) if completed.avg_pace_min_per_km else "N/A"

    # --- Overtraining risk pre-check ---
    # Check last 7 days of completed workouts for volume and consecutive hard sessions.
    seven_days_ago = datetime.utcnow().date() - timedelta(days=7)
    recent_runs = (
        db_session.query(CompletedWorkout)
        .filter(
            CompletedWorkout.athlete_id == athlete.id,
            CompletedWorkout.date >= seven_days_ago,
        )
        .order_by(CompletedWorkout.date.desc())
        .all()
    )
    total_duration_7d = sum((r.duration_seconds or 0) for r in recent_runs) // 60  # minutes
    # "Hard" sessions: avg HR > 155 bpm or aerobic decoupling > 10% or avg pace < 5:00/km
    hard_session_count = sum(
        1 for r in recent_runs
        if (r.avg_hr and r.avg_hr > 155)
        or (r.avg_pace_min_per_km and r.avg_pace_min_per_km < 5.0)
    )

    overtraining_flag = ""
    if total_duration_7d > 400 or hard_session_count >= 3:
        reasons = []
        if total_duration_7d > 400:
            reasons.append(f"cumulative 7-day training load ({total_duration_7d} min) exceeds safe threshold (400 min)")
        if hard_session_count >= 3:
            reasons.append(f"{hard_session_count} hard sessions in the last 7 days")
        overtraining_flag = (
            "\n\n⚠️ INJURY_RISK_FLAG: The following overtraining signals have been detected — "
            f"{'; '.join(reasons)}. "
            "You MUST address injury and overtraining risk explicitly in your feedback. "
            "Recommend recovery actions (easy run, rest day, sleep, nutrition). "
            "Do NOT overlook this even if the athlete seems fine."
        )

    b = biomechanics_result
    max_hr = athlete_max_hr or completed.max_hr or 189

    zone_confidence = b.get("zone_confidence", "low")
    zone_run_count = b.get("zone_max_hr_run_count", 0)
    if zone_confidence == "low":
        zone_confidence_note = (
            f"\n  ⚠️  ZONE CONFIDENCE: LOW ({zone_run_count} run{'s' if zone_run_count != 1 else ''} of data). "
            "Max HR anchor may not reflect true physiological ceiling — these zones could be shifted. "
            "Do NOT draw firm conclusions about zone compliance. "
            "Mention to the athlete that zones will become more reliable as more runs are logged."
        )
    elif zone_confidence == "moderate":
        zone_confidence_note = (
            f"\n  ℹ️  ZONE CONFIDENCE: MODERATE ({zone_run_count} runs of data). "
            "Use zone analysis as a guide only; avoid strong zone-compliance conclusions."
        )
    else:
        zone_confidence_note = ""

    # Build zone boundary labels and source description
    zone_source = b.get("zone_source", "computed_max_hr")
    garmin_bounds = b.get("garmin_zone_boundaries") or {}
    if zone_source == "garmin_lthr" and garmin_bounds:
        lthr_val = garmin_bounds.get(5, "?")
        zone_source_label = f"Garmin LTHR-based zones, LTHR={lthr_val} bpm"
        z2_lower = garmin_bounds.get(2, round(max_hr * 0.60))
        z3_lower = garmin_bounds.get(3, round(max_hr * 0.70))
        z4_lower = garmin_bounds.get(4, round(max_hr * 0.80))
        z5_lower = garmin_bounds.get(5, round(max_hr * 0.90))
    else:
        zone_source_label = f"computed from max HR {max_hr} bpm, {zone_run_count} run{'s' if zone_run_count != 1 else ''}"
        z2_lower = round(max_hr * 0.60)
        z3_lower = round(max_hr * 0.70)
        z4_lower = round(max_hr * 0.80)
        z5_lower = round(max_hr * 0.90)

    prompt = POST_RUN_FEEDBACK_PROMPT.format(
        name=athlete.name,
        run_or_race=run_or_race,
        goal_context=goal_context,
        planned_session=planned_text,
        distance_mi=distance_mi,
        duration_min=duration_min,
        avg_pace=avg_pace_mi,
        avg_hr=completed.avg_hr or "N/A",
        max_hr=max_hr,
        calories=completed.calories or "N/A",
        effort_distribution=b.get("effort_distribution") or "N/A",
        # Zone source + confidence
        zone_source_label=zone_source_label,
        zone_confidence_note=zone_confidence_note,
        # Zone bpm boundaries
        z2_lower=z2_lower,
        z3_lower=z3_lower,
        z4_lower=z4_lower,
        z5_lower=z5_lower,
        zone1_pct=_fmt(b.get("zone1_pct")),
        zone2_pct=_fmt(b.get("zone2_pct")),
        zone3_pct=_fmt(b.get("zone3_pct")),
        zone4_pct=_fmt(b.get("zone4_pct")),
        zone5_pct=_fmt(b.get("zone5_pct")),
        # Decile table
        decile_table=_build_decile_table(b),
        # Fatigue indices
        cadence_q1=_fmt(b.get("cadence_q1_spm"), 0),
        cadence_q4=_fmt(b.get("cadence_q4_spm"), 0),
        cadence_drop=_fmt_change(b.get("cadence_drop_pct")),
        stride_q1=_fmt(b.get("stride_q1_m"), 2),
        stride_q4=_fmt(b.get("stride_q4_m"), 2),
        stride_drop=_fmt_change(b.get("stride_drop_pct")),
        gct_q1=_fmt(b.get("gct_q1_ms"), 0),
        gct_q4=_fmt(b.get("gct_q4_ms"), 0),
        gct_change=_fmt_change(b.get("gct_increase_pct")),
        vr_q1=_fmt(b.get("vr_q1_pct"), 2),
        vr_q4=_fmt(b.get("vr_q4_pct"), 2),
        vr_change=_fmt_change(b.get("vr_change_pct")),
        vo_q1=_fmt(b.get("vo_q1_cm"), 2),
        vo_q4=_fmt(b.get("vo_q4_cm"), 2),
        vo_change=_fmt_change(b.get("vo_change_pct")),
        power_q1=_fmt(b.get("power_q1_w"), 0),
        power_q4=_fmt(b.get("power_q4_w"), 0),
        power_fade=_fmt_change(b.get("power_fade_pct")),
        # Aerobic indicators
        hr_drift_pct=_fmt(b.get("hr_drift_pct")),
        aerobic_decoupling=_fmt(b.get("aerobic_decoupling")),
        zone_compliance_pct=_fmt(b.get("zone_compliance_pct")),
        cadence_cv=_fmt(b.get("cadence_cv")),
        # Performance condition
        perf_start=_fmt(b.get("perf_condition_start"), 0),
        perf_peak=_fmt(b.get("perf_condition_peak"), 0),
        perf_end=_fmt(b.get("perf_condition_end"), 0),
        perf_shape=b.get("perf_condition_shape") or "N/A",
        # Fatigue markers
        fatigue_markers="\n".join(f"  • {m}" for m in (b.get("fatigue_markers") or ["N/A"])),
        profile_trends=_format_profile(profile),
        overtraining_flag=overtraining_flag,
    )

    try:
        response = call_claude(get_persona(athlete.coach_key).persona_block, [{"role": "user", "content": prompt}])
    except Exception as e:
        logger.error("Claude feedback generation failed for athlete %d: %s", athlete.id, e)
        return

    # Extract <remember> tags
    response = extract_and_save_memories(athlete.id, response, db_session)

    # Save to conversation history so the coach has context when the athlete replies
    from running_coach_ai.database.models import ConversationMessage
    db_session.add(ConversationMessage(
        athlete_id=athlete.id,
        role="assistant",
        content=response,
    ))

    # Persist coach analysis for web Activity Feed display
    completed.coach_analysis = response

    # Mark feedback given
    completed.feedback_given = True
    db_session.commit()

    try:
        from running_coach_ai.coach.notify import notify
        notify(
            db_session, athlete,
            kind="post_run_feedback",
            title="Post-run feedback",
            body=response,
            action_path=f"/app#activities/{completed.id}",
            related_id=completed.id,
        )
        db_session.commit()
        logger.info("Post-run feedback delivered to athlete %d", athlete.id)
    except Exception as e:
        logger.error("Failed to deliver feedback to athlete %d: %s", athlete.id, e)


def generate_weekly_review(athlete: Athlete, week_summary: dict, db_session: Session) -> str:
    """Generate a weekly review message in coach voice via Claude."""
    profile = (
        db_session.query(RunningProfile)
        .filter(RunningProfile.athlete_id == athlete.id)
        .first()
    )

    actual_mi = round(km_to_mi(week_summary["actual_km"]), 1)
    planned_mi = round(km_to_mi(week_summary["planned_km"]), 1)

    prompt = WEEKLY_REVIEW_PROMPT.format(
        name=athlete.name,
        completed=week_summary["completed_sessions"],
        planned=week_summary["planned_sessions"],
        completion_pct=week_summary["completion_pct"],
        actual_mi=actual_mi,
        planned_mi=planned_mi,
        quality_completed=week_summary["quality_sessions_completed"],
        quality_planned=week_summary["quality_sessions_planned"],
        training_load=week_summary["total_training_load"],
        avg_hrv=week_summary.get("avg_hrv", "N/A"),
        avg_sleep_score=week_summary.get("avg_sleep_score", "N/A"),
        profile_trends=_format_profile(profile),
    )

    try:
        response = call_claude(get_persona(athlete.coach_key).persona_block, [{"role": "user", "content": prompt}])
        logger.info("Weekly review generated for athlete %d", athlete.id)
        return response
    except Exception as e:
        logger.error("Weekly review generation failed for athlete %d: %s", athlete.id, e)
        return f"I hit a snag putting together this week's review, {athlete.name} — I'll catch you with a proper one next Sunday."
