import discord
import asyncio
import os
import sys
import time
from discord import app_commands
from discord.ext import commands
from sqlalchemy import text

from bot.database.session import get_session_factory, is_database_configured
from bot.config import Settings
from bot.services.draft_service import BOT_ADMIN_IDS, add_bot_admin, is_admin, is_master_admin


def register(bot: commands.Bot, settings: Settings) -> None:
    if not hasattr(bot, "started_at"):
        bot.started_at = time.monotonic()

    @bot.tree.command(name="healthcheck", description="Show current bot health")
    async def healthcheck(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("Only a bot admin can use this command.", ephemeral=True)
            return
        db_status = "not configured"
        if is_database_configured():
            try:
                async with get_session_factory()() as session:
                    await session.execute(text("SELECT 1"))
                db_status = "ok"
            except Exception:
                db_status = "error"
        from bot.cogs.scrim import active_scrim_channels
        from bot.cogs.team3 import active_team3_players, casual_lobbies, ranked_queue
        from bot.services.draft_service import active_drafts

        uptime = int(time.monotonic() - bot.started_at)
        await interaction.response.send_message(
            f"**Bot:** {'ready' if bot.is_ready() else 'not ready'}\n"
            f"**Latency:** {round(bot.latency * 1000)} ms\n"
            f"**Database:** {db_status}\n"
            f"**Uptime:** {uptime // 3600:02d}:{uptime % 3600 // 60:02d}:{uptime % 60:02d}\n"
            f"**2v2 drafts:** {len(active_drafts)}\n"
            f"**3v3 queue:** {len(ranked_queue)} ranked, {sum(len(groups) for groups in casual_lobbies.values())} casual\n"
            f"**3v3 active players:** {len(active_team3_players)}\n"
            f"**NSL scrims:** {len(active_scrim_channels)}",
            ephemeral=True,
        )

    @bot.tree.command(name="refresh", description="Gracefully reconnect the bot")
    async def refresh(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("Only a bot admin can use this command.", ephemeral=True)
            return
        await interaction.response.send_message(
            "The bot is shutting down gracefully. A process supervisor should start it again; all workflow state is in the database.",
            ephemeral=True,
        )
        await asyncio.sleep(1)
        await bot.close()
        os.execv(sys.executable, [sys.executable, *sys.argv])

    @bot.tree.command(name="add_admin", description="Добавить админа бота")
    @app_commands.describe(user="Пользователь, которого нужно сделать админом бота")
    async def add_admin_command(interaction: discord.Interaction, user: discord.Member) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_master_admin(interaction.user):
            await interaction.response.send_message("Только мастер-админ может добавлять админов бота.", ephemeral=True)
            return

        if user.id in BOT_ADMIN_IDS:
            await interaction.response.send_message(f"{user.mention} уже является админом бота.", ephemeral=True)
            return

        await add_bot_admin(user.id)
        await interaction.response.send_message(f"{user.mention} добавлен в админы бота.")
