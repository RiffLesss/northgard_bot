import asyncio
import logging
import re
import time
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.database.session import get_session_factory, is_database_configured
from bot.models.enums import DraftActionType, PickType
from bot.models.ncl import NclTeam
from bot.repositories.clan_repository import ClanRepository
from bot.repositories.ncl_repository import NclTeamRepository
from bot.repositories.user_repository import UserRepository


logger = logging.getLogger(__name__)

NCL_CATEGORY_ID = 1526212599820062982
SCRIM_ACCEPT_SECONDS = 120
SCRIM_DRAFT_STEP_SECONDS = 120
SCRIM_DRAFT_TIMER_UPDATE_SECONDS = 5


@dataclass(frozen=True)
class ScrimDraftStep:
    side: str
    action_type: DraftActionType
    pick_type: PickType


@dataclass
class ScrimBan:
    side: str
    clan: str
    fearless: bool = False
    reverted: bool = False


@dataclass
class ScrimContext:
    guild: discord.Guild
    channel: discord.TextChannel
    team_a_role: discord.Role
    team_b_role: discord.Role
    team_a_members: list[discord.Member]
    team_b_members: list[discord.Member]
    clear_clans: list[str]
    eco_clans: list[str]
    game_number: int = 1
    team_a_score: int = 0
    team_b_score: int = 0
    team_a_previous_eco_picks: set[str] | None = None
    team_b_previous_eco_picks: set[str] | None = None
    team_a_magic_available: bool = True
    team_b_magic_available: bool = True

    def __post_init__(self) -> None:
        if self.team_a_previous_eco_picks is None:
            self.team_a_previous_eco_picks = set()
        if self.team_b_previous_eco_picks is None:
            self.team_b_previous_eco_picks = set()


SCRIM_DRAFT_STEPS = [
    ScrimDraftStep("A", DraftActionType.BAN, PickType.CLEAR),
    ScrimDraftStep("B", DraftActionType.BAN, PickType.CLEAR),
    ScrimDraftStep("B", DraftActionType.BAN, PickType.ECO),
    ScrimDraftStep("A", DraftActionType.BAN, PickType.ECO),
    ScrimDraftStep("A", DraftActionType.PICK, PickType.CLEAR),
    ScrimDraftStep("B", DraftActionType.PICK, PickType.CLEAR),
    ScrimDraftStep("B", DraftActionType.PICK, PickType.ECO),
    ScrimDraftStep("A", DraftActionType.PICK, PickType.ECO),
    ScrimDraftStep("A", DraftActionType.BAN, PickType.ECO),
    ScrimDraftStep("B", DraftActionType.BAN, PickType.ECO),
    ScrimDraftStep("A", DraftActionType.PICK, PickType.ECO),
    ScrimDraftStep("B", DraftActionType.PICK, PickType.ECO),
]

active_scrim_channels: set[int] = set()


class LoggedView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            "Unhandled scrim UI error: view=%s item=%s user_id=%s channel_id=%s",
            self.__class__.__name__,
            item.__class__.__name__,
            interaction.user.id if interaction.user else None,
            interaction.channel_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Something went wrong. The error has been logged.", ephemeral=True)
            else:
                await interaction.response.send_message("Something went wrong. The error has been logged.", ephemeral=True)
        except discord.HTTPException:
            logger.exception("Failed to notify user about scrim UI error")


def channel_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "-", name.strip().lower())
    return cleaned.strip("-")[:90] or "ncl-team"


def role_members(role: discord.Role) -> list[discord.Member]:
    return [member for member in role.members if not member.bot]


def team_mentions(members: list[discord.Member]) -> str:
    return " ".join(member.mention for member in members)


async def registered_users_for_members(members: list[discord.Member]) -> tuple[list[int], list[discord.Member]]:
    session_factory = get_session_factory()
    user_ids: list[int] = []
    missing: list[discord.Member] = []
    async with session_factory() as session:
        users = UserRepository(session)
        for member in members:
            user = await users.get_by_discord_id(member.id)
            if user is None:
                missing.append(member)
            else:
                user_ids.append(user.id)
    return user_ids, missing


async def ncl_team_for_member(member: discord.Member) -> NclTeam | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        users = UserRepository(session)
        user = await users.get_by_discord_id(member.id)
        if user is None:
            return None
        return await NclTeamRepository(session).get_for_user_id(user.id)


async def get_ncl_category(guild: discord.Guild) -> discord.CategoryChannel | None:
    category = guild.get_channel(NCL_CATEGORY_ID)
    if category is None:
        try:
            category = await guild.fetch_channel(NCL_CATEGORY_ID)
        except (discord.Forbidden, discord.NotFound):
            return None
    return category if isinstance(category, discord.CategoryChannel) else None


async def load_clan_pools() -> tuple[list[str], list[str]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        clans = ClanRepository(session)
        clear_clans = await clans.clear_names()
        all_clans = [clan.name for clan in await clans.list_enabled()]
    return clear_clans, [clan for clan in all_clans if clan not in clear_clans]


def side_role(context: ScrimContext, side: str) -> discord.Role:
    if context.game_number % 2 == 1:
        return context.team_a_role if side == "A" else context.team_b_role
    return context.team_b_role if side == "A" else context.team_a_role


def side_members(context: ScrimContext, side: str) -> list[discord.Member]:
    role = side_role(context, side)
    return context.team_a_members if role.id == context.team_a_role.id else context.team_b_members


def side_label(context: ScrimContext, side: str) -> str:
    return side_role(context, side).name


def wins_needed() -> int:
    return 3


def series_score(context: ScrimContext) -> str:
    return f"{context.team_a_role.name} {context.team_a_score}:{context.team_b_score} {context.team_b_role.name}"


class ScrimAcceptView(LoggedView):
    def __init__(self, challenger_role: discord.Role, target_role: discord.Role):
        super().__init__(timeout=SCRIM_ACCEPT_SECONDS)
        self.challenger_role = challenger_role
        self.target_role = target_role
        self.accepted = False

    @discord.ui.button(label="Accept scrim", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or self.target_role not in interaction.user.roles:
            await interaction.response.send_message("Only the challenged team can accept this scrim.", ephemeral=True)
            return
        self.accepted = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=(
                f"✅ Scrim accepted.\n"
                f"**{self.challenger_role.name}** vs **{self.target_role.name}**\n"
                "Starting bo5 draft."
            ),
            view=self,
        )
        self.stop()


class ScrimClanSelect(discord.ui.Select):
    def __init__(self, step: ScrimDraftStep, options: list[str]):
        select_options = [discord.SelectOption(label=clan, value=clan) for clan in options[:25]]
        super().__init__(
            placeholder=f"{step.side}: {step.action_type.value} {step.pick_type.value}",
            options=select_options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, ScrimDraftView):
            await view.handle_clan(interaction, self.values[0])


class ScrimMagicSelect(discord.ui.Select):
    def __init__(self, options: list[str]):
        select_options = [discord.SelectOption(label=clan, value=clan) for clan in options[:25]]
        super().__init__(placeholder="Use magic card to revert opponent ban", options=select_options)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, ScrimDraftView):
            await view.handle_magic(interaction, self.values[0])


class ScrimDraftView(LoggedView):
    def __init__(self, bot: commands.Bot, context: ScrimContext):
        super().__init__(timeout=None)
        self.bot = bot
        self.context = context
        self.step_index = 0
        self.bans: list[ScrimBan] = []
        self.picks: dict[str, list[str]] = {"A": [], "B": []}
        self.draft_results: list[str] = []
        self.message: discord.Message | None = None
        self.step_deadline = time.monotonic() + SCRIM_DRAFT_STEP_SECONDS
        self.timer_task: asyncio.Task | None = None
        self.finished = False
        self.lock = asyncio.Lock()
        self.refresh_items()

    def current_step(self) -> ScrimDraftStep | None:
        if self.step_index >= len(SCRIM_DRAFT_STEPS):
            return None
        return SCRIM_DRAFT_STEPS[self.step_index]

    def active_bans(self) -> set[str]:
        return {ban.clan for ban in self.bans if not ban.reverted}

    def opponent_revertable_bans(self, side: str) -> list[str]:
        return [
            ban.clan
            for ban in self.bans
            if ban.side != side and not ban.fearless and not ban.reverted
        ]

    def current_team_magic_available(self, side: str) -> bool:
        role = side_role(self.context, side)
        if role.id == self.context.team_a_role.id:
            return self.context.team_a_magic_available
        return self.context.team_b_magic_available

    def spend_magic(self, side: str) -> None:
        role = side_role(self.context, side)
        if role.id == self.context.team_a_role.id:
            self.context.team_a_magic_available = False
        else:
            self.context.team_b_magic_available = False

    def fearless_eco_blocked_for_side(self, side: str) -> set[str]:
        role = side_role(self.context, side)
        if role.id == self.context.team_a_role.id:
            return set(self.context.team_a_previous_eco_picks or set())
        return set(self.context.team_b_previous_eco_picks or set())

    def clan_pool(self, step: ScrimDraftStep) -> list[str]:
        source = self.context.clear_clans if step.pick_type == PickType.CLEAR else self.context.eco_clans
        unavailable = self.active_bans() | set(self.picks["A"]) | set(self.picks["B"])
        if step.action_type == DraftActionType.PICK and step.pick_type == PickType.ECO:
            unavailable |= self.fearless_eco_blocked_for_side(step.side)
        return [clan for clan in source if clan not in unavailable]

    def available_options(self, step: ScrimDraftStep) -> list[str]:
        options = self.clan_pool(step)
        if step.action_type == DraftActionType.PICK:
            options = [clan for clan in options if clan not in self.picks[step.side]]
        return options

    def refresh_items(self) -> None:
        self.clear_items()
        step = self.current_step()
        if step is None:
            return
        options = self.available_options(step)
        self.add_item(ScrimClanSelect(step, options))
        if step.action_type == DraftActionType.PICK and self.current_team_magic_available(step.side):
            magic_options = self.opponent_revertable_bans(step.side)
            if magic_options:
                self.add_item(ScrimMagicSelect(magic_options))

    def remaining_seconds(self) -> int:
        return max(0, int(self.step_deadline - time.monotonic()))

    def remaining_text(self) -> str:
        seconds = self.remaining_seconds()
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def format_clans(self, clans: list[str] | set[str]) -> str:
        return " · ".join(f"`{clan}`" for clan in clans) if clans else "-"

    def render_draft_table(self) -> str:
        lines = []
        for index, draft_step in enumerate(SCRIM_DRAFT_STEPS):
            label = f"{draft_step.side} {draft_step.action_type.value} {draft_step.pick_type.value}"
            if index < len(self.draft_results):
                icon = "🚫" if draft_step.action_type == DraftActionType.BAN else "✅"
                value = f"{icon} `{self.draft_results[index]}`"
            elif index == self.step_index:
                value = "⏳ waiting"
            else:
                value = "·"
            lines.append(f"{label:<13} -> {value}")
        return "\n".join(lines)

    def render_magic(self) -> str:
        a_status = "available" if self.context.team_a_magic_available else "used"
        b_status = "available" if self.context.team_b_magic_available else "used"
        return f"`{self.context.team_a_role.name}`: {a_status} · `{self.context.team_b_role.name}`: {b_status}"

    def render(self) -> str:
        step = self.current_step()
        if step is None:
            phase = "Draft finished"
            available = "-"
            action_team = "-"
        else:
            phase = f"{step.action_type.value} {step.pick_type.value}"
            action_team = f"Side {step.side} · {side_label(self.context, step.side)}"
            available = self.format_clans(self.available_options(step))
        active_bans = [ban.clan for ban in self.bans if not ban.reverted]
        reverted_bans = [ban.clan for ban in self.bans if ban.reverted]
        return (
            f"# ⚔️ NCL Scrim Draft · Game {self.context.game_number}\n\n"
            f"## 👥 Teams\n"
            f"**Series score:** {series_score(self.context)}\n"
            f"**Team A:** {self.context.team_a_role.mention} · {team_mentions(self.context.team_a_members)}\n"
            f"**Team B:** {self.context.team_b_role.mention} · {team_mentions(self.context.team_b_members)}\n"
            f"**Draft side A:** {side_label(self.context, 'A')}\n"
            f"**Draft side B:** {side_label(self.context, 'B')}\n\n"
            f"## ⏳ Current Action\n"
            f"**{action_team}** chooses: **{phase}**\n"
            f"Time left: **{self.remaining_text() if step is not None else '-'}**\n\n"
            f"## 🪄 Magic Cards\n"
            f"{self.render_magic()}\n\n"
            f"## 📋 Draft Table\n"
            f"```text\n{self.render_draft_table()}\n```\n"
            f"## 🚫 Bans\n"
            f"{self.format_clans(active_bans)}\n"
            f"**Reverted by magic:** {self.format_clans(reverted_bans)}\n\n"
            f"## ✅ Available For Current Action\n"
            f"{available}\n\n"
            f"⬇️ Choose from the menu below."
        )

    def allowed_user_ids(self, side: str) -> set[int]:
        return {member.id for member in side_members(self.context, side)}

    async def handle_clan(self, interaction: discord.Interaction, clan: str) -> None:
        async with self.lock:
            step = self.current_step()
            if step is None:
                await interaction.response.send_message("The draft is already finished.", ephemeral=True)
                return
            if interaction.user.id not in self.allowed_user_ids(step.side):
                await interaction.response.send_message(f"{side_label(self.context, step.side)} is choosing now.", ephemeral=True)
                return
            if clan not in self.available_options(step):
                await interaction.response.send_message("This clan is not available right now.", ephemeral=True)
                return
            await interaction.response.defer()
            self.cancel_timer()
            if step.action_type == DraftActionType.BAN:
                self.bans.append(ScrimBan(step.side, clan))
            else:
                self.picks[step.side].append(clan)
            self.draft_results.append(clan)
            self.step_index += 1
            await self.finish_or_continue(interaction)

    async def handle_magic(self, interaction: discord.Interaction, clan: str) -> None:
        async with self.lock:
            step = self.current_step()
            if step is None:
                await interaction.response.send_message("The draft is already finished.", ephemeral=True)
                return
            if step.action_type != DraftActionType.PICK:
                await interaction.response.send_message("Magic cards can only be used during your pick turn.", ephemeral=True)
                return
            if interaction.user.id not in self.allowed_user_ids(step.side):
                await interaction.response.send_message(f"{side_label(self.context, step.side)} is choosing now.", ephemeral=True)
                return
            if not self.current_team_magic_available(step.side):
                await interaction.response.send_message("Your team's magic card has already been used.", ephemeral=True)
                return
            for ban in self.bans:
                if ban.clan == clan and ban.side != step.side and not ban.fearless and not ban.reverted:
                    ban.reverted = True
                    self.spend_magic(step.side)
                    self.refresh_items()
                    await interaction.response.edit_message(content=self.render(), view=self)
                    return
            await interaction.response.send_message("This ban cannot be reverted.", ephemeral=True)

    async def auto_pick_current_step(self) -> None:
        async with self.lock:
            if self.finished:
                return
            step = self.current_step()
            if step is None:
                return
            options = self.available_options(step)
            if not options:
                await self.context.channel.send("No clans are available. Scrim draft cancelled.")
                self.finished = True
                self.stop()
                return
            clan = options[0]
            if step.action_type == DraftActionType.BAN:
                self.bans.append(ScrimBan(step.side, clan))
            else:
                self.picks[step.side].append(clan)
            self.draft_results.append(clan)
            self.step_index += 1
            await self.finish_or_continue()

    async def finish_or_continue(self, interaction: discord.Interaction | None = None) -> None:
        self.refresh_items()
        if self.current_step() is None:
            self.finished = True
            self.cancel_timer()
            await self.finish_draft(interaction)
            self.stop()
            return
        self.step_deadline = time.monotonic() + SCRIM_DRAFT_STEP_SECONDS
        self.start_timer()
        if interaction is not None:
            await interaction.edit_original_response(content=self.render(), view=self)
        elif self.message is not None:
            await self.message.edit(content=self.render(), view=self)

    async def finish_draft(self, interaction: discord.Interaction | None = None) -> None:
        for side, picks in self.picks.items():
            role = side_role(self.context, side)
            target = self.context.team_a_previous_eco_picks if role.id == self.context.team_a_role.id else self.context.team_b_previous_eco_picks
            assert target is not None
            target.update(pick for pick in picks if pick in self.context.eco_clans)
        if interaction is not None:
            await interaction.edit_original_response(content=self.render(), view=None)
        elif self.message is not None:
            await self.message.edit(content=self.render(), view=None)
        await self.context.channel.send(
            f"Game {self.context.game_number} draft finished.\n"
            f"Side A picks: {self.format_clans(self.picks['A'])}\n"
            f"Side B picks: {self.format_clans(self.picks['B'])}\n\n"
            "After the game, confirm the winner:",
            view=ScrimResultView(self.bot, self.context),
        )

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
                await asyncio.sleep(min(SCRIM_DRAFT_TIMER_UPDATE_SECONDS, remaining))
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


class ScrimResultView(LoggedView):
    def __init__(self, bot: commands.Bot, context: ScrimContext):
        super().__init__(timeout=86400)
        self.bot = bot
        self.context = context
        self.votes: dict[int, int] = {}
        self.finished = False

    def team_for_user(self, user_id: int) -> str | None:
        if user_id in {member.id for member in self.context.team_a_members}:
            return "A"
        if user_id in {member.id for member in self.context.team_b_members}:
            return "B"
        return None

    def accepted_winner(self) -> int | None:
        for role in [self.context.team_a_role, self.context.team_b_role]:
            a_vote = any(self.votes.get(member.id) == role.id for member in self.context.team_a_members)
            b_vote = any(self.votes.get(member.id) == role.id for member in self.context.team_b_members)
            if a_vote and b_vote:
                return role.id
        return None

    async def vote(self, interaction: discord.Interaction, winner_role: discord.Role) -> None:
        if self.finished:
            await interaction.response.send_message("The result has already been confirmed.", ephemeral=True)
            return
        if self.team_for_user(interaction.user.id) is None:
            await interaction.response.send_message("Only scrim participants can confirm the result.", ephemeral=True)
            return
        self.votes[interaction.user.id] = winner_role.id
        winner_id = self.accepted_winner()
        if winner_id is None:
            await interaction.response.send_message("Vote accepted. Waiting for confirmation from the other team.", ephemeral=True)
            return
        self.finished = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        if winner_id == self.context.team_a_role.id:
            self.context.team_a_score += 1
        else:
            self.context.team_b_score += 1
        if max(self.context.team_a_score, self.context.team_b_score) >= wins_needed():
            active_scrim_channels.discard(self.context.channel.id)
            await self.context.channel.send(
                f"🏁 Scrim finished.\n"
                f"Final score: **{series_score(self.context)}**."
            )
            self.stop()
            return
        self.context.game_number += 1
        await self.context.channel.send(
            f"Game result confirmed: **{winner_role.name}** won.\n"
            f"Series score: **{series_score(self.context)}**.\n"
            f"Starting Game {self.context.game_number} draft."
        )
        await start_scrim_draft(self.bot, self.context)
        self.stop()

    @discord.ui.button(label="Team A won", style=discord.ButtonStyle.success)
    async def team_a_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.vote(interaction, self.context.team_a_role)

    @discord.ui.button(label="Team B won", style=discord.ButtonStyle.success)
    async def team_b_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.vote(interaction, self.context.team_b_role)


async def start_scrim_draft(bot: commands.Bot, context: ScrimContext) -> None:
    view = ScrimDraftView(bot, context)
    message = await context.channel.send(view.render(), view=view)
    view.message = message
    view.start_timer()


async def create_scrim_channel(guild: discord.Guild, team_a: discord.Role, team_b: discord.Role) -> discord.TextChannel:
    category = await get_ncl_category(guild)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        team_a: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        team_b: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    return await guild.create_text_channel(
        name=f"{channel_name(team_a.name)}-vs-{channel_name(team_b.name)}",
        overwrites=overwrites,
        category=category,
        reason="NCL scrim",
    )


def register(bot: commands.Bot, settings: Settings) -> None:
    @bot.tree.command(name="add_ncl_team", description="Create an NCL team role and private channels")
    @app_commands.describe(
        team_name="Team name",
        player1="First player",
        player2="Second player",
        player3="Third player",
        player4="Optional fourth player",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def add_ncl_team(
        interaction: discord.Interaction,
        team_name: str,
        player1: discord.Member,
        player2: discord.Member,
        player3: discord.Member,
        player4: discord.Member | None = None,
    ) -> None:
        if not is_database_configured():
            await interaction.response.send_message("Database is not configured.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command is only available on a server.", ephemeral=True)
            return
        members = [player for player in [player1, player2, player3, player4] if player is not None]
        if len({member.id for member in members}) != len(members):
            await interaction.response.send_message("Team members must be unique.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        user_ids, missing_members = await registered_users_for_members(members)
        if missing_members:
            await interaction.followup.send(
                "All NCL team members must be registered with /register first. Missing: "
                f"{team_mentions(missing_members)}",
                ephemeral=True,
            )
            return
        session_factory = get_session_factory()
        async with session_factory() as session:
            teams = NclTeamRepository(session)
            existing_team = await teams.get_by_name(team_name)
            if existing_team is not None:
                await interaction.followup.send("An NCL team with this name already exists.", ephemeral=True)
                return
            existing_members = []
            for member, user_id in zip(members, user_ids, strict=True):
                if await teams.get_for_user_id(user_id) is not None:
                    existing_members.append(member)
            if existing_members:
                await interaction.followup.send(
                    "These players are already in an NCL team: "
                    f"{team_mentions(existing_members)}",
                    ephemeral=True,
                )
                return
        category = await get_ncl_category(interaction.guild)
        if category is None:
            await interaction.followup.send("NCL category was not found or is not a category.", ephemeral=True)
            return
        try:
            role = await interaction.guild.create_role(name=team_name, mentionable=True, reason="NCL team")
            for member in members:
                await member.add_roles(role, reason="NCL team")
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True, manage_channels=True),
                role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, connect=True),
            }
            text_channel = await interaction.guild.create_text_channel(
                name=channel_name(team_name),
                overwrites=overwrites,
                category=category,
                reason="NCL team",
            )
            voice_channel = await interaction.guild.create_voice_channel(
                name=team_name,
                overwrites=overwrites,
                category=category,
                reason="NCL team",
            )
        except discord.Forbidden:
            await interaction.followup.send("The bot does not have permission to create roles/channels or assign roles.", ephemeral=True)
            return

        async with session_factory() as session:
            teams = NclTeamRepository(session)
            await teams.create(
                team_name=team_name,
                discord_role_id=role.id,
                text_channel_id=text_channel.id,
                voice_channel_id=voice_channel.id,
                user_ids=user_ids,
            )
            await session.commit()
        await interaction.followup.send(
            f"NCL team created: {role.mention}\n"
            f"Members: {team_mentions(members)}\n"
            f"Text channel: {text_channel.mention}\n"
            f"Voice channel: {voice_channel.mention}",
            ephemeral=True,
        )

    @bot.tree.command(name="scrim", description="Challenge another NCL team to a bo5 scrim")
    @app_commands.describe(team_role="Team role to challenge")
    async def scrim(interaction: discord.Interaction, team_role: discord.Role) -> None:
        if not is_database_configured():
            await interaction.response.send_message("Database is not configured.", ephemeral=True)
            return
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command is only available on a server.", ephemeral=True)
            return
        challenger_team = await ncl_team_for_member(interaction.user)
        if challenger_team is None:
            await interaction.response.send_message("You are not a member of a registered NCL team.", ephemeral=True)
            return
        session_factory = get_session_factory()
        async with session_factory() as session:
            target_team = await NclTeamRepository(session).get_by_role_id(team_role.id)
        if target_team is None:
            await interaction.response.send_message("The challenged role is not a registered NCL team.", ephemeral=True)
            return
        if team_role.id == challenger_team.discord_role_id:
            await interaction.response.send_message("You cannot challenge your own team.", ephemeral=True)
            return
        if interaction.channel_id in active_scrim_channels:
            await interaction.response.send_message("There is already an active scrim in this channel.", ephemeral=True)
            return

        challenger_role = interaction.guild.get_role(challenger_team.discord_role_id)
        if challenger_role is None:
            await interaction.response.send_message("Your registered NCL team role was not found on Discord.", ephemeral=True)
            return
        challenger_members = role_members(challenger_role)
        target_members = role_members(team_role)
        if len(challenger_members) < 3 or len(target_members) < 3:
            await interaction.response.send_message("Both NCL teams must have at least 3 members.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            channel = await create_scrim_channel(interaction.guild, challenger_role, team_role)
        except discord.Forbidden:
            await interaction.followup.send("The bot cannot create a private scrim channel.", ephemeral=True)
            return

        view = ScrimAcceptView(challenger_role, team_role)
        await channel.send(
            f"{team_role.mention}, **{challenger_role.name}** challenged you to a bo5 scrim.\n"
            f"At least one member of {team_role.mention} must accept within 2 minutes.",
            view=view,
        )
        await interaction.followup.send(f"Scrim invite created: {channel.mention}", ephemeral=True)
        await view.wait()
        if not view.accepted:
            await channel.send("⌛ Scrim invite expired.")
            return

        clear_clans, eco_clans = await load_clan_pools()
        context = ScrimContext(
            guild=interaction.guild,
            channel=channel,
            team_a_role=challenger_role,
            team_b_role=team_role,
            team_a_members=challenger_members,
            team_b_members=target_members,
            clear_clans=clear_clans,
            eco_clans=eco_clans,
        )
        active_scrim_channels.add(channel.id)
        await start_scrim_draft(bot, context)
