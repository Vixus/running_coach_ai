"""add_run_feedback

Revision ID: f2b3c4d5e6f7
Revises: e1a2b3c4d5e6
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2b3c4d5e6f7'
down_revision: Union[str, None] = 'e1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('completed_workouts', sa.Column('coach_analysis', sa.Text(), nullable=True))

    op.create_table(
        'run_feedback',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('athlete_id', sa.Integer(), sa.ForeignKey('athletes.id'), nullable=False),
        sa.Column('completed_workout_id', sa.Integer(), sa.ForeignKey('completed_workouts.id'), nullable=False),
        sa.Column('feel_score', sa.Integer(), nullable=True),
        sa.Column('rpe', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('completed_workout_id'),
    )
    op.create_index('ix_run_feedback_athlete_workout', 'run_feedback', ['athlete_id', 'completed_workout_id'])


def downgrade() -> None:
    op.drop_index('ix_run_feedback_athlete_workout', table_name='run_feedback')
    op.drop_table('run_feedback')
    op.drop_column('completed_workouts', 'coach_analysis')
