from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.user import User
from bot.repositories.user_repository import UserRepository


STEAM_ID_HELP = (
    "SteamID нужен в формате SteamID64: 17 цифр, обычно начинается с `7656119`.\n"
    "Как найти: открой Steam -> профиль -> скопируй ссылку профиля и вставь ее на "
    "https://steamid.io/lookup или https://steamidfinder.com/ . Нужное поле: `steamID64`."
)


@dataclass(frozen=True)
class RegistrationResult:
    user: User
    created: bool


def parse_steam_id(value: str) -> int:
    cleaned = value.strip()
    if not cleaned.isdigit():
        raise ValueError("SteamID должен состоять только из цифр.")
    if not 15 <= len(cleaned) <= 20:
        raise ValueError("SteamID выглядит неверно. Обычно SteamID64 состоит из 17 цифр.")
    return int(cleaned)


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.users = UserRepository(session)

    async def register(self, discord_id: int, steam_id_raw: str, nickname: str) -> RegistrationResult:
        steam_id = parse_steam_id(steam_id_raw)
        nickname = nickname.strip()
        if not nickname:
            raise ValueError("Nickname не может быть пустым.")

        existing_by_steam = await self.users.get_by_steam_id(steam_id)
        if existing_by_steam is not None and existing_by_steam.discord_id != discord_id:
            raise ValueError("Этот SteamID уже привязан к другому Discord-пользователю.")

        existing_by_discord = await self.users.get_by_discord_id(discord_id)
        if existing_by_discord is not None:
            existing_by_discord.steam_id = steam_id
            existing_by_discord.nickname = nickname
            await self.session.commit()
            return RegistrationResult(user=existing_by_discord, created=False)

        user = await self.users.add(discord_id=discord_id, steam_id=steam_id, nickname=nickname)
        await self.session.commit()
        return RegistrationResult(user=user, created=True)
