"""Database setup — async engine aur session.

Async isliye ki app khud async hai aur har verification me engine ko HTTP call
jaati hai. Sync driver use karte to wo call event loop ko block karti.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, TypeDecorator, event
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.pool import StaticPool

from .config import settings

# Constraint names tay hone se Alembic migrations saaf rehti hain — warna
# har DB apne naam banata hai aur baad me drop/alter karna mushkil ho jaata.
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
    """Har timestamp UTC me jaata hai aur UTC me hi wapas aata hai.

    Iski zaroorat isliye hai ki SQLite timezone rakhta hi nahi — wo naive
    datetime lautata hai, jabki Postgres aware. Bina iske code dev me chal
    jaata hai aur prod me `can't compare offset-naive and offset-aware`
    pe girta hai (ya ulta). Ye dono ko ek jaisa bana deta hai.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            # Naive aaya to UTC maan lo — poore code me hum UTC hi likhte hain
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
        # SQLite ke sath: file lock ke liye ek hi connection, aur in-memory
        # (tests) me har session ko wahi connection milna chahiye.
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
    """SQLite ko theek se chalane ke liye — WAL aur foreign keys.

    Foreign keys SQLite me default OFF hoti hain; bina iske cascade aur
    referential integrity chup-chaap kaam nahi karte.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — request ke saath session khulta aur band hota hai."""
    async with SessionLocal() as session:
        yield session


async def create_all() -> None:
    """Sirf dev/test ke liye. Production me Alembic migrations chalti hain."""
    from . import models  # noqa: F401  — models import hone chahiye tables ke liye
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
