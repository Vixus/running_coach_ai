"""add_pending_onboarding_data

Revision ID: 1f11b24c1fdf
Revises: b3f2a1c9d0e5
Create Date: 2026-04-06 01:06:46.517070

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f11b24c1fdf'
down_revision: Union[str, None] = 'b3f2a1c9d0e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('athletes', sa.Column('pending_onboarding_data', sa.JSON(), nullable=True))
    op.add_column('athletes', sa.Column('pending_onboarding_data_created_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('athletes', 'pending_onboarding_data_created_at')
    op.drop_column('athletes', 'pending_onboarding_data')
