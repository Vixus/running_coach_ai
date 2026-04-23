"""add_target_duration_seconds_to_planned_workouts

Revision ID: i5e6f7g8h9i0
Revises: h4d5e6f7g8h9
Create Date: 2026-04-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'i5e6f7g8h9i0'
down_revision: Union[str, None] = 'h4d5e6f7g8h9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('planned_workouts', sa.Column('target_duration_seconds', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('planned_workouts', 'target_duration_seconds')
