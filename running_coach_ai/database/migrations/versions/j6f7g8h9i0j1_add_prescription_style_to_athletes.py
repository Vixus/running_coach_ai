"""add_prescription_style_to_athletes

Revision ID: j6f7g8h9i0j1
Revises: i5e6f7g8h9i0
Create Date: 2026-04-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'j6f7g8h9i0j1'
down_revision: Union[str, None] = 'f6bd317f2a14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('athletes', sa.Column('prescription_style', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('athletes', 'prescription_style')
