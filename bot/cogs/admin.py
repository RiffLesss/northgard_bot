import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.draft_service import BOT_ADMIN_IDS, add_bot_admin, is_master_admin


def register(bot: commands.Bot, settings: Settings) -> None:
    @bot.tree.command(name="add_admin", description="Добавить админа бота")
    @app_commands.describe(user="Пользователь, которого нужно сделать админом бота")
    async def add_admin_command(interaction: discord.Interaction, user: discord.Member) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_master_admin(interaction.user):
            await interaction.response.send_message("Только мастер-админ может добавлять админов бота.", ephemeral=True)
            return

        if user.id in BOT_ADMIN_IDS:
            await interaction.response.send_message(f"{user.mention} уже является админом бота.", ephemeral=True)
            return

        add_bot_admin(user.id)
        await interaction.response.send_message(f"{user.mention} добавлен в админы бота.")
