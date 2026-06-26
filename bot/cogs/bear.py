import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.database.session import get_session_factory, is_database_configured
from bot.models.bear import BearMatch
from bot.models.enums import BearChallengeStatus
from bot.models.user import User
from bot.services.bear_service import BEAR_ROLE_ID, BearService


BEAR_CHANNEL_ID = 1519833399085236304
BEAR_TIERLIST_CHANNEL_ID = 1519833554375278683
TIER_EMOJIS = {
    "Царь Медведь": "👑",
    "Короли Леса": "🌲",
    "Большие медведи": "🐻",
    "Средние медведи": "🪵",
    "Медвежата": "🧸",
}
PLACE_EMOJIS = {
    1: "🥇",
    2: "🥈",
    3: "🥉",
}
last_tierlist_message_ids: dict[int, list[int]] = {}


class AcceptChallengeView(discord.ui.View):
    def __init__(self, opponent_id: int):
        super().__init__(timeout=120)
        self.opponent_id = opponent_id
        self.accepted = False

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("Этот вызов адресован другому игроку.", ephemeral=True)
            return
        self.accepted = True
        await interaction.response.send_message("Вызов принят.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("Этот вызов адресован другому игроку.", ephemeral=True)
            return
        self.accepted = False
        await interaction.response.send_message("Вызов отклонен.", ephemeral=True)
        self.stop()


class WinnerConfirmView(discord.ui.View):
    def __init__(self, player1: discord.Member, player2: discord.Member):
        super().__init__(timeout=120)
        self.player1 = player1
        self.player2 = player2
        self.choices: dict[int, int] = {}

    async def choose(self, interaction: discord.Interaction, winner_id: int) -> None:
        if interaction.user.id not in {self.player1.id, self.player2.id}:
            await interaction.response.send_message("Вы не участвуете в этой дуэли.", ephemeral=True)
            return
        self.choices[interaction.user.id] = winner_id
        await interaction.response.send_message("Выбор принят.", ephemeral=True)
        if self.player1.id in self.choices and self.player2.id in self.choices:
            self.stop()

    @discord.ui.button(label="Победил игрок 1", style=discord.ButtonStyle.primary)
    async def choose_player1(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.choose(interaction, self.player1.id)

    @discord.ui.button(label="Победил игрок 2", style=discord.ButtonStyle.primary)
    async def choose_player2(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.choose(interaction, self.player2.id)

    def agreed_winner_id(self) -> int | None:
        if len(self.choices) < 2:
            return None
        values = set(self.choices.values())
        if len(values) != 1:
            return None
        return next(iter(values))


def disable_view(view: discord.ui.View) -> None:
    for item in view.children:
        item.disabled = True


class BearChannelAccessError(RuntimeError):
    pass


def is_bear_channel(interaction: discord.Interaction) -> bool:
    return interaction.channel_id == BEAR_CHANNEL_ID


def is_bear_tierlist_channel(interaction: discord.Interaction) -> bool:
    return interaction.channel_id == BEAR_TIERLIST_CHANNEL_ID


async def delete_message(message: discord.Message | discord.WebhookMessage) -> None:
    try:
        await message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass


async def delete_messages_by_ids(channel: discord.abc.Messageable, message_ids: list[int]) -> None:
    for message_id in message_ids:
        try:
            message = await channel.fetch_message(message_id)  # type: ignore[attr-defined]
        except (AttributeError, discord.Forbidden, discord.NotFound):
            continue
        await delete_message(message)


def personal_score_text(match: BearMatch, first_user: User, second_user: User) -> str:
    if match.player1_id == first_user.id:
        first_wins = match.player1_wins
        second_wins = match.player2_wins
    else:
        first_wins = match.player2_wins
        second_wins = match.player1_wins
    return f"{first_wins}:{second_wins}"


def bear_player_name(user: User) -> str:
    return user.nickname or str(user.discord_id)


def place_label(index: int) -> str:
    emoji = PLACE_EMOJIS.get(index)
    return f"{emoji} **{index}.**" if emoji else f"**{index}.**"


def split_discord_messages(text: str, limit: int = 1900) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        next_part = block if not current else f"{current}\n\n{block}"
        if len(next_part) <= limit:
            current = next_part
            continue
        if current:
            chunks.append(current)
        current = block
    if current:
        chunks.append(current)
    return chunks


async def ask_acceptance(channel: discord.abc.Messageable, challenger: discord.Member, opponent: discord.Member, title: str) -> bool:
    view = AcceptChallengeView(opponent.id)
    try:
        message = await channel.send(
            f"{opponent.mention}, {challenger.mention} вызывает тебя: **{title}**.\n"
            "Подтверди участие в течение 2 минут.",
            view=view,
        )
    except discord.Forbidden as error:
        raise BearChannelAccessError("У бота нет доступа отправлять сообщения в этот канал.") from error

    await view.wait()
    disable_view(view)
    try:
        await message.edit(view=view)
    except discord.Forbidden:
        pass
    await delete_message(message)
    return view.accepted


async def ask_confirmed_winner(
    channel: discord.abc.Messageable,
    player1: discord.Member,
    player2: discord.Member,
    title: str,
) -> discord.Member | None:
    for attempt in range(1, 3):
        view = WinnerConfirmView(player1, player2)
        try:
            message = await channel.send(
                f"{title}\n"
                f"{player1.mention} и {player2.mention}, выберите победителя. Попытка {attempt}/2.",
                view=view,
            )
        except discord.Forbidden as error:
            raise BearChannelAccessError("У бота нет доступа отправлять сообщения в этот канал.") from error

        await view.wait()
        disable_view(view)
        try:
            await message.edit(view=view)
        except discord.Forbidden:
            pass
        await delete_message(message)
        winner_id = view.agreed_winner_id()
        if winner_id == player1.id:
            return player1
        if winner_id == player2.id:
            return player2
    return None


def user_by_id(users: tuple[User, User], user_id: int) -> User:
    for user in users:
        if user.id == user_id:
            return user
    raise ValueError("Unknown user id")


def register(bot: commands.Bot, settings: Settings) -> None:
    @bot.tree.command(name="become_a_bear", description="Вступить в медвежий ладдер")
    async def become_a_bear(interaction: discord.Interaction) -> None:
        if not is_database_configured():
            await interaction.response.send_message("База данных не настроена.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return

        session_factory = get_session_factory()
        async with session_factory() as session:
            service = BearService(session)
            try:
                user, changed = await service.become_a_bear(interaction.user.id)
            except ValueError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return

        if not changed:
            await interaction.response.send_message("Ты уже медведь.", ephemeral=True)
            return

        role = interaction.guild.get_role(BEAR_ROLE_ID) if interaction.guild else None
        if role is not None:
            try:
                await interaction.user.add_roles(role, reason="Joined bear ladder")
            except discord.Forbidden:
                await interaction.response.send_message(
                    "Ты добавлен в медвежий ладдер, но у бота нет прав выдать роль.",
                    ephemeral=True,
                )
                return

        await interaction.response.send_message(
            f"Добро пожаловать в медвежий ладдер. Стартовый тир: Медвежата, место #{user.tier_placement}.",
            ephemeral=True,
        )

    @bot.tree.command(name="bear_tierlist", description="Показать медвежий тирлист")
    async def bear_tierlist(interaction: discord.Interaction) -> None:
        if not is_database_configured():
            await interaction.response.send_message("База данных не настроена.", ephemeral=True)
            return
        if not is_bear_tierlist_channel(interaction):
            await interaction.response.send_message(
                f"Медвежий тирлист можно смотреть только в канале <#{BEAR_TIERLIST_CHANNEL_ID}>.",
                ephemeral=True,
            )
            return
        if interaction.channel is None or interaction.channel_id is None:
            await interaction.response.send_message("Не удалось определить канал тирлиста.", ephemeral=True)
            return

        await interaction.response.defer()
        channel = interaction.channel
        session_factory = get_session_factory()
        async with session_factory() as session:
            service = BearService(session)
            tierlist = await service.tierlist()

        blocks: list[str] = ["# 🐻 Медвежий тирлист"]
        for tier, users in tierlist:
            emoji = TIER_EMOJIS.get(tier.name, "🐾")
            limit_text = f" · {len(users)}/{tier.slots}" if tier.is_capped and tier.slots is not None else ""
            lines = [f"## {emoji} {tier.name}{limit_text}"]
            if users:
                lines.extend(
                    f"{place_label(index)} {bear_player_name(user)}"
                    for index, user in enumerate(users, start=1)
                )
            else:
                lines.append("_Пока пусто_")
            blocks.append("\n".join(lines))

        text = "\n\n".join(blocks)
        chunks = split_discord_messages(text)
        previous_message_ids = last_tierlist_message_ids.pop(interaction.channel_id, [])
        await delete_messages_by_ids(channel, previous_message_ids)

        sent_messages: list[int] = []
        first_message = await interaction.followup.send(
            chunks[0] if chunks else "Медвежий тирлист пока пуст.",
            wait=True,
        )
        sent_messages.append(first_message.id)
        for chunk in chunks[1:]:
            message = await channel.send(chunk)
            sent_messages.append(message.id)
        last_tierlist_message_ids[interaction.channel_id] = sent_messages

    @bot.tree.command(name="bear_duel", description="Вызвать другого медведя на дуэль")
    @app_commands.describe(player="Медведь, которого ты вызываешь")
    async def bear_duel(interaction: discord.Interaction, player: discord.Member) -> None:
        if not is_database_configured():
            await interaction.response.send_message("База данных не настроена.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return
        if not is_bear_channel(interaction):
            await interaction.response.send_message(
                f"Медвежьи дуэли можно запускать только в канале <#{BEAR_CHANNEL_ID}>.",
                ephemeral=True,
            )
            return
        if interaction.channel is None:
            await interaction.response.send_message("Не удалось определить канал для дуэли.", ephemeral=True)
            return

        await interaction.response.defer()
        channel = interaction.channel
        session_factory = get_session_factory()
        async with session_factory() as session:
            service = BearService(session)
            try:
                challenger_user = await service.get_registered_bear(interaction.user.id)
                opponent_user = await service.get_registered_bear(player.id)
            except ValueError as error:
                await interaction.followup.send(str(error))
                return
            if challenger_user.id == opponent_user.id:
                await interaction.followup.send("Нельзя вызвать самого себя.")
                return

            try:
                accepted = await ask_acceptance(channel, interaction.user, player, "медвежья дуэль")
            except BearChannelAccessError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            if not accepted:
                await interaction.followup.send("Вызов отменен: игрок не подтвердил участие.")
                return

            try:
                winner_member = await ask_confirmed_winner(channel, interaction.user, player, "Медвежья дуэль")
            except BearChannelAccessError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            if winner_member is None:
                await interaction.followup.send("Результат не подтвержден. Обратитесь к администратору.")
                return

            winner_user = challenger_user if winner_member.id == interaction.user.id else opponent_user
            bear_match = await service.record_duel(challenger_user, opponent_user, winner_user)

        await interaction.followup.send(
            f"Медвежья дуэль завершена.\n"
            f"Игроки: {interaction.user.mention} vs {player.mention}\n"
            f"Победитель: {winner_member.mention}\n"
            f"Общий счет личных встреч: {personal_score_text(bear_match, challenger_user, opponent_user)}"
        )

    @bot.tree.command(name="bear_challenge", description="Вызвать другого медведя за место в тирлисте")
    @app_commands.describe(player="Медведь, которого ты вызываешь")
    async def bear_challenge(interaction: discord.Interaction, player: discord.Member) -> None:
        if not is_database_configured():
            await interaction.response.send_message("База данных не настроена.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return
        if not is_bear_channel(interaction):
            await interaction.response.send_message(
                f"Медвежьи challenge можно запускать только в канале <#{BEAR_CHANNEL_ID}>.",
                ephemeral=True,
            )
            return
        if interaction.channel is None:
            await interaction.response.send_message("Не удалось определить канал для challenge.", ephemeral=True)
            return

        await interaction.response.defer()
        channel = interaction.channel
        session_factory = get_session_factory()
        async with session_factory() as session:
            service = BearService(session)
            try:
                challenger_user = await service.get_registered_bear(interaction.user.id)
                opponent_user = await service.get_registered_bear(player.id)
                challenge, plan = await service.create_challenge(challenger_user, opponent_user)
            except ValueError as error:
                await interaction.followup.send(str(error))
                return

            try:
                accepted = await ask_acceptance(
                    channel,
                    interaction.user,
                    player,
                    f"медвежий challenge {plan.challenge_format.value}",
                )
            except BearChannelAccessError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            if not accepted:
                challenge.status = BearChallengeStatus.FINISHED
                await session.commit()
                await interaction.followup.send("Вызов отменен: игрок не подтвердил участие.")
                return

            await service.start_challenge(challenge)
            player1_wins = 0
            player2_wins = 0
            winner_member: discord.Member | None = None
            bear_match: BearMatch | None = None

            while player1_wins < plan.wins_required and player2_wins < plan.wins_required:
                game_number = player1_wins + player2_wins + 1
                try:
                    game_winner = await ask_confirmed_winner(
                        channel,
                        interaction.user,
                        player,
                        f"Медвежий challenge, игра {game_number}",
                    )
                except BearChannelAccessError as error:
                    await interaction.followup.send(str(error), ephemeral=True)
                    return
                if game_winner is None:
                    await interaction.followup.send("Результат игры не подтвержден. Обратитесь к администратору.")
                    return
                if game_winner.id == interaction.user.id:
                    player1_wins += 1
                    game_winner_user = challenger_user
                else:
                    player2_wins += 1
                    game_winner_user = opponent_user
                winner_member = game_winner
                bear_match = await service.record_duel(challenger_user, opponent_user, game_winner_user)

            if winner_member is None:
                await interaction.followup.send("Challenge завершился без победителя. Обратитесь к администратору.")
                return

            winner_user = challenger_user if winner_member.id == interaction.user.id else opponent_user
            loser_user = opponent_user if winner_user.id == challenger_user.id else challenger_user
            await service.finish_challenge(challenge, winner_user, loser_user, player1_wins, player2_wins)

        total_score = personal_score_text(bear_match, challenger_user, opponent_user) if bear_match is not None else "0:0"
        await interaction.followup.send(
            f"Медвежий challenge завершен.\n"
            f"Игроки: {interaction.user.mention} vs {player.mention}\n"
            f"Победитель серии: {winner_member.mention}\n"
            f"Счет серии: {player1_wins}:{player2_wins}\n"
            f"Общий счет личных встреч: {total_score}"
        )
