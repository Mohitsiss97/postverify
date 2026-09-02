"""Alembic environment.

The database URL comes from the application settings rather than from
alembic.ini, so development and production share one source of truth.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app import models  # noqa: F401  — the import is what registers the tables
from app.config import settings
from app.db import Base, UtcDateTime

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def render_item(type_, obj, autogen_context):
    """Render UtcDateTime as a plain timestamp in migrations.

    It is a Python-side TypeDecorator; in the database it is simply TIMESTAMP
    WITH TIME ZONE. Writing the application class into a migration would cause
    two problems: the migration file would depend on application code, and the
    import it needs is not emitted automatically.
    """
    if type_ == "type" and isinstance(obj, UtcDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def do_run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_item=render_item,
        # SQLite's ALTER TABLE is very limited; batch mode works around it by
        # rebuilding the table.
        render_as_batch=settings.database_url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async())
