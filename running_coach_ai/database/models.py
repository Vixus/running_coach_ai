"""SQLAlchemy ORM models for the AI Running Coach."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Float,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class Athlete(Base):
    __tablename__ = "athletes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slack_user_id = Column(Text, unique=True, nullable=True)
    slack_dm_channel_id = Column(Text, nullable=True)
    email = Column(Text, unique=True, nullable=True)
    name = Column(Text, nullable=True)
    age = Column(Integer, nullable=True)
    home_lat = Column(Float, nullable=True)
    home_lon = Column(Float, nullable=True)
    timezone = Column(Text, nullable=True)
    garmin_email = Column(Text, nullable=True)
    garmin_password_encrypted = Column(LargeBinary, nullable=True)
    onboarding_complete = Column(Boolean, nullable=False, default=False)
    onboarding_step = Column(Integer, nullable=False, default=0)
    allowed = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_morning_checkin_date = Column(Date, nullable=True)
    lthr_bpm = Column(Integer, nullable=True)       # Lactate Threshold HR from Garmin profile
    coach_key = Column(Text, nullable=True)          # Key into PERSONAS registry; NULL treated as "classic"
    pending_onboarding_data = Column(JSON, nullable=True)           # Profile JSON awaiting Garmin creds modal
    pending_onboarding_data_created_at = Column(DateTime, nullable=True)  # UTC timestamp for TTL check
    web_username = Column(Text, nullable=True)
    web_password_hash = Column(Text, nullable=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    prescription_style = Column(Text, nullable=True)  # "time" | "distance" | None (not yet set)

    goals = relationship("Goal", back_populates="athlete")
    health_snapshots = relationship("HealthSnapshot", back_populates="athlete")
    coach_memories = relationship("CoachMemory", back_populates="athlete")
    conversation_messages = relationship("ConversationMessage", back_populates="athlete")
    running_profile = relationship("RunningProfile", back_populates="athlete", uselist=False)
    run_feedbacks = relationship("RunFeedback", back_populates="athlete")
    weekly_review_summaries = relationship("WeeklyReviewSummary", back_populates="athlete")
    web_events = relationship("WebEvent", back_populates="athlete")
    notifications = relationship("Notification", back_populates="athlete")


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    race_type = Column(Text, nullable=False)
    race_name = Column(Text, nullable=True)
    race_date = Column(Date, nullable=False)
    target_time_seconds = Column(Integer, nullable=False)
    current_weekly_mileage_km = Column(Float, nullable=True)
    experience_level = Column(Text, nullable=False)
    training_days_per_week = Column(Integer, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    athlete = relationship("Athlete", back_populates="goals")
    training_plans = relationship("TrainingPlan", back_populates="goal")

    __table_args__ = (
        Index("ix_goals_athlete_active", "athlete_id", "active"),
    )


class TrainingPlan(Base):
    __tablename__ = "training_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    goal_id = Column(Integer, ForeignKey("goals.id"), nullable=False)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=False)
    plan_json = Column(JSON, nullable=False)
    current_phase = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True)

    goal = relationship("Goal", back_populates="training_plans")
    planned_workouts = relationship("PlannedWorkout", back_populates="training_plan")


class PlannedWorkout(Base):
    __tablename__ = "planned_workouts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("training_plans.id"), nullable=False)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    scheduled_date = Column(Date, nullable=False)
    workout_type = Column(Text, nullable=False)
    workout_name = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    target_distance_km = Column(Float, nullable=True)
    target_duration_seconds = Column(Integer, nullable=True)
    target_pace_min_per_km = Column(Float, nullable=True)
    target_zones_json = Column(JSON, nullable=True)
    status = Column(Text, nullable=False, default="planned")
    modified_reason = Column(Text, nullable=True)
    garmin_workout_id = Column(Text, nullable=True)
    garmin_schedule_id = Column(Text, nullable=True)
    last_garmin_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    training_plan = relationship("TrainingPlan", back_populates="planned_workouts")
    completed_workout = relationship("CompletedWorkout", back_populates="planned_workout", uselist=False)

    __table_args__ = (
        Index("ix_planned_workouts_athlete_date", "athlete_id", "scheduled_date"),
        Index("ix_planned_workouts_athlete_status", "athlete_id", "status"),
    )


class CompletedWorkout(Base):
    __tablename__ = "completed_workouts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    planned_workout_id = Column(Integer, ForeignKey("planned_workouts.id"), nullable=True)
    garmin_activity_id = Column(Text, unique=True, nullable=False)
    activity_type = Column(Text, nullable=True)  # Garmin typeKey e.g. "running", "hiking", "tennis"
    date = Column(Date, nullable=False)
    distance_km = Column(Float, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    avg_hr = Column(Integer, nullable=True)
    max_hr = Column(Integer, nullable=True)
    avg_pace_min_per_km = Column(Float, nullable=True)
    max_pace_min_per_km = Column(Float, nullable=True)
    avg_cadence_spm = Column(Integer, nullable=True)
    max_cadence_spm = Column(Integer, nullable=True)
    avg_stride_length_m = Column(Float, nullable=True)
    avg_ground_contact_time_ms = Column(Float, nullable=True)
    avg_vertical_oscillation_cm = Column(Float, nullable=True)
    avg_vertical_ratio_pct = Column(Float, nullable=True)
    avg_power_w = Column(Float, nullable=True)
    max_power_w = Column(Float, nullable=True)
    elevation_gain_m = Column(Float, nullable=True)
    training_load = Column(Float, nullable=True)
    aerobic_training_effect = Column(Float, nullable=True)
    anaerobic_training_effect = Column(Float, nullable=True)
    vo2max_estimate = Column(Float, nullable=True)
    calories = Column(Integer, nullable=True)
    telemetry_channels_json = Column(JSON, nullable=True)
    feedback_given = Column(Boolean, nullable=False, default=False)
    coach_analysis = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    planned_workout = relationship("PlannedWorkout", back_populates="completed_workout")
    telemetry = relationship("WorkoutTelemetry", back_populates="completed_workout", uselist=False, cascade="all, delete-orphan")
    run_feedback = relationship("RunFeedback", back_populates="completed_workout", uselist=False)

    __table_args__ = (
        Index("ix_completed_workouts_athlete_date", "athlete_id", "date"),
    )


class WorkoutTelemetry(Base):
    __tablename__ = "workout_telemetry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    completed_workout_id = Column(Integer, ForeignKey("completed_workouts.id"), unique=True, nullable=False)
    sample_interval_seconds = Column(Integer, nullable=True)
    heart_rate_json = Column(JSON, nullable=True)
    pace_json = Column(JSON, nullable=True)
    cadence_json = Column(JSON, nullable=True)
    stride_length_json = Column(JSON, nullable=True)
    ground_contact_time_json = Column(JSON, nullable=True)
    vertical_oscillation_json = Column(JSON, nullable=True)
    vertical_ratio_json = Column(JSON, nullable=True)
    power_json = Column(JSON, nullable=True)
    elevation_json = Column(JSON, nullable=True)
    air_temperature_json = Column(JSON, nullable=True)
    respiration_rate_json = Column(JSON, nullable=True)
    performance_condition_json = Column(JSON, nullable=True)
    laps_json = Column(JSON, nullable=True)
    recorded_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    completed_workout = relationship("CompletedWorkout", back_populates="telemetry")


class RunningProfile(Base):
    __tablename__ = "running_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), unique=True, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    typical_cadence_easy_spm = Column(Float, nullable=True)
    typical_cadence_hard_spm = Column(Float, nullable=True)
    typical_ground_contact_easy_ms = Column(Float, nullable=True)
    typical_ground_contact_hard_ms = Column(Float, nullable=True)
    typical_vertical_oscillation_cm = Column(Float, nullable=True)
    typical_vertical_ratio_pct = Column(Float, nullable=True)
    cadence_trend = Column(Text, nullable=True)
    hr_drift_pct = Column(Float, nullable=True)
    hr_drift_trend = Column(Text, nullable=True)
    hr_pace_decoupling = Column(Float, nullable=True)
    hr_pace_decoupling_trend = Column(Text, nullable=True)
    easy_hr_zone_compliance_pct = Column(Float, nullable=True)
    easy_hr_zone_compliance_trend = Column(Text, nullable=True)
    preferred_effort_distribution = Column(JSON, nullable=True)
    notes_json = Column(JSON, nullable=True)

    athlete = relationship("Athlete", back_populates="running_profile")


class HealthSnapshot(Base):
    __tablename__ = "health_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    date = Column(Date, nullable=False)
    hrv_score = Column(Float, nullable=True)
    hrv_status = Column(Text, nullable=True)
    resting_hr = Column(Integer, nullable=True)
    sleep_score = Column(Integer, nullable=True)
    sleep_duration_seconds = Column(Integer, nullable=True)
    body_battery_start = Column(Integer, nullable=True)
    body_battery_end = Column(Integer, nullable=True)
    stress_avg = Column(Integer, nullable=True)
    steps = Column(Integer, nullable=True)
    spo2_avg = Column(Float, nullable=True)
    training_readiness = Column(Integer, nullable=True)

    athlete = relationship("Athlete", back_populates="health_snapshots")

    __table_args__ = (
        UniqueConstraint("athlete_id", "date", name="uq_health_snapshot_athlete_date"),
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    slack_ts = Column(Text, nullable=True)
    role = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    source = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    athlete = relationship("Athlete", back_populates="conversation_messages")

    __table_args__ = (
        Index("ix_conversation_messages_athlete_created", "athlete_id", "created_at"),
    )


class CoachMemory(Base):
    __tablename__ = "coach_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    category = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    active = Column(Boolean, nullable=False, default=True)
    source = Column(Text, nullable=True)

    athlete = relationship("Athlete", back_populates="coach_memories")

    __table_args__ = (
        Index("ix_coach_memories_athlete_active", "athlete_id", "active"),
    )


class RunFeedback(Base):
    __tablename__ = "run_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    completed_workout_id = Column(Integer, ForeignKey("completed_workouts.id"), unique=True, nullable=False)
    feel_score = Column(Integer, nullable=True)
    rpe = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    athlete = relationship("Athlete", back_populates="run_feedbacks")
    completed_workout = relationship("CompletedWorkout", back_populates="run_feedback")

    __table_args__ = (
        Index("ix_run_feedback_athlete_workout", "athlete_id", "completed_workout_id"),
    )


class WeeklyReviewSummary(Base):
    __tablename__ = "weekly_review_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    week_start_date = Column(Date, nullable=False)
    total_miles = Column(Float, nullable=True)
    elevation_gain_ft = Column(Float, nullable=True)
    avg_hrv = Column(Float, nullable=True)
    total_tss = Column(Float, nullable=True)
    narrative = Column(Text, nullable=False)
    daily_volume_json = Column(JSON, nullable=True)
    body_battery_json = Column(JSON, nullable=True)
    next_week_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    athlete = relationship("Athlete", back_populates="weekly_review_summaries")

    __table_args__ = (
        UniqueConstraint("athlete_id", "week_start_date", name="uq_weekly_review_athlete_week"),
    )


class WebEvent(Base):
    __tablename__ = "web_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    severity = Column(Text, nullable=False)
    category = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    details_json = Column(JSON, nullable=True)

    athlete = relationship("Athlete", back_populates="web_events")

    __table_args__ = (
        Index("ix_web_events_timestamp", "timestamp"),
        Index("ix_web_events_category_timestamp", "category", "timestamp"),
    )


class InviteToken(Base):
    __tablename__ = "invite_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(Text, unique=True, nullable=False)
    email_hint = Column(Text, nullable=True)
    created_by_athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    used_by_athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=True)
    used_at = Column(DateTime, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    kind = Column(Text, nullable=False)  # morning_checkin | post_run_feedback | weekly_review | system
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    action_path = Column(Text, nullable=True)
    related_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    read_at = Column(DateTime, nullable=True)

    athlete = relationship("Athlete", back_populates="notifications")

    __table_args__ = (
        Index("ix_notifications_athlete_created", "athlete_id", "created_at"),
        Index("ix_notifications_athlete_unread", "athlete_id", "read_at"),
    )
