"""Alembic migration environment."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.infrastructure.persistence.calendar_event_model import *  # noqa: F401,F403 — calendar events model
from src.infrastructure.persistence.config_model import *  # noqa: F401,F403 — config settings model
from src.infrastructure.persistence.database import Base
from src.infrastructure.persistence.email_models import *  # noqa: F401,F403 — email intelligence models
from src.infrastructure.persistence.models import *  # noqa: F401,F403 — import models for autogenerate
from src.infrastructure.persistence.org_models import *  # noqa: F401,F403 — org/multi-tenant models

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Allow DATABASE_URL env var to override alembic.ini value
_db_url = os.getenv("DATABASE_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


import asyncio

if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
