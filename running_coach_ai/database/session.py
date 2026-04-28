"""Database session factory and helpers."""

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from running_coach_ai.config import settings

if settings.DB_PATH:
    db_dir = os.path.dirname(settings.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    if settings.GARMIN_SESSION_DIR:
        os.makedirs(settings.GARMIN_SESSION_DIR, exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionFactory = sessionmaker(bind=engine)


@contextmanager
def get_session() -> Session:
    """Yield a DB session that auto-closes on exit."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def scoped_query(db_session: Session, model, athlete_id: int):
    """Return a base query for a model filtered by athlete_id.

    Enforces FR-001: every query operating on behalf of an athlete MUST
    be scoped to that athlete's ID. Use this helper instead of bare
    db_session.query(Model) for all athlete-scoped lookups.

    Example:
        workouts = scoped_query(db_session, PlannedWorkout, athlete.id).all()
    """
    return db_session.query(model).filter(model.athlete_id == athlete_id)
