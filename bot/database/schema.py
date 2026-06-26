from sqlalchemy.ext.asyncio import AsyncEngine

from bot.models import Base  # noqa: F401


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
