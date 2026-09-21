"""Database engine and sessions.

Every part of the app that talks to Postgres goes through `get_session()`, so
connection handling lives in exactly one place.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # drop connections that died while idle instead of failing a query
    future=True,
)

SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session() -> Iterator[Session]:
    """Give out a session, commit on success, roll back on error, always close.

    Usage:
        with get_session() as session:
            session.add(row)
    """
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
