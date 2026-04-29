"""add_notifications

Revision ID: m9i0j1k2l3m4
Revises: l8h9i0j1k2l3
Create Date: 2026-04-29

In-app inbox for delivery-agnostic notifications: morning check-in, post-run
feedback, weekly review, system. The web app polls these; existing Slack DMs
keep flowing in parallel until Phase 6.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'm9i0j1k2l3m4'
down_revision: Union[str, None] = 'l8h9i0j1k2l3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'notifications' not in inspector.get_table_names():
        op.create_table(
            'notifications',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('athlete_id', sa.Integer(), nullable=False),
            sa.Column('kind', sa.Text(), nullable=False),
            sa.Column('title', sa.Text(), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('action_path', sa.Text(), nullable=True),
            sa.Column('related_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('read_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['athlete_id'], ['athletes.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_notifications_athlete_created', 'notifications', ['athlete_id', 'created_at'])
        op.create_index('ix_notifications_athlete_unread', 'notifications', ['athlete_id', 'read_at'])


def downgrade() -> None:
    op.drop_index('ix_notifications_athlete_unread', table_name='notifications')
    op.drop_index('ix_notifications_athlete_created', table_name='notifications')
    op.drop_table('notifications')
