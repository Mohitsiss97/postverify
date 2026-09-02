"""Database setup: the async engine and session factory.

Async throughout, because the application is async and every verification makes
an HTTP call to the engine. A synchronous driver would block the event loop for
the whole duration of that call.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, TypeDecorator, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.pool import StaticPool

from .config import settings

# Fixing the constraint naming keeps Alembic migrations clean. Without it each
# database invents its own names, and dropping or altering a constraint later
# becomes guesswork.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """Every timestamp is stored as UTC and comes back as UTC.

    This exists because SQLite does not store timezones at all and returns naive
    datetimes, while PostgreSQL returns aware ones. Without it the code works in
    development and fails in production with "can't compare offset-naive and
    offset-aware datetimes", or the reverse. This makes both behave alike.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            # A naive value is assumed to be UTC; this codebase only writes UTC.
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def timestamp_column(**kwargs):
    return mapped_column(UtcDateTime(), **kwargs)


def _make_engine(url: str):
    kwargs: dict = {"echo": settings.db_echo, "future": True}
    if url.startswith("sqlite"):
        # SQLite specifics: a single connection because of file locking, and
        # for an in-memory database (tests) every session must share it.
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)
    return create_async_engine(url, **kwargs)


engine = _make_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False,
                                  class_=AsyncSession)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """The settings SQLite needs to behave correctly: WAL and foreign keys.

    Foreign keys are OFF by default in SQLite, so without this both cascades and
    referential integrity fail silently.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a session opened and closed with the request."""
    async with SessionLocal() as session:
        yield session


async def create_all() -> None:
    """Development and tests only. Production schema changes go through Alembic."""
    from . import models  # noqa: F401  — the import is what registers the tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
