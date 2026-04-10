"""Temporary diagnostic script."""
import sqlalchemy as sa
from running_coach_ai.database.session import get_session

with get_session() as db:
    version = db.execute(sa.text("SELECT version_num FROM alembic_version")).fetchall()
    print("alembic version:", version)
    cols = db.execute(sa.text("PRAGMA table_info(athletes)")).fetchall()
    print("athletes cols:", [c[1] for c in cols])
