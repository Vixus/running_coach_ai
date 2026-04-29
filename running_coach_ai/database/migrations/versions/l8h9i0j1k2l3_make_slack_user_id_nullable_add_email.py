"""make_slack_user_id_nullable_add_email

Revision ID: l8h9i0j1k2l3
Revises: k7g8h9i0j1k2
Create Date: 2026-04-29

Foundation for the web-only transition. Athletes can exist without a Slack ID,
and email becomes the new identity column. Existing rows are preserved; we
backfill `email` from `web_username` only when web_username already looks like
an email address.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'l8h9i0j1k2l3'
down_revision: Union[str, None] = 'k7g8h9i0j1k2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('athletes') as batch_op:
        batch_op.alter_column('slack_user_id', existing_type=sa.Text(), nullable=True)
        batch_op.add_column(sa.Column('email', sa.Text(), nullable=True))
        batch_op.create_unique_constraint('uq_athletes_email', ['email'])

    op.execute("""
        UPDATE athletes
           SET email = web_username
         WHERE web_username IS NOT NULL
           AND instr(web_username, '@') > 0
           AND email IS NULL
    """)


def downgrade() -> None:
    with op.batch_alter_table('athletes') as batch_op:
        batch_op.drop_constraint('uq_athletes_email', type_='unique')
        batch_op.drop_column('email')
        batch_op.alter_column('slack_user_id', existing_type=sa.Text(), nullable=False)
