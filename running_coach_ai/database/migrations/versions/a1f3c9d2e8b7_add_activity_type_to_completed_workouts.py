"""add_activity_type_to_completed_workouts

Revision ID: a1f3c9d2e8b7
Revises: c8111922664b
Create Date: 2026-04-05 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f3c9d2e8b7'
down_revision: Union[str, None] = '5bb958015cfa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('completed_workouts', sa.Column('activity_type', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('completed_workouts', 'activity_type')
