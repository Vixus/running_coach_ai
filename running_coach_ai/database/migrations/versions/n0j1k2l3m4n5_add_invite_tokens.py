"""add_invite_tokens

Revision ID: n0j1k2l3m4n5
Revises: m9i0j1k2l3m4
Create Date: 2026-04-29

Invite tokens for self-serve web signup. The admin generates a token via
the Phase 4 admin UI and shares the signup URL out-of-band; the receiver
uses it once to create their athlete account.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'n0j1k2l3m4n5'
down_revision: Union[str, None] = 'm9i0j1k2l3m4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'invite_tokens' in inspector.get_table_names():
        return
    op.create_table(
        'invite_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token', sa.Text(), nullable=False),
        sa.Column('email_hint', sa.Text(), nullable=True),
        sa.Column('created_by_athlete_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('used_by_athlete_id', sa.Integer(), nullable=True),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_athlete_id'], ['athletes.id']),
        sa.ForeignKeyConstraint(['used_by_athlete_id'], ['athletes.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token', name='uq_invite_tokens_token'),
    )


def downgrade() -> None:
    op.drop_table('invite_tokens')
