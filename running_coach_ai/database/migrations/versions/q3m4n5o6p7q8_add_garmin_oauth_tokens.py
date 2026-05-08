"""Add garmin_oauth_tokens to athletes for DB-backed token storage.

Revision ID: q3m4n5o6p7q8
Revises: p2l3m4n5o6p7
Create Date: 2026-05-08
"""

from alembic import op
import sqlalchemy as sa

revision = "q3m4n5o6p7q8"
down_revision = "p2l3m4n5o6p7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("athletes", sa.Column("garmin_oauth_tokens", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("athletes", "garmin_oauth_tokens")
