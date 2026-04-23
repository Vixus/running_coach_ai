"""reorder_planned_workouts_columns

Revision ID: k7g8h9i0j1k2
Revises: j6f7g8h9i0j1
Create Date: 2026-04-23

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'k7g8h9i0j1k2'
down_revision: Union[str, None] = 'j6f7g8h9i0j1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite has no ALTER TABLE ... MOVE COLUMN; recreate with desired order.
    op.execute("""
        CREATE TABLE planned_workouts_new (
            id                    INTEGER  NOT NULL,
            plan_id               INTEGER  NOT NULL,
            athlete_id            INTEGER  NOT NULL,
            scheduled_date        DATE     NOT NULL,
            workout_type          TEXT     NOT NULL,
            workout_name          TEXT,
            description           TEXT,
            target_distance_km    FLOAT,
            target_duration_seconds INTEGER,
            target_pace_min_per_km  FLOAT,
            target_zones_json     JSON,
            status                TEXT     NOT NULL,
            modified_reason       TEXT,
            garmin_workout_id     TEXT,
            garmin_schedule_id    TEXT,
            last_garmin_synced_at DATETIME,
            created_at            DATETIME,
            updated_at            DATETIME,
            PRIMARY KEY (id),
            FOREIGN KEY(plan_id)    REFERENCES training_plans (id),
            FOREIGN KEY(athlete_id) REFERENCES athletes (id)
        )
    """)
    op.execute("""
        INSERT INTO planned_workouts_new
            SELECT id, plan_id, athlete_id, scheduled_date, workout_type,
                   workout_name, description,
                   target_distance_km, target_duration_seconds, target_pace_min_per_km,
                   target_zones_json,
                   status, modified_reason,
                   garmin_workout_id, garmin_schedule_id, last_garmin_synced_at,
                   created_at, updated_at
            FROM planned_workouts
    """)
    op.execute("DROP TABLE planned_workouts")
    op.execute("ALTER TABLE planned_workouts_new RENAME TO planned_workouts")
    op.execute("CREATE INDEX ix_planned_workouts_athlete_date   ON planned_workouts (athlete_id, scheduled_date)")
    op.execute("CREATE INDEX ix_planned_workouts_athlete_status ON planned_workouts (athlete_id, status)")


def downgrade() -> None:
    # Restore original column order (as it existed before this migration).
    op.execute("""
        CREATE TABLE planned_workouts_new (
            id                      INTEGER NOT NULL,
            plan_id                 INTEGER NOT NULL,
            athlete_id              INTEGER NOT NULL,
            scheduled_date          DATE    NOT NULL,
            workout_type            TEXT    NOT NULL,
            description             TEXT,
            target_distance_km      FLOAT,
            target_pace_min_per_km  FLOAT,
            target_zones_json       JSON,
            garmin_workout_id       TEXT,
            garmin_schedule_id      TEXT,
            status                  TEXT    NOT NULL,
            modified_reason         TEXT,
            workout_name            TEXT,
            last_garmin_synced_at   DATETIME,
            created_at              DATETIME,
            updated_at              DATETIME,
            target_duration_seconds INTEGER,
            PRIMARY KEY (id),
            FOREIGN KEY(plan_id)    REFERENCES training_plans (id),
            FOREIGN KEY(athlete_id) REFERENCES athletes (id)
        )
    """)
    op.execute("""
        INSERT INTO planned_workouts_new
            SELECT id, plan_id, athlete_id, scheduled_date, workout_type,
                   description,
                   target_distance_km, target_pace_min_per_km, target_zones_json,
                   garmin_workout_id, garmin_schedule_id,
                   status, modified_reason,
                   workout_name, last_garmin_synced_at,
                   created_at, updated_at, target_duration_seconds
            FROM planned_workouts
    """)
    op.execute("DROP TABLE planned_workouts")
    op.execute("ALTER TABLE planned_workouts_new RENAME TO planned_workouts")
    op.execute("CREATE INDEX ix_planned_workouts_athlete_date   ON planned_workouts (athlete_id, scheduled_date)")
    op.execute("CREATE INDEX ix_planned_workouts_athlete_status ON planned_workouts (athlete_id, status)")
