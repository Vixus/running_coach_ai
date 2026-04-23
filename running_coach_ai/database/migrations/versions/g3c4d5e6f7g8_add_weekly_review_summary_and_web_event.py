"""add_weekly_review_summary_and_web_event

Revision ID: g3c4d5e6f7g8
Revises: f2b3c4d5e6f7
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g3c4d5e6f7g8'
down_revision: Union[str, None] = 'f2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'weekly_review_summaries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('athlete_id', sa.Integer(), sa.ForeignKey('athletes.id'), nullable=False),
        sa.Column('week_start_date', sa.Date(), nullable=False),
        sa.Column('total_miles', sa.Float(), nullable=True),
        sa.Column('elevation_gain_ft', sa.Float(), nullable=True),
        sa.Column('avg_hrv', sa.Float(), nullable=True),
        sa.Column('total_tss', sa.Float(), nullable=True),
        sa.Column('narrative', sa.Text(), nullable=False),
        sa.Column('daily_volume_json', sa.JSON(), nullable=True),
        sa.Column('body_battery_json', sa.JSON(), nullable=True),
        sa.Column('next_week_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('athlete_id', 'week_start_date', name='uq_weekly_review_athlete_week'),
    )

    op.create_table(
        'web_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('athlete_id', sa.Integer(), sa.ForeignKey('athletes.id'), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('severity', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('details_json', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_web_events_timestamp', 'web_events', ['timestamp'])
    op.create_index('ix_web_events_category_timestamp', 'web_events', ['category', 'timestamp'])


def downgrade() -> None:
    op.drop_index('ix_web_events_category_timestamp', table_name='web_events')
    op.drop_index('ix_web_events_timestamp', table_name='web_events')
    op.drop_table('web_events')
    op.drop_table('weekly_review_summaries')
