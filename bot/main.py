import discord
from discord.ext import commands

from bot.cogs import admin, bear, draft, matchmaking, registration, team3
from bot.config import Settings, load_settings
from bot.database.session import init_database
from bot.services.draft_service import configure_admins_file, load_bot_admins


def create_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    admin.register(bot, settings)
    bear.register(bot, settings)
    draft.register(bot, settings)
    matchmaking.register(bot, settings)
    registration.register(bot, settings)
    team3.register(bot, settings)

    @bot.event
    async def on_ready() -> None:
        if settings.discord_guild_id:
            guild = discord.Object(id=settings.discord_guild_id)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} command(s) to guild {settings.discord_guild_id}")
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} global command(s)")

        print(f"Logged in as {bot.user}")

    return bot


def main() -> None:
    settings = load_settings()
    init_database(settings)
    configure_admins_file(settings.bot_admins_file)
    load_bot_admins()
    bot = create_bot(settings)
    bot.run(settings.discord_bot_token)


if __name__ == "__main__":
    main()
