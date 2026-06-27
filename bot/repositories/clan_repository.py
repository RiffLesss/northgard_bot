from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.clan import Clan


class ClanRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_name(self, name: str) -> Clan | None:
        return await self.session.scalar(select(Clan).where(Clan.name == name))

    async def list_all(self) -> list[Clan]:
        result = await self.session.scalars(select(Clan).order_by(Clan.sort_order, Clan.name))
        return list(result)

    async def list_enabled(self) -> list[Clan]:
        result = await self.session.scalars(
            select(Clan).where(Clan.is_enabled.is_(True)).order_by(Clan.sort_order, Clan.name)
        )
        return list(result)

    async def add(self, name: str) -> Clan:
        clan = Clan(name=name)
        self.session.add(clan)
        await self.session.flush()
        return clan

    async def enabled_names(self) -> list[str]:
        return [clan.name for clan in await self.list_enabled()]

    async def clear_names(self) -> list[str]:
        result = await self.session.scalars(
            select(Clan.name)
            .where(Clan.is_enabled.is_(True), Clan.is_clear.is_(True))
            .order_by(Clan.sort_order, Clan.name)
        )
        return list(result)

    async def kingdom_names(self) -> list[str]:
        result = await self.session.scalars(
            select(Clan.name)
            .where(Clan.is_enabled.is_(True), Clan.is_kingdom.is_(True))
            .order_by(Clan.sort_order, Clan.name)
        )
        return list(result)
