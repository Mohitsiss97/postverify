"""Alembic environment.

DB URL app ke settings se aata hai, alembic.ini se nahi — taaki dev aur
production dono jagah wahi ek source of truth rahe.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from app.config import settings
from app.db import Base, UtcDateTime
from app import models  # noqa: F401  — tables register hone chahiye

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
    """UtcDateTime ko migration me plain timestamp likho.

    Wo Python-side TypeDecorator hai; database me wo bas TIMESTAMP WITH TIME
    ZONE hai. Migration me app ka class likhne se do dikkatein hoti: migration
    file app code pe depend kar jaati, aur import bhi khud nahi aata.
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
        # SQLite ALTER TABLE bahut seemit hai; batch mode usse table dobara
        # bana kar kaam chala leta hai.
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
