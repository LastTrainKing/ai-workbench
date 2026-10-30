"""Async SQLAlchemy engine / session management.

A single engine is created per process; sessions are created per request or per
job and always closed.  ``expire_on_commit=False`` keeps ORM objects usable
after commit so service layer code can serialise them without extra queries.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,  # transparently drop dead connections
            pool_recycle=1800,
            connect_args={"timeout": 10},  # asyncpg connect timeout
        )
        logger.info(
            "database_engine_created",
            extra={"url": _redact(settings.database_url)},
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


def _redact(url: str) -> str:
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.split("@", 1)
    user = creds.split(":")[0]
    return f"{scheme}://{user}:***@{host}"


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("database_engine_disposed")


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope: commit on success, rollback on any exception."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Read-only paths may still commit (harmless)."""
    async with session_scope() as session:
        yield session


async def healthcheck() -> tuple[bool, str]:
    from sqlalchemy import text

    try:
        async with get_engine().connect() as conn:
            version = (await conn.execute(text("select version()"))).scalar_one()
        return True, str(version)
    except Exception as exc:
        logger.warning("database_healthcheck_failed", extra={"error": str(exc)})
        return False, f"{type(exc).__name__}: {exc}"
