"""add_lthr_bpm_to_athletes

Revision ID: 723d7be1dd42
Revises: a1f3c9d2e8b7
Create Date: 2026-04-05 18:22:14.623837

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '723d7be1dd42'
down_revision: Union[str, None] = 'a1f3c9d2e8b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('athletes', sa.Column('lthr_bpm', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('athletes', 'lthr_bpm')
