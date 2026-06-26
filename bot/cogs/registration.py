import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.database.session import get_session_factory, is_database_configured
from bot.services.user_service import STEAM_ID_HELP, UserService


def register(bot: commands.Bot, settings: Settings) -> None:
    @bot.tree.command(name="register", description="Зарегистрироваться в Northgard bot")
    @app_commands.describe(
        steam_id="SteamID64: 17 цифр, обычно начинается с 7656119",
        nickname="Твой игровой ник",
    )
    async def register_command(
        interaction: discord.Interaction,
        steam_id: str,
        nickname: str,
    ) -> None:
        if not is_database_configured():
            await interaction.response.send_message(
                "База данных не настроена. Проверь DATABASE_URL и миграции.",
                ephemeral=True,
            )
            return

        session_factory = get_session_factory()
        async with session_factory() as session:
            service = UserService(session)
            try:
                result = await service.register(
                    discord_id=interaction.user.id,
                    steam_id_raw=steam_id,
                    nickname=nickname,
                )
            except ValueError as error:
                await interaction.response.send_message(
                    f"{error}\n\n{STEAM_ID_HELP}",
                    ephemeral=True,
                )
                return

        action = "Регистрация завершена" if result.created else "Регистрация обновлена"
        await interaction.response.send_message(
            f"{action}.\n"
            f"Discord: {interaction.user.mention}\n"
            f"SteamID64: `{result.user.steam_id}`\n"
            f"Nickname: `{result.user.nickname}`\n\n"
            f"{STEAM_ID_HELP}",
            ephemeral=True,
        )
