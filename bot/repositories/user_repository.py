from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_discord_id(self, discord_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.discord_id == discord_id))

    async def get_by_steam_id(self, steam_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.steam_id == steam_id))

    async def add(self, discord_id: int, steam_id: int, nickname: str | None = None) -> User:
        user = User(discord_id=discord_id, steam_id=steam_id, nickname=nickname)
        self.session.add(user)
        await self.session.flush()
        return user
