"""add_source_to_conversation_messages

Revision ID: h4d5e6f7g8h9
Revises: g3c4d5e6f7g8
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'h4d5e6f7g8h9'
down_revision: Union[str, None] = 'g3c4d5e6f7g8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('conversation_messages', sa.Column('source', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('conversation_messages', 'source')
