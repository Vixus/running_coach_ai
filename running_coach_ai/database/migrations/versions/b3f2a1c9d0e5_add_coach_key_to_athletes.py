"""add coach_key to athletes

Revision ID: b3f2a1c9d0e5
Revises: 723d7be1dd42
Create Date: 2026-04-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f2a1c9d0e5'
down_revision: Union[str, None] = '723d7be1dd42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('athletes', sa.Column('coach_key', sa.Text(), nullable=True))
    op.execute("UPDATE athletes SET coach_key = 'classic' WHERE coach_key IS NULL")


def downgrade() -> None:
    op.drop_column('athletes', 'coach_key')
