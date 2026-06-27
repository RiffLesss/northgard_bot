import asyncio
import random
import time
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.database.session import get_session_factory, is_database_configured
from bot.models.enums import BestOf, DraftActionType, GameMode, PickType
from bot.models.user import User
from bot.services.draft_service import is_admin
from bot.services.team3_service import (
    TEAM3_DRAFT_STEPS,
    QueueEntry,
    Team3DraftStep,
    Team3Service,
    find_best_ranked_match,
    split_casual_players,
)


DISPUTE_CHANNEL_ID = 1520167921194766518
DRAFT_STEP_SECONDS = 120
DRAFT_TIMER_UPDATE_SECONDS = 5
ranked_queue: dict[int, QueueEntry] = {}
casual_lobbies: dict[int, list[tuple[int, ...]]] = {}
pending_ready_checks: set[int] = set()
team3_panel_messages: dict[int, int] = {}
active_team3_players: set[int] = set()


@dataclass
class Team3MatchContext:
    match_id: int
    team1_id: int
    team2_id: int
    team1_members: list[discord.Member]
    team2_members: list[discord.Member]
    team1_user_ids: list[int]
    team2_user_ids: list[int]
    game_mode: GameMode
    best_of: BestOf
    team1_role: discord.Role | None = None
    team2_role: discord.Role | None = None
    team1_channel: discord.VoiceChannel | None = None
    team2_channel: discord.VoiceChannel | None = None
    text_channel: discord.TextChannel | None = None
    managed_text_channel: bool = False
    game_number: int = 1
    team1_score: int = 0
    team2_score: int = 0
    clear_clans: list[str] | None = None
    eco_clans: list[str] | None = None


def member_names(members: list[discord.Member]) -> str:
    return ", ".join(member.display_name for member in members)


def team_mentions(members: list[discord.Member]) -> str:
    return " ".join(member.mention for member in members)


def casual_lobby_count(channel_id: int) -> int:
    return sum(len(group) for group in casual_lobbies.get(channel_id, []))


def ranked_queue_count() -> int:
    return len(ranked_queue)


def ranked_wide_count() -> int:
    return sum(1 for entry in ranked_queue.values() if entry.wide)


def render_team3_panel(channel_id: int) -> str:
    return (
        "# Northgard 3v3\n"
        "Выбери режим кнопкой ниже.\n\n"
        f"**Обычная 3v3:** {casual_lobby_count(channel_id)}/6 в lobby\n"
        f"**Ranked 3v3:** {ranked_queue_count()} в очереди\n"
        f"**Ranked wide:** {ranked_wide_count()} в wide-поиске\n\n"
        "**Обычная 3v3** - solo lobby на 6 игроков, команды рандомятся.\n"
        "**Ranked 3v3** - поиск с ограничением рейтинга.\n"
        "**Ranked wide** - поиск с высоким разбросом рейтинга."
    )


async def update_team3_panel(channel: discord.abc.Messageable, channel_id: int) -> None:
    message_id = team3_panel_messages.get(channel_id)
    if message_id is None:
        return
    try:
        message = await channel.fetch_message(message_id)  # type: ignore[attr-defined]
        await message.edit(content=render_team3_panel(channel_id), view=Team3PanelView())
    except (AttributeError, discord.Forbidden, discord.NotFound):
        team3_panel_messages.pop(channel_id, None)


def missing_voice_members(members: list[discord.Member]) -> list[discord.Member]:
    return [member for member in members if member.voice is None or member.voice.channel is None]


def user_display(user: User) -> str:
    return user.nickname or str(user.discord_id)


def is_in_casual_lobby(channel_id: int, discord_id: int) -> bool:
    return any(discord_id in group for group in casual_lobbies.get(channel_id, []))


def clear_lobby(channel_id: int) -> None:
    casual_lobbies.pop(channel_id, None)


def remove_users_from_lobby(channel_id: int, discord_ids: set[int]) -> None:
    updated_groups = []
    for group in casual_lobbies.get(channel_id, []):
        updated_group = tuple(discord_id for discord_id in group if discord_id not in discord_ids)
        if updated_group:
            updated_groups.append(updated_group)
    if updated_groups:
        casual_lobbies[channel_id] = updated_groups
    else:
        casual_lobbies.pop(channel_id, None)


def remove_from_all_searches(discord_ids: set[int]) -> None:
    for discord_id in discord_ids:
        ranked_queue.pop(discord_id, None)
    for channel_id in list(casual_lobbies):
        remove_users_from_lobby(channel_id, discord_ids)


def match_discord_ids(context: Team3MatchContext) -> set[int]:
    return {member.id for member in [*context.team1_members, *context.team2_members]}


def result_timeout_seconds(best_of: BestOf) -> int:
    return 7200 if best_of == BestOf.BO1 else 86400


def wins_needed(best_of: BestOf) -> int:
    return int(best_of.value) // 2 + 1


def series_score(context: Team3MatchContext) -> str:
    return f"Team A {context.team1_score}:{context.team2_score} Team B"


async def fetch_member(guild: discord.Guild, discord_id: int) -> discord.Member | None:
    member = guild.get_member(discord_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(discord_id)
    except discord.NotFound:
        return None


async def create_ranked_resources(guild: discord.Guild, context: Team3MatchContext) -> None:
    overwrites_base = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
    }
    context.team1_role = await guild.create_role(name=f"3v3 Team A #{context.match_id}", reason="Ranked 3v3 match")
    context.team2_role = await guild.create_role(name=f"3v3 Team B #{context.match_id}", reason="Ranked 3v3 match")
    for member in context.team1_members:
        await member.add_roles(context.team1_role, reason="Ranked 3v3 match")
    for member in context.team2_members:
        await member.add_roles(context.team2_role, reason="Ranked 3v3 match")

    context.team1_channel = await guild.create_voice_channel(
        name=f"3v3 Team A #{context.match_id}",
        overwrites={**overwrites_base, context.team1_role: discord.PermissionOverwrite(view_channel=True, connect=True)},
        reason="Ranked 3v3 match",
    )
    context.team2_channel = await guild.create_voice_channel(
        name=f"3v3 Team B #{context.match_id}",
        overwrites={**overwrites_base, context.team2_role: discord.PermissionOverwrite(view_channel=True, connect=True)},
        reason="Ranked 3v3 match",
    )


async def create_match_text_channel(guild: discord.Guild, context: Team3MatchContext, source_channel: discord.abc.GuildChannel) -> discord.TextChannel:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    for member in [*context.team1_members, *context.team2_members]:
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    category = source_channel.category if isinstance(source_channel, discord.abc.GuildChannel) else None
    channel = await guild.create_text_channel(
        name=f"3v3-match-{context.match_id}",
        overwrites=overwrites,
        category=category,
        reason="3v3 match draft channel",
    )
    context.text_channel = channel
    return channel


async def create_ready_text_channel(
    guild: discord.Guild,
    members: list[discord.Member],
    source_channel: discord.abc.GuildChannel,
    title: str,
) -> discord.TextChannel:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    for member in members:
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    category = source_channel.category if isinstance(source_channel, discord.abc.GuildChannel) else None
    safe_title = title.lower().replace(" ", "-")
    return await guild.create_text_channel(
        name=f"{safe_title}-ready",
        overwrites=overwrites,
        category=category,
        reason="3v3 match ready-check channel",
    )


async def rename_match_text_channel(channel: discord.TextChannel, match_id: int) -> None:
    try:
        await channel.edit(name=f"3v3-match-{match_id}", reason="3v3 match created")
    except (discord.Forbidden, discord.NotFound):
        pass


async def delete_channel(channel: discord.abc.GuildChannel) -> None:
    try:
        await channel.delete(reason="3v3 match cancelled")
    except (discord.Forbidden, discord.NotFound):
        pass


async def create_casual_voice_channels(guild: discord.Guild, context: Team3MatchContext, source_channel: discord.abc.GuildChannel) -> None:
    overwrites_base = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, move_members=True),
    }
    team1_overwrites = {
        **overwrites_base,
        **{member: discord.PermissionOverwrite(view_channel=True, connect=True) for member in context.team1_members},
    }
    team2_overwrites = {
        **overwrites_base,
        **{member: discord.PermissionOverwrite(view_channel=True, connect=True) for member in context.team2_members},
    }
    category = source_channel.category if isinstance(source_channel, discord.abc.GuildChannel) else None
    context.team1_channel = await guild.create_voice_channel(
        name=f"3v3 Team A #{context.match_id}",
        overwrites=team1_overwrites,
        category=category,
        reason="Casual 3v3 match",
    )
    context.team2_channel = await guild.create_voice_channel(
        name=f"3v3 Team B #{context.match_id}",
        overwrites=team2_overwrites,
        category=category,
        reason="Casual 3v3 match",
    )


async def move_match_members(context: Team3MatchContext) -> None:
    if context.team1_channel is not None:
        for member in context.team1_members:
            await member.move_to(context.team1_channel, reason="3v3 match started")
    if context.team2_channel is not None:
        for member in context.team2_members:
            await member.move_to(context.team2_channel, reason="3v3 match started")


async def cleanup_match_resources(context: Team3MatchContext) -> None:
    channels: list[discord.abc.GuildChannel | None] = [context.team1_channel, context.team2_channel]
    if context.managed_text_channel:
        channels.append(context.text_channel)
    for channel in channels:
        if channel is not None:
            try:
                await channel.delete(reason="3v3 match finished")
            except (discord.Forbidden, discord.NotFound):
                pass
    for role in [context.team1_role, context.team2_role]:
        if role is not None:
            try:
                await role.delete(reason="Ranked 3v3 match finished")
            except (discord.Forbidden, discord.NotFound):
                pass


class Team3DraftView(discord.ui.View):
    def __init__(self, context: Team3MatchContext, channel: discord.abc.Messageable, on_finish):
        super().__init__(timeout=None)
        self.context = context
        self.channel = channel
        self.on_finish = on_finish
        self.step_index = 0
        self.bans: list[str] = []
        self.picks: dict[str, list[str]] = {"A": [], "B": []}
        self.message: discord.Message | None = None
        self.step_deadline = time.monotonic() + DRAFT_STEP_SECONDS
        self.timer_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.finished = False
        self.refresh_items()

    def current_step(self) -> Team3DraftStep | None:
        if self.step_index >= len(TEAM3_DRAFT_STEPS):
            return None
        return TEAM3_DRAFT_STEPS[self.step_index]

    def side_members(self, side: str) -> list[discord.Member]:
        if self.context.game_number % 2 == 1:
            return self.context.team1_members if side == "A" else self.context.team2_members
        return self.context.team2_members if side == "A" else self.context.team1_members

    def side_team_id(self, side: str) -> int:
        if self.context.game_number % 2 == 1:
            return self.context.team1_id if side == "A" else self.context.team2_id
        return self.context.team2_id if side == "A" else self.context.team1_id

    def clan_pool(self, pick_type: PickType) -> list[str]:
        source = self.context.clear_clans if pick_type == PickType.CLEAR else self.context.eco_clans
        if source is None:
            source = []
        return [clan for clan in source if clan not in self.bans]

    def available_options(self, step: Team3DraftStep) -> list[str]:
        options = self.clan_pool(step.pick_type)
        if step.action_type == DraftActionType.PICK:
            options = [clan for clan in options if clan not in self.picks[step.side]]
            if step.pick_type == PickType.CLEAR:
                clear_clans = set(self.context.clear_clans or [])
                options = [clan for clan in options if not any(pick in clear_clans for pick in self.picks[step.side])]
        return options

    def refresh_items(self) -> None:
        self.clear_items()
        step = self.current_step()
        if step is None:
            return
        options = self.available_options(step)
        self.add_item(Team3ClanSelect(step, options))

    def remaining_seconds(self) -> int:
        return max(0, int(self.step_deadline - time.monotonic()))

    def remaining_text(self) -> str:
        seconds = self.remaining_seconds()
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def render(self) -> str:
        step = self.current_step()
        phase = "Драфт завершен" if step is None else f"Team {step.side}: {step.action_type.value} {step.pick_type.value}"
        available_clear = ", ".join(self.clan_pool(PickType.CLEAR)) or "-"
        available_eco = ", ".join(self.clan_pool(PickType.ECO)) or "-"
        return (
            f"# 3v3 Draft · Match #{self.context.match_id} · Game {self.context.game_number}\n"
            f"**Счет серии:** {series_score(self.context)}\n"
            f"**Team A:** {member_names(self.context.team1_members)}\n"
            f"**Team B:** {member_names(self.context.team2_members)}\n"
            f"**Draft side A:** {member_names(self.side_members('A'))}\n"
            f"**Draft side B:** {member_names(self.side_members('B'))}\n\n"
            f"**Фаза:** {phase}\n"
            f"**Осталось на действие:** {self.remaining_text() if step is not None else '-'}\n"
            f"**Баны:** {', '.join(self.bans) or '-'}\n"
            f"**Draft side A picks:** {', '.join(self.picks['A']) or '-'}\n"
            f"**Draft side B picks:** {', '.join(self.picks['B']) or '-'}\n\n"
            f"**Доступные clear:** {available_clear}\n"
            f"**Доступные eco:** {available_eco}"
        )

    async def record_current_step(self, step: Team3DraftStep, clan: str) -> None:
        if step.action_type == DraftActionType.BAN:
            self.bans.append(clan)
        else:
            self.picks[step.side].append(clan)

        session_factory = get_session_factory()
        async with session_factory() as session:
            service = Team3Service(session)
            await service.record_draft_action(
                self.context.match_id,
                self.side_team_id(step.side),
                clan,
                step.action_type,
                step.pick_type,
            )

    async def finish_or_continue(self, interaction: discord.Interaction | None = None) -> None:
        self.refresh_items()
        if self.current_step() is None:
            self.finished = True
            self.cancel_timer()
            if interaction is not None:
                await interaction.response.edit_message(content=self.render(), view=None)
            elif self.message is not None:
                await self.message.edit(content=self.render(), view=None)
            await self.on_finish(self.channel, self.context)
            self.stop()
            return

        self.step_deadline = time.monotonic() + DRAFT_STEP_SECONDS
        self.start_timer()
        if interaction is not None:
            await interaction.response.edit_message(content=self.render(), view=self)
        elif self.message is not None:
            await self.message.edit(content=self.render(), view=self)

    async def handle_pick(self, interaction: discord.Interaction, clan: str) -> None:
        async with self.lock:
            step = self.current_step()
            if step is None:
                await interaction.response.send_message("Драфт уже завершен.", ephemeral=True)
                return
            allowed_ids = {member.id for member in self.side_members(step.side)}
            if interaction.user.id not in allowed_ids:
                await interaction.response.send_message(f"Сейчас выбирает Team {step.side}.", ephemeral=True)
                return
            if clan not in self.available_options(step):
                await interaction.response.send_message("Этот клан сейчас недоступен.", ephemeral=True)
                return

            self.cancel_timer()
            await self.record_current_step(step, clan)
            self.step_index += 1
            await self.finish_or_continue(interaction)

    async def auto_pick_current_step(self) -> None:
        async with self.lock:
            if self.finished:
                return
            step = self.current_step()
            if step is None:
                return
            options = self.available_options(step)
            if not options:
                await cancel_team3_match(self.context, "В драфте не осталось доступных кланов. Матч отменен.")
                self.finished = True
                self.stop()
                return
            await self.record_current_step(step, random.choice(options))
            self.step_index += 1
            await self.finish_or_continue()

    def start_timer(self) -> None:
        self.cancel_timer()
        self.timer_task = asyncio.create_task(self.timer_loop())

    def cancel_timer(self) -> None:
        if self.timer_task is not None and self.timer_task is not asyncio.current_task() and not self.timer_task.done():
            self.timer_task.cancel()
        self.timer_task = None

    async def timer_loop(self) -> None:
        try:
            while not self.finished and self.current_step() is not None:
                remaining = self.remaining_seconds()
                if remaining <= 0:
                    await self.auto_pick_current_step()
                    return
                await asyncio.sleep(min(DRAFT_TIMER_UPDATE_SECONDS, remaining))
                if self.message is not None and not self.finished and self.current_step() is not None:
                    await self.message.edit(content=self.render(), view=self)
        except asyncio.CancelledError:
            return
        except (discord.Forbidden, discord.NotFound):
            self.finished = True
            self.stop()

    def stop(self) -> None:
        self.cancel_timer()
        super().stop()


class Team3ClanSelect(discord.ui.Select):
    def __init__(self, step: Team3DraftStep, clans: list[str]):
        options = [discord.SelectOption(label=clan, value=clan) for clan in clans[:25]]
        super().__init__(placeholder=f"Team {step.side}: {step.action_type.value} {step.pick_type.value}", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, Team3DraftView):
            return
        await view.handle_pick(interaction, self.values[0])


class ResultConfirmView(discord.ui.View):
    def __init__(self, bot: commands.Bot, context: Team3MatchContext):
        super().__init__(timeout=result_timeout_seconds(context.best_of))
        self.bot = bot
        self.context = context
        self.votes: dict[int, int] = {}
        self.finished = False

    def team_for_user(self, discord_id: int) -> str | None:
        if discord_id in {member.id for member in self.context.team1_members}:
            return "A"
        if discord_id in {member.id for member in self.context.team2_members}:
            return "B"
        return None

    def accepted_winner(self) -> int | None:
        for winner_team_id in [self.context.team1_id, self.context.team2_id]:
            team_a_votes = sum(
                1 for member in self.context.team1_members if self.votes.get(member.id) == winner_team_id
            )
            team_b_votes = sum(
                1 for member in self.context.team2_members if self.votes.get(member.id) == winner_team_id
            )
            if team_a_votes >= 2 and team_b_votes >= 2:
                return winner_team_id
        return None

    def has_conflict(self) -> bool:
        team_a_winners = {self.votes.get(member.id) for member in self.context.team1_members}
        team_b_winners = {self.votes.get(member.id) for member in self.context.team2_members}
        team_a_winners.discard(None)
        team_b_winners.discard(None)
        if not team_a_winners or not team_b_winners:
            return False
        if self.accepted_winner() is not None:
            return False
        return len(self.votes) == 6 or team_a_winners.isdisjoint(team_b_winners)

    async def vote(self, interaction: discord.Interaction, winner_team_id: int) -> None:
        if self.finished:
            await interaction.response.send_message("Результат уже подтвержден.", ephemeral=True)
            return
        if self.team_for_user(interaction.user.id) is None:
            await interaction.response.send_message("Ты не участвуешь в этом матче.", ephemeral=True)
            return
        self.votes[interaction.user.id] = winner_team_id
        accepted = self.accepted_winner()
        if accepted is None:
            if self.has_conflict():
                self.finished = True
                await interaction.response.send_message(
                    "Команды не согласовали победителя. Матч отправлен администраторам на решение.",
                    ephemeral=True,
                )
                await send_result_dispute(self.bot, self.context, self.votes)
                self.stop()
                return
            await interaction.response.send_message("Голос принят. Ждем подтверждения 2 игроков из каждой команды.", ephemeral=True)
            return

        self.finished = True
        await finish_team3_match(self.bot, interaction, self.context, accepted)
        self.stop()

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.finished = True
        await cancel_team3_match(self.context, "Время на подтверждение результата истекло. Матч отменен.")

    @discord.ui.button(label="Победила Team A", style=discord.ButtonStyle.success)
    async def team_a_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.vote(interaction, self.context.team1_id)

    @discord.ui.button(label="Победила Team B", style=discord.ButtonStyle.success)
    async def team_b_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.vote(interaction, self.context.team2_id)


class ReadyCheckView(discord.ui.View):
    def __init__(self, members: list[discord.Member], title: str):
        super().__init__(timeout=60)
        self.members = members
        self.title = title
        self.accepted_ids: set[int] = set()
        self.declined_id: int | None = None

    async def update_message(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=self.render(), view=self)

    def render(self) -> str:
        accepted = len(self.accepted_ids)
        mentions = " ".join(member.mention for member in self.members)
        return (
            f"**{self.title} найден.**\n"
            f"{mentions}\n\n"
            f"Нужно принять матч: **{accepted}/6**\n"
            f"Время на подтверждение: 60 секунд."
        )

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        allowed_ids = {member.id for member in self.members}
        if interaction.user.id not in allowed_ids:
            await interaction.response.send_message("Ты не участвуешь в этом ready-check.", ephemeral=True)
            return
        self.accepted_ids.add(interaction.user.id)
        if len(self.accepted_ids) == len(self.members):
            for item in self.children:
                item.disabled = True
            await self.update_message(interaction)
            self.stop()
            return
        await self.update_message(interaction)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        allowed_ids = {member.id for member in self.members}
        if interaction.user.id not in allowed_ids:
            await interaction.response.send_message("Ты не участвуешь в этом ready-check.", ephemeral=True)
            return
        self.declined_id = interaction.user.id
        for item in self.children:
            item.disabled = True
        await self.update_message(interaction)
        self.stop()

    def accepted(self) -> bool:
        return self.declined_id is None and len(self.accepted_ids) == len(self.members)


async def run_ready_check(channel: discord.abc.Messageable, members: list[discord.Member], title: str) -> ReadyCheckView:
    view = ReadyCheckView(members, title)
    message = await channel.send(view.render(), view=view)
    await view.wait()
    for item in view.children:
        item.disabled = True
    try:
        await message.edit(content=view.render(), view=view)
    except (discord.Forbidden, discord.NotFound):
        pass
    return view


async def finish_team3_match(
    bot: commands.Bot,
    interaction: discord.Interaction,
    context: Team3MatchContext,
    winner_team_id: int,
) -> None:
    winner_label = "Team A" if winner_team_id == context.team1_id else "Team B"
    finished_game = context.game_number
    if winner_team_id == context.team1_id:
        context.team1_score += 1
    else:
        context.team2_score += 1

    if max(context.team1_score, context.team2_score) < wins_needed(context.best_of):
        context.game_number += 1
        next_channel = context.text_channel or interaction.channel
        message_text = (
            f"Game {finished_game} завершена.\n"
            f"Победитель: **{winner_label}**.\n"
            f"Счет серии: **{series_score(context)}**.\n"
            f"Запускаю Game {context.game_number}."
        )
        await interaction.response.send_message(message_text)
        if context.text_channel is not None and context.text_channel.id != interaction.channel_id:
            await context.text_channel.send(message_text)
        if next_channel is not None:
            await start_team3_draft(bot, next_channel, context)
        return

    session_factory = get_session_factory()
    async with session_factory() as session:
        service = Team3Service(session)
        if context.game_mode == GameMode.RANKED:
            if winner_team_id == context.team1_id:
                delta = await service.finish_ranked_match(context.match_id, winner_team_id, context.team1_user_ids, context.team2_user_ids)
            else:
                delta = await service.finish_ranked_match(context.match_id, winner_team_id, context.team2_user_ids, context.team1_user_ids)
        else:
            delta = 0
            await service.finish_casual_match(context.match_id, winner_team_id)

    rating_text = f"\nРейтинг: +{delta}/-{delta}" if context.game_mode == GameMode.RANKED else ""
    message_text = (
        f"Матч #{context.match_id} завершен.\n"
        f"Финальный счет: **{series_score(context)}**.\n"
        f"Победитель: **{winner_label}**.{rating_text}\n"
        "Временные каналы и роли будут удалены через 10 секунд."
    )
    await interaction.response.send_message(message_text)
    if context.text_channel is not None and context.text_channel.id != interaction.channel_id:
        await context.text_channel.send(message_text)
    active_team3_players.difference_update(match_discord_ids(context))
    await asyncio.sleep(10)
    await cleanup_match_resources(context)


async def cancel_team3_match(context: Team3MatchContext, reason: str) -> None:
    active_team3_players.difference_update(match_discord_ids(context))
    if context.text_channel is not None:
        try:
            await context.text_channel.send(f"{reason}\nВременные каналы и роли будут удалены через 10 секунд.")
        except (discord.Forbidden, discord.NotFound):
            pass
    await asyncio.sleep(10)
    await cleanup_match_resources(context)


class DisputeResolveView(discord.ui.View):
    def __init__(self, bot: commands.Bot, context: Team3MatchContext):
        super().__init__(timeout=None)
        self.bot = bot
        self.context = context
        self.resolved = False

    async def resolve(self, interaction: discord.Interaction, winner_team_id: int) -> None:
        if self.resolved:
            await interaction.response.send_message("Этот спор уже решен.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("Только админ бота может решить спор.", ephemeral=True)
            return
        self.resolved = True
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        await finish_team3_match(self.bot, interaction, self.context, winner_team_id)

    @discord.ui.button(label="Победила Team A", style=discord.ButtonStyle.success)
    async def team_a_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.resolve(interaction, self.context.team1_id)

    @discord.ui.button(label="Победила Team B", style=discord.ButtonStyle.success)
    async def team_b_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.resolve(interaction, self.context.team2_id)


async def send_result_dispute(bot: commands.Bot, context: Team3MatchContext, votes: dict[int, int]) -> None:
    channel = bot.get_channel(DISPUTE_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(DISPUTE_CHANNEL_ID)
        except (discord.Forbidden, discord.NotFound):
            channel = None
    if not isinstance(channel, discord.TextChannel):
        if context.text_channel is not None:
            await context.text_channel.send("Не удалось отправить спор администраторам. Обратитесь к администратору вручную.")
        return

    def vote_label(member: discord.Member) -> str:
        vote = votes.get(member.id)
        if vote == context.team1_id:
            return f"{member.mention}: Team A"
        if vote == context.team2_id:
            return f"{member.mention}: Team B"
        return f"{member.mention}: нет голоса"

    await channel.send(
        "@here\n"
        f"Спорный результат 3v3 матча #{context.match_id}.\n"
        f"Game {context.game_number}. Счет серии: {series_score(context)}.\n"
        f"Team A: {team_mentions(context.team1_members)}\n"
        f"Team B: {team_mentions(context.team2_members)}\n\n"
        "**Голоса Team A:**\n"
        + "\n".join(vote_label(member) for member in context.team1_members)
        + "\n\n**Голоса Team B:**\n"
        + "\n".join(vote_label(member) for member in context.team2_members)
        + "\n\nВыберите победителя:",
        view=DisputeResolveView(bot, context),
    )


async def start_result_confirmation(bot: commands.Bot, channel: discord.abc.Messageable, context: Team3MatchContext) -> None:
    await channel.send(
        f"Матч #{context.match_id}, Game {context.game_number} началась.\n"
        f"Счет серии: **{series_score(context)}**.\n"
        f"После игры подтвердите победителя. Нужно минимум 2 голоса от каждой команды.\n"
        f"Лимит времени: {'2 часа' if context.best_of == BestOf.BO1 else '24 часа'}.",
        view=ResultConfirmView(bot, context),
    )


async def start_team3_draft(bot: commands.Bot, channel: discord.abc.Messageable, context: Team3MatchContext) -> None:
    async def on_finish(result_channel: discord.abc.Messageable, match_context: Team3MatchContext) -> None:
        await start_result_confirmation(bot, result_channel, match_context)

    view = Team3DraftView(context, channel, on_finish)
    message = await channel.send(view.render(), view=view)
    view.message = message
    view.start_timer()


async def build_context(
    guild: discord.Guild,
    team1_members: list[discord.Member],
    team2_members: list[discord.Member],
    game_mode: GameMode,
    best_of: BestOf = BestOf.BO1,
) -> Team3MatchContext:
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = Team3Service(session)
        team1_users = [await service.get_registered_user(member.id) for member in team1_members]
        team2_users = [await service.get_registered_user(member.id) for member in team2_members]
        created = await service.create_match(
            [user.id for user in team1_users],
            [user.id for user in team2_users],
            game_mode,
            best_of,
        )
        clear_clans, eco_clans = await service.get_clan_pools()
    return Team3MatchContext(
        match_id=created.match.id,
        team1_id=created.team1.id,
        team2_id=created.team2.id,
        team1_members=team1_members,
        team2_members=team2_members,
        team1_user_ids=[user.id for user in team1_users],
        team2_user_ids=[user.id for user in team2_users],
        game_mode=game_mode,
        best_of=best_of,
        clear_clans=clear_clans,
        eco_clans=eco_clans,
    )


class Team3PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Обычная 3v3", style=discord.ButtonStyle.primary)
    async def casual(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await join_casual_queue(interaction)

    @discord.ui.button(label="Ranked 3v3", style=discord.ButtonStyle.success)
    async def ranked(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await join_ranked_queue(interaction, wide=False)

    @discord.ui.button(label="Ranked wide", style=discord.ButtonStyle.danger)
    async def ranked_wide(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await join_ranked_queue(interaction, wide=True)

    @discord.ui.button(label="Выйти из поиска", style=discord.ButtonStyle.secondary)
    async def leave_queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        ranked_queue.pop(interaction.user.id, None)
        if interaction.channel_id is not None and is_in_casual_lobby(interaction.channel_id, interaction.user.id):
            casual_lobbies[interaction.channel_id] = [
                group for group in casual_lobbies.get(interaction.channel_id, []) if interaction.user.id not in group
            ]
        if interaction.channel is not None and interaction.channel_id is not None:
            await update_team3_panel(interaction.channel, interaction.channel_id)
        await interaction.response.send_message("Ты вышел из очереди/lobby.", ephemeral=True)


async def join_ranked_queue(interaction: discord.Interaction, wide: bool) -> None:
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message("Поиск доступен только на сервере.", ephemeral=True)
        return
    if not is_database_configured():
        await interaction.response.send_message("База данных не настроена.", ephemeral=True)
        return
    if interaction.user.id in active_team3_players:
        await interaction.response.send_message("Ты уже участвуешь в активном 3v3 матче.", ephemeral=True)
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = Team3Service(session)
        try:
            user = await service.get_registered_user(interaction.user.id)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
    ranked_queue[interaction.user.id] = QueueEntry(
        user_id=user.id,
        discord_id=user.discord_id,
        nickname=user_display(user),
        rating=user.team_rating,
        wide=wide,
    )
    mode = "wide" if wide else "normal"
    await interaction.response.send_message(f"Ты в ranked 3v3 queue. Режим: {mode}.", ephemeral=True)
    await update_team3_panel(interaction.channel, interaction.channel.id)
    await maybe_start_ranked_match(interaction)


async def join_casual_queue(interaction: discord.Interaction) -> None:
    if interaction.guild is None or interaction.channel_id is None or interaction.channel is None:
        await interaction.response.send_message("Поиск доступен только на сервере.", ephemeral=True)
        return
    if not is_database_configured():
        await interaction.response.send_message("База данных не настроена.", ephemeral=True)
        return
    if interaction.user.id in active_team3_players:
        await interaction.response.send_message("Ты уже участвуешь в активном 3v3 матче.", ephemeral=True)
        return
    if is_in_casual_lobby(interaction.channel_id, interaction.user.id):
        await interaction.response.send_message("Ты уже находишься в casual lobby.", ephemeral=True)
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = Team3Service(session)
        try:
            await service.get_registered_user(interaction.user.id)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
    lobby = casual_lobbies.setdefault(interaction.channel_id, [])
    if casual_lobby_count(interaction.channel_id) >= 6:
        await interaction.response.send_message("Casual lobby уже заполнено.", ephemeral=True)
        return
    lobby.append((interaction.user.id,))
    await interaction.response.send_message("Ты добавлен в casual 3v3 lobby.", ephemeral=True)
    await update_team3_panel(interaction.channel, interaction.channel_id)
    await maybe_start_casual_match(interaction)


async def maybe_start_ranked_match(interaction: discord.Interaction) -> None:
    if interaction.guild is None or interaction.channel is None:
        return
    if interaction.channel.id in pending_ready_checks:
        return
    queue_entries = list(ranked_queue.values())
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = Team3Service(session)
        blacklist_pairs = await service.get_blacklist_pairs([entry.user_id for entry in queue_entries])
    split = find_best_ranked_match(queue_entries, blacklist_pairs)
    if split is None:
        return

    team1_members = [await fetch_member(interaction.guild, entry.discord_id) for entry in split.team_a]
    team2_members = [await fetch_member(interaction.guild, entry.discord_id) for entry in split.team_b]
    if any(member is None for member in [*team1_members, *team2_members]):
        await interaction.channel.send("Не удалось найти всех игроков на сервере. Матч отменен.")
        return
    members = [member for member in [*team1_members, *team2_members] if member is not None]
    missing_voice = missing_voice_members(members)
    if missing_voice:
        for member in missing_voice:
            ranked_queue.pop(member.id, None)
        await update_team3_panel(interaction.channel, interaction.channel.id)
        await interaction.channel.send(
            "Матч найден, но не все игроки находятся в голосовом канале. "
            f"Игроки удалены из очереди: {team_mentions(missing_voice)}"
        )
        return

    try:
        ready_channel = await create_ready_text_channel(interaction.guild, members, interaction.channel, "ranked-3v3")
        await interaction.channel.send(f"Ranked 3v3 найден. Ready-check: {ready_channel.mention}")
    except discord.Forbidden:
        ready_channel = interaction.channel
        await interaction.channel.send("У бота нет прав создать ready-check канал. Ready-check будет здесь.")

    pending_ready_checks.add(interaction.channel.id)
    ready_check = await run_ready_check(ready_channel, members, "Ranked 3v3")
    pending_ready_checks.discard(interaction.channel.id)
    if not ready_check.accepted():
        ready_ids = {member.id for member in members}
        declined_or_missing = ready_ids - ready_check.accepted_ids
        await interaction.channel.send(
            "Ready-check не принят всеми игроками. Матч отменен, принявшие игроки остаются в очереди."
        )
        for discord_id in declined_or_missing:
            ranked_queue.pop(discord_id, None)
        await update_team3_panel(interaction.channel, interaction.channel.id)
        if isinstance(ready_channel, discord.TextChannel):
            await delete_channel(ready_channel)
        return

    for entry in [*split.team_a, *split.team_b]:
        ranked_queue.pop(entry.discord_id, None)
    match_player_ids = {entry.discord_id for entry in [*split.team_a, *split.team_b]}
    active_team3_players.update(match_player_ids)
    remove_from_all_searches(match_player_ids)
    await update_team3_panel(interaction.channel, interaction.channel.id)

    context = await build_context(
        interaction.guild,
        [member for member in team1_members if member is not None],
        [member for member in team2_members if member is not None],
        GameMode.RANKED,
    )
    if isinstance(ready_channel, discord.TextChannel):
        context.text_channel = ready_channel
        context.managed_text_channel = True
        await rename_match_text_channel(ready_channel, context.match_id)
    try:
        await create_ranked_resources(interaction.guild, context)
        await move_match_members(context)
    except discord.Forbidden:
        await interaction.channel.send(
            "Матч создан, но у бота нет прав создать роли/голосовые каналы или переместить игроков."
        )
    draft_channel = context.text_channel or interaction.channel

    await interaction.channel.send(
        f"Ranked 3v3 найден. Match #{context.match_id}\n"
        f"Team A: {team_mentions(context.team1_members)}\n"
        f"Team B: {team_mentions(context.team2_members)}\n"
        f"Разница рейтинга команд: {split.rating_diff}\n"
        f"Draft channel: {draft_channel.mention if isinstance(draft_channel, discord.TextChannel) else 'этот канал'}"
    )
    await start_team3_draft(interaction.client, draft_channel, context)


async def maybe_start_casual_match(interaction: discord.Interaction) -> None:
    if interaction.guild is None or interaction.channel is None or interaction.channel_id is None:
        return
    if interaction.channel_id in pending_ready_checks:
        return
    lobby = casual_lobbies.get(interaction.channel_id, [])
    if sum(len(group) for group in lobby) < 6:
        await update_team3_panel(interaction.channel, interaction.channel_id)
        return
    if sum(len(group) for group in lobby) > 6:
        return

    session_factory = get_session_factory()
    users: list[User] = []
    async with session_factory() as session:
        service = Team3Service(session)
        for group in lobby:
            for discord_id in group:
                users.append(await service.get_registered_user(discord_id))
        blacklist_pairs = await service.get_blacklist_pairs([user.id for user in users])
    try:
        team1_users, team2_users = split_casual_players(users, blacklist_pairs)
    except ValueError:
        await update_team3_panel(interaction.channel, interaction.channel_id)
        await interaction.channel.send(
            "Casual lobby заполнено, но невозможно собрать две команды без blacklist-конфликтов. "
            "Кому-то нужно выйти из lobby или изменить blacklist."
        )
        return
    team1_members = [await fetch_member(interaction.guild, user.discord_id) for user in team1_users]
    team2_members = [await fetch_member(interaction.guild, user.discord_id) for user in team2_users]
    if any(member is None for member in [*team1_members, *team2_members]):
        await interaction.channel.send("Не удалось найти всех игроков на сервере. Lobby очищено.")
        clear_lobby(interaction.channel_id)
        return
    members = [member for member in [*team1_members, *team2_members] if member is not None]
    missing_voice = missing_voice_members(members)
    if missing_voice:
        remove_users_from_lobby(interaction.channel_id, {member.id for member in missing_voice})
        remaining_count = sum(len(group) for group in casual_lobbies.get(interaction.channel_id, []))
        await update_team3_panel(interaction.channel, interaction.channel_id)
        await interaction.channel.send(
            "Матч найден, но не все игроки находятся в голосовом канале. "
            f"Игроки удалены из lobby: {team_mentions(missing_voice)}. Осталось: {remaining_count}/6."
        )
        return

    try:
        ready_channel = await create_ready_text_channel(interaction.guild, members, interaction.channel, "casual-3v3")
        await interaction.channel.send(f"Casual 3v3 найден. Ready-check: {ready_channel.mention}")
    except discord.Forbidden:
        ready_channel = interaction.channel
        await interaction.channel.send("У бота нет прав создать ready-check канал. Ready-check будет здесь.")

    pending_ready_checks.add(interaction.channel_id)
    ready_check = await run_ready_check(ready_channel, members, "Casual 3v3")
    pending_ready_checks.discard(interaction.channel_id)
    if not ready_check.accepted():
        ready_ids = {member.id for member in members}
        declined_or_missing = ready_ids - ready_check.accepted_ids
        remove_users_from_lobby(interaction.channel_id, declined_or_missing)
        remaining_count = sum(len(group) for group in casual_lobbies.get(interaction.channel_id, []))
        await update_team3_panel(interaction.channel, interaction.channel_id)
        await interaction.channel.send(
            "Ready-check не принят всеми игроками. "
            f"Принявшие игроки остаются в lobby: {remaining_count}/6."
        )
        if isinstance(ready_channel, discord.TextChannel):
            await delete_channel(ready_channel)
        return

    context = await build_context(
        interaction.guild,
        [member for member in team1_members if member is not None],
        [member for member in team2_members if member is not None],
        GameMode.CASUAL,
    )
    active_team3_players.update(member.id for member in [*context.team1_members, *context.team2_members])
    remove_from_all_searches(match_discord_ids(context))
    if isinstance(ready_channel, discord.TextChannel):
        context.text_channel = ready_channel
        context.managed_text_channel = True
        await rename_match_text_channel(ready_channel, context.match_id)
    clear_lobby(interaction.channel_id)
    await update_team3_panel(interaction.channel, interaction.channel_id)
    try:
        await create_casual_voice_channels(interaction.guild, context, interaction.channel)
        await move_match_members(context)
    except discord.Forbidden:
        await interaction.channel.send(
            "Матч создан, но у бота нет прав создать голосовые каналы или переместить игроков."
        )
    draft_channel = context.text_channel or interaction.channel

    await interaction.channel.send(
        f"Casual 3v3 собран. Match #{context.match_id}\n"
        f"Team A: {team_mentions(context.team1_members)}\n"
        f"Team B: {team_mentions(context.team2_members)}\n"
        f"Draft channel: {draft_channel.mention if isinstance(draft_channel, discord.TextChannel) else 'этот канал'}"
    )
    await start_team3_draft(interaction.client, draft_channel, context)


def register(bot: commands.Bot, settings: Settings) -> None:
    @bot.tree.command(name="team3_panel", description="Панель 3v3 матчей")
    @app_commands.default_permissions(manage_guild=True)
    async def team3_panel(interaction: discord.Interaction) -> None:
        if interaction.channel_id is None:
            await interaction.response.send_message("Не удалось определить канал.", ephemeral=True)
            return
        await interaction.response.send_message(render_team3_panel(interaction.channel_id), view=Team3PanelView())
        message = await interaction.original_response()
        team3_panel_messages[interaction.channel_id] = message.id

    @bot.tree.command(name="tournament_3v3_start", description="Запустить турнирный 3v3 draft")
    @app_commands.describe(
        team_a_role="Роль первой команды",
        team_b_role="Роль второй команды",
        best_of="Формат серии",
    )
    @app_commands.choices(
        best_of=[
            app_commands.Choice(name="bo1", value="1"),
            app_commands.Choice(name="bo3", value="3"),
            app_commands.Choice(name="bo5", value="5"),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def tournament_3v3_start(
        interaction: discord.Interaction,
        team_a_role: discord.Role,
        team_b_role: discord.Role,
        best_of: app_commands.Choice[str],
    ) -> None:
        if not is_database_configured():
            await interaction.response.send_message("База данных не настроена.", ephemeral=True)
            return
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return

        team_a_members = [member for member in team_a_role.members if not member.bot]
        team_b_members = [member for member in team_b_role.members if not member.bot]
        if len(team_a_members) != 3 or len(team_b_members) != 3:
            await interaction.response.send_message(
                "В каждой командной роли должно быть ровно 3 зарегистрированных игрока.",
                ephemeral=True,
            )
            return
        busy_members = [member for member in [*team_a_members, *team_b_members] if member.id in active_team3_players]
        if busy_members:
            await interaction.response.send_message(
                "Нельзя запустить матч: эти игроки уже участвуют в активном 3v3 матче: "
                f"{team_mentions(busy_members)}",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        try:
            context = await build_context(
                interaction.guild,
                team_a_members,
                team_b_members,
                GameMode.TOURNAMENT,
                BestOf(best_of.value),
            )
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return

        await interaction.followup.send(
            f"Tournament 3v3 создан. Match #{context.match_id}, bo{best_of.value}.\n"
            f"Team A: {team_mentions(context.team1_members)}\n"
            f"Team B: {team_mentions(context.team2_members)}"
        )
        active_team3_players.update(member.id for member in [*context.team1_members, *context.team2_members])
        remove_from_all_searches(match_discord_ids(context))
        if isinstance(interaction.channel, discord.TextChannel):
            context.text_channel = interaction.channel
        await start_team3_draft(bot, interaction.channel, context)
