"""add_last_morning_checkin_date_to_athletes

Revision ID: 5bb958015cfa
Revises: c8111922664b
Create Date: 2026-04-04 18:48:17.011143

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5bb958015cfa'
down_revision: Union[str, None] = 'c8111922664b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('athletes', sa.Column('last_morning_checkin_date', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('athletes', 'last_morning_checkin_date')
