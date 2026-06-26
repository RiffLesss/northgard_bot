from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.user import BotAdmin


class AdminRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, user_id: int) -> None:
        self.session.add(BotAdmin(user_id=user_id))
        await self.session.commit()

    async def exists(self, user_id: int) -> bool:
        result = await self.session.scalar(select(BotAdmin.user_id).where(BotAdmin.user_id == user_id))
        return result is not None

    async def list_ids(self) -> list[int]:
        result = await self.session.scalars(select(BotAdmin.user_id))
        return list(result)
