from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Base


@lru_cache
def get_engine() -> AsyncEngine:
    """Lazy, cached engine construction.

    Built from ``get_settings()`` at first *call* rather than at import
    time — importing this module (directly, or transitively through
    ``app.main`` / ``app.scheduler``) must never itself pin a database
    connection, otherwise whichever settings happen to be active at
    import time win regardless of what a caller configures afterwards
    (this bit tests hard: pytest imports every test module during
    collection, before any fixture has run). Tests that override
    ``DATABASE_URL`` should call ``get_engine.cache_clear()`` /
    ``get_session_maker.cache_clear()`` alongside ``get_settings.cache_clear()``.
    """
    settings = get_settings()
    return create_async_engine(settings.database_url, echo=False, future=True)


@lru_cache
def get_session_maker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    session_maker = get_session_maker()
    async with session_maker() as session:
        yield session
