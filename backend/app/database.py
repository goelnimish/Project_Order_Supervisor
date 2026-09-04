"""Asynchronous SQLAlchemy database foundation."""

import asyncio
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base metadata registry for future database models."""


async def get_session() -> AsyncIterator[AsyncSession]:
    """Provide an isolated asynchronous database session."""

    async with async_session_factory() as session:
        yield session


async def database_is_reachable() -> bool:
    """Check database connectivity without leaking connection details."""

    try:
        async with asyncio.timeout(3):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception:
        # This health boundary intentionally converts driver/network failures to False.
        return False

    return True
