import re
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.database.session import get_session_factory, is_database_configured
from bot.models.enums import BestOf, DraftActionType, GameMode, PickType
from bot.models.user import User
from bot.services.team3_service import (
    ALL_CLANS,
    CLEAR_CLANS,
    ECO_CLANS,
    TEAM3_DRAFT_STEPS,
    CasualGroup,
    QueueEntry,
    Team3DraftStep,
    Team3Service,
    find_best_ranked_match,
    split_casual_groups,
)


MENTION_RE = re.compile(r"<@!?(\d+)>")
ranked_queue: dict[int, QueueEntry] = {}
casual_lobbies: dict[int, list[tuple[int, ...]]] = {}


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
    team1_role: discord.Role | None = None
    team2_role: discord.Role | None = None
    team1_channel: discord.VoiceChannel | None = None
    team2_channel: discord.VoiceChannel | None = None


def member_names(members: list[discord.Member]) -> str:
    return ", ".join(member.display_name for member in members)


def team_mentions(members: list[discord.Member]) -> str:
    return " ".join(member.mention for member in members)


def user_display(user: User) -> str:
    return user.nickname or str(user.discord_id)


def is_in_casual_lobby(channel_id: int, discord_id: int) -> bool:
    return any(discord_id in group for group in casual_lobbies.get(channel_id, []))


def clear_lobby(channel_id: int) -> None:
    casual_lobbies.pop(channel_id, None)


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


async def cleanup_ranked_resources(context: Team3MatchContext) -> None:
    for channel in [context.team1_channel, context.team2_channel]:
        if channel is not None:
            try:
                await channel.delete(reason="Ranked 3v3 match finished")
            except (discord.Forbidden, discord.NotFound):
                pass
    for role in [context.team1_role, context.team2_role]:
        if role is not None:
            try:
                await role.delete(reason="Ranked 3v3 match finished")
            except (discord.Forbidden, discord.NotFound):
                pass


class Team3DraftView(discord.ui.View):
    def __init__(self, context: Team3MatchContext, on_finish):
        super().__init__(timeout=900)
        self.context = context
        self.on_finish = on_finish
        self.step_index = 0
        self.bans: list[str] = []
        self.picks: dict[str, list[str]] = {"A": [], "B": []}
        self.message: discord.Message | None = None
        self.refresh_items()

    def current_step(self) -> Team3DraftStep | None:
        if self.step_index >= len(TEAM3_DRAFT_STEPS):
            return None
        return TEAM3_DRAFT_STEPS[self.step_index]

    def side_members(self, side: str) -> list[discord.Member]:
        return self.context.team1_members if side == "A" else self.context.team2_members

    def side_team_id(self, side: str) -> int:
        return self.context.team1_id if side == "A" else self.context.team2_id

    def clan_pool(self, pick_type: PickType) -> list[str]:
        source = CLEAR_CLANS if pick_type == PickType.CLEAR else ECO_CLANS
        return [clan for clan in source if clan not in self.bans]

    def available_options(self, step: Team3DraftStep) -> list[str]:
        options = self.clan_pool(step.pick_type)
        if step.action_type == DraftActionType.PICK:
            options = [clan for clan in options if clan not in self.picks[step.side]]
            if step.pick_type == PickType.CLEAR:
                options = [clan for clan in options if not any(pick in CLEAR_CLANS for pick in self.picks[step.side])]
        return options

    def refresh_items(self) -> None:
        self.clear_items()
        step = self.current_step()
        if step is None:
            return
        options = self.available_options(step)
        self.add_item(Team3ClanSelect(step, options))

    def render(self) -> str:
        step = self.current_step()
        phase = "Драфт завершен" if step is None else f"Team {step.side}: {step.action_type.value} {step.pick_type.value}"
        available_clear = ", ".join(self.clan_pool(PickType.CLEAR)) or "-"
        available_eco = ", ".join(self.clan_pool(PickType.ECO)) or "-"
        return (
            f"# 3v3 Draft · Match #{self.context.match_id}\n"
            f"**Team A:** {member_names(self.context.team1_members)}\n"
            f"**Team B:** {member_names(self.context.team2_members)}\n\n"
            f"**Фаза:** {phase}\n"
            f"**Баны:** {', '.join(self.bans) or '-'}\n"
            f"**Team A picks:** {', '.join(self.picks['A']) or '-'}\n"
            f"**Team B picks:** {', '.join(self.picks['B']) or '-'}\n\n"
            f"**Доступные clear:** {available_clear}\n"
            f"**Доступные eco:** {available_eco}"
        )

    async def handle_pick(self, interaction: discord.Interaction, clan: str) -> None:
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

        self.step_index += 1
        self.refresh_items()
        await interaction.response.edit_message(content=self.render(), view=self if self.current_step() else None)
        if self.current_step() is None:
            await self.on_finish(interaction, self.context)
            self.stop()


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
    def __init__(self, context: Team3MatchContext):
        super().__init__(timeout=3600)
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
            await interaction.response.send_message("Голос принят. Ждем подтверждения 2 игроков из каждой команды.", ephemeral=True)
            return

        self.finished = True
        await finish_team3_match(interaction, self.context, accepted)
        self.stop()

    @discord.ui.button(label="Победила Team A", style=discord.ButtonStyle.success)
    async def team_a_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.vote(interaction, self.context.team1_id)

    @discord.ui.button(label="Победила Team B", style=discord.ButtonStyle.success)
    async def team_b_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.vote(interaction, self.context.team2_id)


async def finish_team3_match(interaction: discord.Interaction, context: Team3MatchContext, winner_team_id: int) -> None:
    winner_label = "Team A" if winner_team_id == context.team1_id else "Team B"
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

    await cleanup_ranked_resources(context)
    rating_text = f"\nРейтинг: +{delta}/-{delta}" if context.game_mode == GameMode.RANKED else ""
    await interaction.response.send_message(
        f"Матч #{context.match_id} завершен.\nПобедитель: **{winner_label}**.{rating_text}"
    )


async def start_result_confirmation(channel: discord.abc.Messageable, context: Team3MatchContext) -> None:
    await channel.send(
        f"Матч #{context.match_id} начался.\n"
        f"После игры подтвердите победителя. Нужно минимум 2 голоса от каждой команды.",
        view=ResultConfirmView(context),
    )


async def start_team3_draft(channel: discord.abc.Messageable, context: Team3MatchContext) -> None:
    async def on_finish(interaction: discord.Interaction, match_context: Team3MatchContext) -> None:
        await start_result_confirmation(interaction.channel, match_context)

    view = Team3DraftView(context, on_finish)
    message = await channel.send(view.render(), view=view)
    view.message = message


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
    return Team3MatchContext(
        match_id=created.match.id,
        team1_id=created.team1.id,
        team2_id=created.team2.id,
        team1_members=team1_members,
        team2_members=team2_members,
        team1_user_ids=[user.id for user in team1_users],
        team2_user_ids=[user.id for user in team2_users],
        game_mode=game_mode,
    )


class CasualPartyModal(discord.ui.Modal, title="Зайти группой"):
    members = discord.ui.TextInput(
        label="Пинги игроков группы",
        placeholder="@player2 @player3",
        required=False,
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel_id is None or interaction.channel is None:
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return
        ids = [interaction.user.id, *[int(match) for match in MENTION_RE.findall(str(self.members))]]
        unique_ids = tuple(dict.fromkeys(ids))
        if not 1 <= len(unique_ids) <= 3:
            await interaction.response.send_message("Группа должна быть от 1 до 3 игроков.", ephemeral=True)
            return
        if any(is_in_casual_lobby(interaction.channel_id, discord_id) for discord_id in unique_ids):
            await interaction.response.send_message("Кто-то из этой группы уже находится в lobby.", ephemeral=True)
            return

        session_factory = get_session_factory()
        async with session_factory() as session:
            service = Team3Service(session)
            try:
                for discord_id in unique_ids:
                    await service.get_registered_user(discord_id)
            except ValueError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return

        lobby = casual_lobbies.setdefault(interaction.channel_id, [])
        if sum(len(group) for group in lobby) + len(unique_ids) > 6:
            await interaction.response.send_message("В lobby не хватает места для этой группы.", ephemeral=True)
            return
        lobby.append(unique_ids)
        await interaction.response.send_message(f"Группа добавлена в casual 3v3 lobby: {len(unique_ids)} игрок(а).", ephemeral=True)
        await maybe_start_casual_match(interaction)


class Team3PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Обычная 3v3", style=discord.ButtonStyle.primary)
    async def casual(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(CasualPartyModal())

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
        await interaction.response.send_message("Ты вышел из очереди/lobby.", ephemeral=True)


async def join_ranked_queue(interaction: discord.Interaction, wide: bool) -> None:
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message("Поиск доступен только на сервере.", ephemeral=True)
        return
    if not is_database_configured():
        await interaction.response.send_message("База данных не настроена.", ephemeral=True)
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
    await maybe_start_ranked_match(interaction)


async def maybe_start_ranked_match(interaction: discord.Interaction) -> None:
    if interaction.guild is None or interaction.channel is None:
        return
    split = find_best_ranked_match(list(ranked_queue.values()))
    if split is None:
        return

    for entry in [*split.team_a, *split.team_b]:
        ranked_queue.pop(entry.discord_id, None)

    team1_members = [await fetch_member(interaction.guild, entry.discord_id) for entry in split.team_a]
    team2_members = [await fetch_member(interaction.guild, entry.discord_id) for entry in split.team_b]
    if any(member is None for member in [*team1_members, *team2_members]):
        await interaction.channel.send("Не удалось найти всех игроков на сервере. Матч отменен.")
        return

    context = await build_context(
        interaction.guild,
        [member for member in team1_members if member is not None],
        [member for member in team2_members if member is not None],
        GameMode.RANKED,
    )
    try:
        await create_ranked_resources(interaction.guild, context)
    except discord.Forbidden:
        await interaction.channel.send("Матч создан, но у бота нет прав создать роли/голосовые каналы.")

    await interaction.channel.send(
        f"Ranked 3v3 найден. Match #{context.match_id}\n"
        f"Team A: {team_mentions(context.team1_members)}\n"
        f"Team B: {team_mentions(context.team2_members)}\n"
        f"Разница рейтинга команд: {split.rating_diff}"
    )
    await start_team3_draft(interaction.channel, context)


async def maybe_start_casual_match(interaction: discord.Interaction) -> None:
    if interaction.guild is None or interaction.channel is None or interaction.channel_id is None:
        return
    lobby = casual_lobbies.get(interaction.channel_id, [])
    if sum(len(group) for group in lobby) < 6:
        await interaction.channel.send(f"Casual 3v3 lobby: {sum(len(group) for group in lobby)}/6")
        return
    if sum(len(group) for group in lobby) > 6:
        return

    session_factory = get_session_factory()
    groups: list[CasualGroup] = []
    async with session_factory() as session:
        service = Team3Service(session)
        for group in lobby:
            users = [await service.get_registered_user(discord_id) for discord_id in group]
            groups.append(CasualGroup(tuple(users)))
    team1_users, team2_users = split_casual_groups(groups)
    team1_members = [await fetch_member(interaction.guild, user.discord_id) for user in team1_users]
    team2_members = [await fetch_member(interaction.guild, user.discord_id) for user in team2_users]
    if any(member is None for member in [*team1_members, *team2_members]):
        await interaction.channel.send("Не удалось найти всех игроков на сервере. Lobby очищено.")
        clear_lobby(interaction.channel_id)
        return

    context = await build_context(
        interaction.guild,
        [member for member in team1_members if member is not None],
        [member for member in team2_members if member is not None],
        GameMode.CASUAL,
    )
    clear_lobby(interaction.channel_id)
    await interaction.channel.send(
        f"Casual 3v3 собран. Match #{context.match_id}\n"
        f"Team A: {team_mentions(context.team1_members)}\n"
        f"Team B: {team_mentions(context.team2_members)}"
    )
    await start_team3_draft(interaction.channel, context)


def register(bot: commands.Bot, settings: Settings) -> None:
    @bot.tree.command(name="team3_panel", description="Панель 3v3 матчей")
    @app_commands.default_permissions(manage_guild=True)
    async def team3_panel(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "# Northgard 3v3\n"
            "Выбери режим кнопкой ниже.\n\n"
            "**Обычная 3v3** - lobby на 6 игроков, можно зайти solo/party до 3 человек.\n"
            "**Ranked 3v3** - поиск с ограничением рейтинга.\n"
            "**Ranked wide** - поиск с высоким разбросом рейтинга.",
            view=Team3PanelView(),
        )

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
        await start_team3_draft(interaction.channel, context)
