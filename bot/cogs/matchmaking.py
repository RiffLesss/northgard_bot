import discord
from discord.ext import commands

from bot.config import Settings
from bot.services.team_service import format_schedule, generate_schedule


def register(bot: commands.Bot, settings: Settings) -> None:
    @bot.tree.command(name="shuffle_teams", description="Сгенерировать расписание раундов Northgard")
    async def shuffle_teams(interaction: discord.Interaction) -> None:
        if settings.allowed_channel_id and interaction.channel_id != settings.allowed_channel_id:
            await interaction.response.send_message(
                f"Команда доступна только в канале <#{settings.allowed_channel_id}>.",
                ephemeral=True,
            )
            return

        schedule = generate_schedule()
        await interaction.response.send_message(format_schedule(schedule, use_spoilers=True))
