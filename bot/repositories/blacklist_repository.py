from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.blacklist import PlayerBlacklist


class BlacklistRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, player_id: int, blacklisted_player_id: int) -> PlayerBlacklist:
        entry = PlayerBlacklist(
            player_id=player_id,
            blacklisted_player_id=blacklisted_player_id,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry
