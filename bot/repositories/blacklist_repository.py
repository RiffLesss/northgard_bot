from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.blacklist import PlayerBlacklist
from bot.models.user import User


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

    async def exists(self, player_id: int, blacklisted_player_id: int) -> bool:
        entry_id = await self.session.scalar(
            select(PlayerBlacklist.id).where(
                PlayerBlacklist.player_id == player_id,
                PlayerBlacklist.blacklisted_player_id == blacklisted_player_id,
            )
        )
        return entry_id is not None

    async def remove(self, player_id: int, blacklisted_player_id: int) -> bool:
        result = await self.session.execute(
            delete(PlayerBlacklist).where(
                PlayerBlacklist.player_id == player_id,
                PlayerBlacklist.blacklisted_player_id == blacklisted_player_id,
            )
        )
        return result.rowcount > 0

    async def list_for_player(self, player_id: int) -> list[User]:
        result = await self.session.scalars(
            select(User)
            .join(PlayerBlacklist, PlayerBlacklist.blacklisted_player_id == User.id)
            .where(PlayerBlacklist.player_id == player_id)
            .order_by(User.nickname, User.discord_id)
        )
        return list(result)

    async def list_pairs_for_players(self, player_ids: list[int]) -> set[tuple[int, int]]:
        if not player_ids:
            return set()
        result = await self.session.execute(
            select(PlayerBlacklist.player_id, PlayerBlacklist.blacklisted_player_id).where(
                PlayerBlacklist.player_id.in_(player_ids),
                PlayerBlacklist.blacklisted_player_id.in_(player_ids),
            )
        )
        return {(player_id, blacklisted_player_id) for player_id, blacklisted_player_id in result.all()}
