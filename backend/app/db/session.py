from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import ensure_directories, settings


class Base(DeclarativeBase):
    pass


ensure_directories()
connect_args = {"check_same_thread": False} if settings.sqlite_url.startswith("sqlite") else {}
# pool_pre_ping revalidates pooled connections before use, so a database
# restart (upgrade, tuning change) costs one silent reconnect instead of a
# burst of user-facing 500s.
engine = create_engine(
    settings.sqlite_url,
    connect_args=connect_args,
    future=True,
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    if settings.sqlite_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

