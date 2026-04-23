"""merge_heads

Revision ID: f6bd317f2a14
Revises: d4e7f2a1b9c3, i5e6f7g8h9i0
Create Date: 2026-04-21 07:06:22.386351

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6bd317f2a14'
down_revision: Union[str, None] = ('d4e7f2a1b9c3', 'i5e6f7g8h9i0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
