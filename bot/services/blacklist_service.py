from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.user import User
from bot.repositories.blacklist_repository import BlacklistRepository
from bot.repositories.user_repository import UserRepository


@dataclass(frozen=True)
class BlacklistOperationResult:
    target: User
    changed: bool


class BlacklistService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.users = UserRepository(session)
        self.blacklist = BlacklistRepository(session)

    async def add(self, player_discord_id: int, target_discord_id: int) -> BlacklistOperationResult:
        player, target = await self.get_pair(player_discord_id, target_discord_id)
        if await self.blacklist.exists(player.id, target.id):
            return BlacklistOperationResult(target=target, changed=False)

        await self.blacklist.add(player.id, target.id)
        await self.session.commit()
        return BlacklistOperationResult(target=target, changed=True)

    async def remove(self, player_discord_id: int, target_discord_id: int) -> BlacklistOperationResult:
        player, target = await self.get_pair(player_discord_id, target_discord_id)
        removed = await self.blacklist.remove(player.id, target.id)
        if removed:
            await self.session.commit()
        return BlacklistOperationResult(target=target, changed=removed)

    async def list_for_player(self, player_discord_id: int) -> list[User]:
        player = await self.users.get_by_discord_id(player_discord_id)
        if player is None:
            raise ValueError("Сначала нужно зарегистрироваться через /register.")
        return await self.blacklist.list_for_player(player.id)

    async def get_pair(self, player_discord_id: int, target_discord_id: int) -> tuple[User, User]:
        if player_discord_id == target_discord_id:
            raise ValueError("Нельзя добавить в blacklist самого себя.")

        player = await self.users.get_by_discord_id(player_discord_id)
        if player is None:
            raise ValueError("Сначала нужно зарегистрироваться через /register.")

        target = await self.users.get_by_discord_id(target_discord_id)
        if target is None:
            raise ValueError("Этот игрок еще не зарегистрирован в боте.")

        return player, target
