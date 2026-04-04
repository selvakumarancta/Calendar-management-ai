"""
SQLAlchemy database engine and session management.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all models."""

    pass


class Database:
    """Async database connection manager."""

    def __init__(self, database_url: str, echo: bool = False) -> None:
        engine_kwargs: dict = dict(echo=echo, pool_pre_ping=True)
        if database_url.startswith("sqlite"):
            # SQLite: use NullPool so each session gets its own connection
            # This prevents "database is locked" with concurrent async sessions
            from sqlalchemy.pool import StaticPool

            engine_kwargs["connect_args"] = {
                "timeout": 30,
                "check_same_thread": False,
            }
            engine_kwargs["poolclass"] = StaticPool
        else:
            engine_kwargs.update(pool_size=10, max_overflow=20)

        self.engine = create_async_engine(database_url, **engine_kwargs)

        # Enable WAL mode for SQLite (better concurrent access)
        if database_url.startswith("sqlite"):
            from sqlalchemy import event as sa_event

            @sa_event.listens_for(self.engine.sync_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, connection_record):  # type: ignore[no-untyped-def]
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def create_tables(self) -> None:
        """Create all tables (dev only — use Alembic in production)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def get_session(self) -> AsyncSession:
        """Create a new async session."""
        async with self.session_factory() as session:
            return session

    async def close(self) -> None:
        """Dispose of the engine."""
        await self.engine.dispose()
