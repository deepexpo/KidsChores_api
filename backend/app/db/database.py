"""Async SQLAlchemy engine and session factory."""

from collections.abc import AsyncGenerator, Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_debug,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Shared declarative base for all models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async DB session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def run_and_dispose(coro: Coroutine[Any, Any, None]) -> None:
    """
    Runs `coro`, then disposes the engine's connection pool. Celery task
    entry points only — each is invoked via its own asyncio.run() (a fresh
    event loop every call), but a long-lived worker process reuses this same
    module-level `engine` across every invocation. Without disposal, pooled
    asyncpg connections stay bound to the event loop that opened them, and
    the next invocation in that worker process crashes with "Future attached
    to a different loop". The FastAPI app never needs this — uvicorn runs one
    event loop for the app's whole lifetime, so its connections are always
    used on the loop that created them.
    """
    try:
        await coro
    finally:
        await engine.dispose()
