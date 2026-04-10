"""add training_readiness to health_snapshots

Revision ID: d4e7f2a1b9c3
Revises: 1f11b24c1fdf
Create Date: 2026-04-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e7f2a1b9c3'
down_revision: Union[str, None] = '1f11b24c1fdf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('health_snapshots', sa.Column('training_readiness', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('health_snapshots', 'training_readiness')
