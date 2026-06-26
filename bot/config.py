import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    discord_bot_token: str
    discord_guild_id: int | None
    allowed_channel_id: int | None
    bot_admins_file: Path
    database_url: str | None


def _optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if not value:
        return None
    return int(value)


def load_settings() -> Settings:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("Environment variable DISCORD_BOT_TOKEN is not set")
    database_url = os.getenv("DATABASE_URL")
    if database_url and database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    return Settings(
        discord_bot_token=token,
        discord_guild_id=_optional_int("DISCORD_GUILD_ID"),
        allowed_channel_id=_optional_int("ALLOWED_CHANNEL_ID"),
        bot_admins_file=Path(os.getenv("BOT_ADMINS_FILE", "data/bot_admins.json")),
        database_url=database_url,
    )
