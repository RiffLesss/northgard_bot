import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.database.session import get_session_factory, is_database_configured
from bot.services.blacklist_service import BlacklistService


BLACKLIST_CHANNEL_ID = 1520442310423347210


def user_label(user) -> str:
    return user.nickname or str(user.discord_id)


async def ensure_blacklist_channel(interaction: discord.Interaction) -> bool:
    if interaction.channel_id == BLACKLIST_CHANNEL_ID:
        return True
    await interaction.response.send_message(
        f"Blacklist-команды доступны только в канале <#{BLACKLIST_CHANNEL_ID}>.",
        ephemeral=True,
    )
    return False


def register(bot: commands.Bot, settings: Settings) -> None:
    @bot.tree.command(name="blacklist_add", description="Добавить игрока в личный blacklist")
    @app_commands.describe(player="Игрок, с которым ты не хочешь попадаться")
    async def blacklist_add(interaction: discord.Interaction, player: discord.Member) -> None:
        if not await ensure_blacklist_channel(interaction):
            return
        if not is_database_configured():
            await interaction.response.send_message("База данных не настроена.", ephemeral=True)
            return

        session_factory = get_session_factory()
        async with session_factory() as session:
            service = BlacklistService(session)
            try:
                result = await service.add(interaction.user.id, player.id)
            except ValueError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return

        if result.changed:
            text = f"`{user_label(result.target)}` добавлен в твой blacklist."
        else:
            text = f"`{user_label(result.target)}` уже находится в твоем blacklist."
        await interaction.response.send_message(text, ephemeral=True)

    @bot.tree.command(name="blacklist_remove", description="Убрать игрока из личного blacklist")
    @app_commands.describe(player="Игрок, которого нужно убрать из blacklist")
    async def blacklist_remove(interaction: discord.Interaction, player: discord.Member) -> None:
        if not await ensure_blacklist_channel(interaction):
            return
        if not is_database_configured():
            await interaction.response.send_message("База данных не настроена.", ephemeral=True)
            return

        session_factory = get_session_factory()
        async with session_factory() as session:
            service = BlacklistService(session)
            try:
                result = await service.remove(interaction.user.id, player.id)
            except ValueError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return

        if result.changed:
            text = f"`{user_label(result.target)}` удален из твоего blacklist."
        else:
            text = f"`{user_label(result.target)}` не был в твоем blacklist."
        await interaction.response.send_message(text, ephemeral=True)

    @bot.tree.command(name="blacklist", description="Показать твой личный blacklist")
    async def blacklist_list(interaction: discord.Interaction) -> None:
        if not await ensure_blacklist_channel(interaction):
            return
        if not is_database_configured():
            await interaction.response.send_message("База данных не настроена.", ephemeral=True)
            return

        session_factory = get_session_factory()
        async with session_factory() as session:
            service = BlacklistService(session)
            try:
                users = await service.list_for_player(interaction.user.id)
            except ValueError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return

        if not users:
            await interaction.response.send_message("Твой blacklist пуст.", ephemeral=True)
            return

        lines = [f"{index}. `{user_label(user)}`" for index, user in enumerate(users, start=1)]
        await interaction.response.send_message("Твой blacklist:\n" + "\n".join(lines), ephemeral=True)
