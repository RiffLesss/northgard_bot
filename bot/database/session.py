from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from bot.config import Settings


engine: AsyncEngine | None = None
session_factory: async_sessionmaker[AsyncSession] | None = None


def init_database(settings: Settings) -> None:
    global engine, session_factory
    if not settings.database_url:
        return

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    if session_factory is None:
        raise RuntimeError("Database is not configured")

    async with session_factory() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if session_factory is None:
        raise RuntimeError("Database is not configured")
    return session_factory


def is_database_configured() -> bool:
    return session_factory is not None
