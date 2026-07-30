"""
db/database.py
Engine + scoped session setup, with SQLite pragmas applied per-connection
(WAL mode, foreign_keys=ON, synchronous=NORMAL) so the scheduler thread can
write while Flask request threads read.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from config import settings
from core.models import Base

engine: Engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
SessionLocal = scoped_session(SessionFactory)


def init_db() -> None:
    """Creates all tables that don't already exist. Call once at startup."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context manager yielding a Session. Commits on clean exit, rolls back
    and re-raises on exception. Always removes the scoped session afterwards
    so the next caller (possibly on a different thread) gets a fresh one.

    Usage:
        with get_session() as session:
            plot = session.get(FarmPlot, plot_id)
            plot.water_deficit_mm = 0.0
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        SessionLocal.remove()
