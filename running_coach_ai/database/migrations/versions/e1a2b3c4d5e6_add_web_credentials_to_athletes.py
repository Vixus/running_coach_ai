"""add_web_credentials_to_athletes

Revision ID: e1a2b3c4d5e6
Revises: 1f11b24c1fdf
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1a2b3c4d5e6'
down_revision: Union[str, None] = '1f11b24c1fdf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('athletes', sa.Column('web_username', sa.Text(), nullable=True))
    op.add_column('athletes', sa.Column('web_password_hash', sa.Text(), nullable=True))
    op.add_column('athletes', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('athletes', 'is_admin')
    op.drop_column('athletes', 'web_password_hash')
    op.drop_column('athletes', 'web_username')
