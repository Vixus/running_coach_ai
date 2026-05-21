"""Add morning_snapshot_date to notifications.

Records which day's health snapshot fed a morning check-in body so the
14:00 health-backfill job (and later morning ticks) can detect stale-
based notifications and re-fire the check-in in place.

Revision ID: r4n5o6p7q8r9
Revises: q3m4n5o6p7q8
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa

revision = "r4n5o6p7q8r9"
down_revision = "q3m4n5o6p7q8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "notifications",
        sa.Column("morning_snapshot_date", sa.Date(), nullable=True),
    )


def downgrade():
    op.drop_column("notifications", "morning_snapshot_date")
