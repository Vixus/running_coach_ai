"""add_planned_workout_timestamps_and_running_profile_trends

Revision ID: c8111922664b
Revises: 2870a78bdeb4
Create Date: 2026-04-04 16:55:18.806460

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8111922664b'
down_revision: Union[str, None] = '2870a78bdeb4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PlannedWorkout: new timestamp columns.
    # created_at and updated_at are NOT NULL in the model but SQLite cannot add
    # a NOT NULL column without a server_default on an existing table.
    # Strategy: add as nullable, backfill with utcnow, then leave nullable=True
    # (the application layer always sets these on new rows).
    op.add_column('planned_workouts', sa.Column('last_garmin_synced_at', sa.DateTime(), nullable=True))
    op.add_column('planned_workouts', sa.Column('created_at', sa.DateTime(), nullable=True))
    op.add_column('planned_workouts', sa.Column('updated_at', sa.DateTime(), nullable=True))

    # Backfill existing rows so no NULLs remain for created_at / updated_at.
    op.execute("UPDATE planned_workouts SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    op.execute("UPDATE planned_workouts SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")

    # RunningProfile: new trend direction columns (nullable — populated by next update_running_profile run).
    op.add_column('running_profiles', sa.Column('hr_drift_trend', sa.Text(), nullable=True))
    op.add_column('running_profiles', sa.Column('hr_pace_decoupling_trend', sa.Text(), nullable=True))
    op.add_column('running_profiles', sa.Column('easy_hr_zone_compliance_trend', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('running_profiles', 'easy_hr_zone_compliance_trend')
    op.drop_column('running_profiles', 'hr_pace_decoupling_trend')
    op.drop_column('running_profiles', 'hr_drift_trend')
    op.drop_column('planned_workouts', 'updated_at')
    op.drop_column('planned_workouts', 'created_at')
    op.drop_column('planned_workouts', 'last_garmin_synced_at')
