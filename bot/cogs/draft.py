import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.draft_service import DraftSession, active_drafts, is_admin


def register(bot: commands.Bot, settings: Settings) -> None:
    @bot.tree.command(name="start_draft_2v2", description="Запустить бан-пик Northgard 2v2")
    @app_commands.describe(
        player_1="Первый игрок",
        player_2="Второй игрок",
        bo="Формат матча",
    )
    @app_commands.choices(
        bo=[
            app_commands.Choice(name="bo1", value="bo1"),
            app_commands.Choice(name="bo3", value="bo3"),
            app_commands.Choice(name="bo5", value="bo5"),
        ]
    )
    async def start_draft_2v2(
        interaction: discord.Interaction,
        player_1: discord.Member,
        player_2: discord.Member,
        bo: app_commands.Choice[str],
    ) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("Только админ бота может запускать драфт.", ephemeral=True)
            return
        if player_1.id == player_2.id:
            await interaction.response.send_message("Нужно выбрать двух разных игроков.", ephemeral=True)
            return
        if interaction.channel is None:
            await interaction.response.send_message("Не удалось определить канал для драфта.", ephemeral=True)
            return
        if interaction.channel_id in active_drafts:
            await interaction.response.send_message("В этом канале уже идет драфт.", ephemeral=True)
            return

        session = DraftSession(bot, interaction.channel, player_1, player_2, bo.value)
        active_drafts[interaction.channel_id] = session
        await interaction.response.send_message(
            f"Драфт {bo.value} запущен: {player_1.mention} vs {player_2.mention}."
        )
        session.start_next_game()

    @bot.tree.command(name="stop_draft_2v2", description="Остановить текущий матч Northgard 2v2")
    async def stop_draft_2v2(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("Только админ бота может остановить драфт.", ephemeral=True)
            return
        if interaction.channel_id is None:
            await interaction.response.send_message("Не удалось определить канал драфта.", ephemeral=True)
            return

        session = active_drafts.get(interaction.channel_id)
        if session is None:
            await interaction.response.send_message("В этом канале нет активного драфта.", ephemeral=True)
            return

        await interaction.response.send_message("Останавливаю текущий драфт 2v2.")
        await session.stop()

    @bot.tree.command(name="restart_draft_2v2", description="Перезапустить драфт текущей игры Northgard 2v2")
    async def restart_draft_2v2(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("Только админ бота может перезапустить драфт.", ephemeral=True)
            return
        if interaction.channel_id is None:
            await interaction.response.send_message("Не удалось определить канал драфта.", ephemeral=True)
            return

        session = active_drafts.get(interaction.channel_id)
        if session is None:
            await interaction.response.send_message("В этом канале нет активного драфта.", ephemeral=True)
            return

        await interaction.response.send_message("Перезапускаю драфт текущей игры 2v2.")
        await session.restart_current_game_draft()

    async def on_message(message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        session = active_drafts.get(message.channel.id)
        if session is None:
            await bot.process_commands(message)
            return

        parts = message.content.strip().split()
        if len(parts) >= 2 and parts[0].lower() == "win":
            if not isinstance(message.author, discord.Member) or not is_admin(message.author):
                await message.reply("Только админ бота может записывать победителя.", mention_author=False)
                return
            if not message.mentions:
                await message.reply("Укажи победителя через упоминание: `win @player`.", mention_author=False)
                return

            await session.record_win(message.mentions[0])
            return

        await bot.process_commands(message)

    bot.add_listener(on_message, "on_message")
