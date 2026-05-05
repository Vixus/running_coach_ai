"""drop_slack_columns

Revision ID: o1k2l3m4n5o6
Revises: n0j1k2l3m4n5
Create Date: 2026-05-04

Drop the legacy Slack columns now that the app is fully web-native.
Columns retained as nullable historical record since Phase 6; this
migration removes them after ~30 days of confirmed stable web-only ops.

athletes: slack_user_id, slack_dm_channel_id
conversation_messages: slack_ts
"""

from alembic import op
import sqlalchemy as sa

revision = 'o1k2l3m4n5o6'
down_revision = 'n0j1k2l3m4n5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('athletes') as batch:
        batch.drop_column('slack_user_id')
        batch.drop_column('slack_dm_channel_id')

    with op.batch_alter_table('conversation_messages') as batch:
        batch.drop_column('slack_ts')


def downgrade():
    with op.batch_alter_table('athletes') as batch:
        batch.add_column(sa.Column('slack_dm_channel_id', sa.Text(), nullable=True))
        batch.add_column(sa.Column('slack_user_id', sa.Text(), nullable=True))

    with op.batch_alter_table('conversation_messages') as batch:
        batch.add_column(sa.Column('slack_ts', sa.Text(), nullable=True))
