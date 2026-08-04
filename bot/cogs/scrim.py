import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.database.session import get_session_factory, is_database_configured
from bot.models.enums import DraftActionType, PickType
from bot.models.nsl import NslTeam
from bot.repositories.clan_repository import ClanRepository
from bot.repositories.nsl_repository import NslTeamRepository
from bot.repositories.runtime_state_repository import RuntimeStateRepository
from bot.repositories.user_repository import UserRepository
from bot.services.draft_service import is_admin
from bot.services.nsl_service import generate_nsl_schedule, nsl_rating_update, next_monday


logger = logging.getLogger(__name__)

NSL_CATEGORY_ID = 1526212599820062982
NSL_LEADERBOARD_CHANNEL_ID = 1533831143034454127
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
    team_a_nsl_id: int
    team_b_nsl_id: int
    team_a_members: list[discord.Member]
    team_b_members: list[discord.Member]
    clear_clans: list[str]
    eco_clans: list[str]
    scheduled_match_id: int | None = None
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


def scrim_state(context: ScrimContext) -> dict:
    return {
        "guild_id": context.guild.id,
        "channel_id": context.channel.id,
        "team_a_role_id": context.team_a_role.id,
        "team_b_role_id": context.team_b_role.id,
        "team_a_nsl_id": context.team_a_nsl_id,
        "team_b_nsl_id": context.team_b_nsl_id,
        "team_a_member_ids": [member.id for member in context.team_a_members],
        "team_b_member_ids": [member.id for member in context.team_b_members],
        "clear_clans": context.clear_clans,
        "eco_clans": context.eco_clans,
        "scheduled_match_id": context.scheduled_match_id,
        "game_number": context.game_number,
        "team_a_score": context.team_a_score,
        "team_b_score": context.team_b_score,
        "team_a_previous_eco_picks": sorted(context.team_a_previous_eco_picks or set()),
        "team_b_previous_eco_picks": sorted(context.team_b_previous_eco_picks or set()),
        "team_a_magic_available": context.team_a_magic_available,
        "team_b_magic_available": context.team_b_magic_available,
    }


async def restore_scrim_context(bot: commands.Bot, state: dict) -> ScrimContext | None:
    guild = bot.get_guild(int(state["guild_id"]))
    if guild is None:
        return None
    channel = await bot.fetch_channel(int(state["channel_id"]))
    role_a = guild.get_role(int(state["team_a_role_id"]))
    role_b = guild.get_role(int(state["team_b_role_id"]))
    if not isinstance(channel, discord.TextChannel) or role_a is None or role_b is None:
        return None
    members_a = [await guild.fetch_member(int(member_id)) for member_id in state["team_a_member_ids"]]
    members_b = [await guild.fetch_member(int(member_id)) for member_id in state["team_b_member_ids"]]
    return ScrimContext(
        guild=guild, channel=channel, team_a_role=role_a, team_b_role=role_b,
        team_a_nsl_id=int(state["team_a_nsl_id"]), team_b_nsl_id=int(state["team_b_nsl_id"]),
        team_a_members=members_a, team_b_members=members_b,
        clear_clans=list(state["clear_clans"]), eco_clans=list(state["eco_clans"]),
        scheduled_match_id=state.get("scheduled_match_id"), game_number=int(state["game_number"]),
        team_a_score=int(state["team_a_score"]), team_b_score=int(state["team_b_score"]),
        team_a_previous_eco_picks=set(state.get("team_a_previous_eco_picks", [])),
        team_b_previous_eco_picks=set(state.get("team_b_previous_eco_picks", [])),
        team_a_magic_available=bool(state["team_a_magic_available"]),
        team_b_magic_available=bool(state["team_b_magic_available"]),
    )


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
active_nsl_match_ids: set[int] = set()
nsl_leaderboard_message_id: int | None = None
NSL_STATE_PREFIX = "nsl:"
NSL_INVITE_PREFIX = "nsl:invite:"
NSL_READY_PREFIX = "nsl:ready:"
_active_nsl_drafts: dict[int, "ScrimDraftView"] = {}
_active_nsl_results: dict[int, "ScrimResultView"] = {}


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
    return cleaned.strip("-")[:90] or "nsl-team"


async def fetch_nsl_team_members(guild: discord.Guild, team: NslTeam) -> list[discord.Member]:
    members: list[discord.Member] = []
    for team_member in team.members:
        member = guild.get_member(team_member.user.discord_id)
        if member is None:
            try:
                member = await guild.fetch_member(team_member.user.discord_id)
            except discord.NotFound:
                member = None
        if member is not None and not member.bot:
            members.append(member)
    return members


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


async def nsl_team_for_member(member: discord.Member) -> NslTeam | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        users = UserRepository(session)
        user = await users.get_by_discord_id(member.id)
        if user is None:
            return None
        return await NslTeamRepository(session).get_for_user_id(user.id)


async def get_nsl_category(guild: discord.Guild) -> discord.CategoryChannel | None:
    category = guild.get_channel(NSL_CATEGORY_ID)
    if category is None:
        try:
            category = await guild.fetch_channel(NSL_CATEGORY_ID)
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


def can_manage_nsl(member: discord.Member) -> bool:
    return is_admin(member) or member.guild_permissions.manage_guild


def is_match_participant(member: discord.Member, *roles: discord.Role) -> bool:
    return any(role in member.roles for role in roles)


def ordinal(day: int) -> str:
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def format_week_range(week_start: date, week_end: date) -> str:
    if week_start.month == week_end.month:
        return f"{week_start.strftime('%B')} {ordinal(week_start.day)} - {week_end.strftime('%B')} {ordinal(week_end.day)}"
    return f"{week_start.strftime('%B')} {ordinal(week_start.day)} - {week_end.strftime('%B')} {ordinal(week_end.day)}"


async def role_for_nsl_team(guild: discord.Guild, team: NslTeam) -> discord.Role | None:
    role = guild.get_role(team.discord_role_id)
    if role is not None:
        return role
    return None


class ScrimAcceptView(LoggedView):
    def __init__(self, challenger_role: discord.Role, target_role: discord.Role, state_key: str | None = None, workflow: dict | None = None):
        super().__init__(timeout=SCRIM_ACCEPT_SECONDS)
        self.challenger_role = challenger_role
        self.target_role = target_role
        self.accepted = False
        self.state_key = state_key
        self.workflow = workflow or {}
        self.message: discord.Message | None = None

    async def persist(self) -> None:
        if not is_database_configured() or self.message is None or self.state_key is None:
            return
        async with get_session_factory()() as session:
            await RuntimeStateRepository(session).put(self.state_key, {
                **self.workflow, "message_id": self.message.id, "channel_id": self.message.channel.id,
                "accepted": self.accepted,
            })
            await session.commit()

    @discord.ui.button(label="Accept scrim", style=discord.ButtonStyle.success, custom_id="nsl_invite:accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or self.target_role not in interaction.user.roles:
            await interaction.response.send_message("Only the challenged team can accept this scrim.", ephemeral=True)
            return
        self.accepted = True
        await self.persist()
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


class NslMatchReadyView(LoggedView):
    def __init__(self, team_a_role: discord.Role, team_b_role: discord.Role, state_key: str | None = None, workflow: dict | None = None):
        super().__init__(timeout=SCRIM_ACCEPT_SECONDS)
        self.team_a_role = team_a_role
        self.team_b_role = team_b_role
        self.team_a_ready = False
        self.team_b_ready = False
        self.state_key = state_key
        self.workflow = workflow or {}
        self.message: discord.Message | None = None

    async def persist(self) -> None:
        if not is_database_configured() or self.message is None or self.state_key is None:
            return
        async with get_session_factory()() as session:
            await RuntimeStateRepository(session).put(self.state_key, {
                **self.workflow, "message_id": self.message.id, "channel_id": self.message.channel.id,
                "team_a_ready": self.team_a_ready, "team_b_ready": self.team_b_ready,
            })
            await session.commit()

    def render(self) -> str:
        return (
            f"{self.team_a_role.mention} vs {self.team_b_role.mention}\n"
            "Scheduled NSL match ready-check.\n\n"
            f"**{self.team_a_role.name}:** {'ready' if self.team_a_ready else 'waiting'}\n"
            f"**{self.team_b_role.name}:** {'ready' if self.team_b_ready else 'waiting'}\n\n"
            "At least one player from each team must accept within 2 minutes."
        )

    def accepted(self) -> bool:
        return self.team_a_ready and self.team_b_ready

    @discord.ui.button(label="Ready", style=discord.ButtonStyle.success, custom_id="nsl_ready:accept")
    async def ready(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This button is only available on a server.", ephemeral=True)
            return
        if self.team_a_role in interaction.user.roles:
            self.team_a_ready = True
        elif self.team_b_role in interaction.user.roles:
            self.team_b_ready = True
        else:
            await interaction.response.send_message("Only match participants can accept this ready-check.", ephemeral=True)
            return
        await self.persist()
        if self.accepted():
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(content=self.render(), view=self)
            self.stop()
            return
        await interaction.response.edit_message(content=self.render(), view=self)


class ScrimClanSelect(discord.ui.Select):
    def __init__(self, step: ScrimDraftStep, options: list[str]):
        select_options = [discord.SelectOption(label=clan, value=clan) for clan in options[:25]]
        super().__init__(
            placeholder=f"{step.side}: {step.action_type.value} {step.pick_type.value}",
            custom_id=f"nsl_draft:{step.side}:{step.action_type.value}:{step.pick_type.value}",
            options=select_options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, ScrimDraftView):
            await view.handle_clan(interaction, self.values[0])


class ScrimMagicSelect(discord.ui.Select):
    def __init__(self, options: list[str]):
        select_options = [discord.SelectOption(label=clan, value=clan) for clan in options[:25]]
        super().__init__(placeholder="Use magic card to revert opponent ban", custom_id="nsl_draft:magic", options=select_options)

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

    async def persist(self) -> None:
        if not is_database_configured() or self.message is None:
            return
        state = scrim_state(self.context)
        state.update({
            "message_id": self.message.id,
            "step_index": self.step_index,
            "bans": [{"side": ban.side, "clan": ban.clan, "fearless": ban.fearless, "reverted": ban.reverted} for ban in self.bans],
            "picks": self.picks,
            "draft_results": self.draft_results,
            "deadline": time.time() + self.remaining_seconds(),
        })
        async with get_session_factory()() as session:
            await RuntimeStateRepository(session).put(f"{NSL_STATE_PREFIX}draft:{self.context.channel.id}", state)
            await session.commit()

    async def delete_state(self) -> None:
        if not is_database_configured():
            return
        async with get_session_factory()() as session:
            await RuntimeStateRepository(session).delete(f"{NSL_STATE_PREFIX}draft:{self.context.channel.id}")
            await session.commit()

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
        return [clan for clan in source if clan not in self.active_bans()]

    def available_options(self, step: ScrimDraftStep) -> list[str]:
        options = self.clan_pool(step)
        if step.action_type == DraftActionType.BAN:
            picked_clans = set(self.picks["A"]) | set(self.picks["B"])
            return [clan for clan in options if clan not in picked_clans]
        if step.action_type == DraftActionType.PICK:
            options = [clan for clan in options if clan not in self.picks[step.side]]
            if step.pick_type == PickType.ECO:
                options = [clan for clan in options if clan not in self.fearless_eco_blocked_for_side(step.side)]
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
            f"# ⚔️ NSL Scrim Draft · Game {self.context.game_number}\n\n"
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
            await self.delete_state()
            _active_nsl_drafts.pop(self.context.channel.id, None)
            self.stop()
            return
        self.step_deadline = time.monotonic() + SCRIM_DRAFT_STEP_SECONDS
        await self.persist()
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
        result_view = ScrimResultView(self.bot, self.context)
        result_message = await self.context.channel.send(
            f"Game {self.context.game_number} draft finished.\n"
            f"Side A picks: {self.format_clans(self.picks['A'])}\n"
            f"Side B picks: {self.format_clans(self.picks['B'])}\n\n"
            "After the game, confirm the winner:",
            view=result_view,
        )
        result_view.message = result_message
        _active_nsl_results[self.context.channel.id] = result_view
        await result_view.persist()

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
        self.message: discord.Message | None = None
        self.deadline = time.time() + 86400

    async def persist(self) -> None:
        if not is_database_configured() or self.message is None:
            return
        state = scrim_state(self.context)
        state.update({
            "message_id": self.message.id,
            "votes": {str(user_id): role_id for user_id, role_id in self.votes.items()},
            "deadline": self.deadline,
        })
        async with get_session_factory()() as session:
            await RuntimeStateRepository(session).put(f"{NSL_STATE_PREFIX}result:{self.context.channel.id}", state)
            await session.commit()

    async def delete_state(self) -> None:
        if not is_database_configured():
            return
        async with get_session_factory()() as session:
            await RuntimeStateRepository(session).delete(f"{NSL_STATE_PREFIX}result:{self.context.channel.id}")
            await session.commit()

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
        await self.persist()
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
            await self.delete_state()
            _active_nsl_results.pop(self.context.channel.id, None)
            await finish_scheduled_nsl_match_if_needed(self.bot, self.context)
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
        await self.delete_state()
        _active_nsl_results.pop(self.context.channel.id, None)
        self.stop()

    @discord.ui.button(label="Team A won", style=discord.ButtonStyle.success, custom_id="nsl_result:team_a")
    async def team_a_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.vote(interaction, self.context.team_a_role)

    @discord.ui.button(label="Team B won", style=discord.ButtonStyle.success, custom_id="nsl_result:team_b")
    async def team_b_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.vote(interaction, self.context.team_b_role)


async def start_scrim_draft(bot: commands.Bot, context: ScrimContext) -> None:
    view = ScrimDraftView(bot, context)
    message = await context.channel.send(view.render(), view=view)
    view.message = message
    _active_nsl_drafts[context.channel.id] = view
    await view.persist()
    view.start_timer()


async def restore_active_nsl_drafts(bot: commands.Bot) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        rows = await RuntimeStateRepository(session).list_prefix(f"{NSL_STATE_PREFIX}draft:")
    for _, state in rows:
        try:
            context = await restore_scrim_context(bot, state)
            if context is None or context.channel.id in _active_nsl_drafts:
                continue
            message = await context.channel.fetch_message(int(state["message_id"]))
            view = ScrimDraftView(bot, context)
            view.step_index = int(state["step_index"])
            view.bans = [ScrimBan(item["side"], item["clan"], bool(item["fearless"]), bool(item["reverted"])) for item in state["bans"]]
            view.picks = {side: list(values) for side, values in state["picks"].items()}
            view.draft_results = list(state["draft_results"])
            view.step_deadline = time.monotonic() + max(0, float(state["deadline"] - time.time()))
            view.refresh_items()
            view.message = message
            bot.add_view(view, message_id=message.id)
            _active_nsl_drafts[context.channel.id] = view
            view.start_timer()
        except (KeyError, TypeError, ValueError, discord.DiscordException):
            logger.exception("Failed to restore NSL scrim draft")


async def restore_active_nsl_results(bot: commands.Bot) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        rows = await RuntimeStateRepository(session).list_prefix(f"{NSL_STATE_PREFIX}result:")
    for _, state in rows:
        try:
            context = await restore_scrim_context(bot, state)
            if context is None or context.channel.id in _active_nsl_results:
                continue
            message = await context.channel.fetch_message(int(state["message_id"]))
            view = ScrimResultView(bot, context)
            view.votes = {int(user_id): int(role_id) for user_id, role_id in state.get("votes", {}).items()}
            view.deadline = float(state["deadline"])
            view.timeout = max(0, view.deadline - time.time())
            view.message = message
            bot.add_view(view, message_id=message.id)
            _active_nsl_results[context.channel.id] = view
        except (KeyError, TypeError, ValueError, discord.DiscordException):
            logger.exception("Failed to restore NSL scrim result")


async def _delete_nsl_state(key: str) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        await RuntimeStateRepository(session).delete(key)
        await session.commit()


async def stop_scrim_in_channel(channel: discord.TextChannel) -> bool:
    stopped = False
    draft = _active_nsl_drafts.pop(channel.id, None)
    if draft is not None:
        draft.finished = True
        draft.cancel_timer()
        await draft.delete_state()
        stopped = True
    result = _active_nsl_results.pop(channel.id, None)
    if result is not None:
        result.finished = True
        await result.delete_state()
        stopped = True
    active_scrim_channels.discard(channel.id)
    await _delete_nsl_state(f"{NSL_INVITE_PREFIX}{channel.id}")
    return stopped


async def restore_nsl_entry_views(bot: commands.Bot) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        rows = await RuntimeStateRepository(session).list_prefix(NSL_INVITE_PREFIX)
        rows += await RuntimeStateRepository(session).list_prefix(NSL_READY_PREFIX)
    for state_key, state in rows:
        try:
            guild = bot.get_guild(int(state["guild_id"]))
            if guild is None:
                continue
            channel = await bot.fetch_channel(int(state["channel_id"]))
            role_a = guild.get_role(int(state["team_a_role_id"]))
            role_b = guild.get_role(int(state["team_b_role_id"]))
            if not isinstance(channel, discord.TextChannel) or role_a is None or role_b is None:
                continue
            message = await channel.fetch_message(int(state["message_id"]))
            if state_key.startswith(NSL_INVITE_PREFIX):
                view = ScrimAcceptView(role_a, role_b, state_key, state)
                view.accepted = bool(state.get("accepted"))
                bot.add_view(view, message_id=message.id)
                view.message = message
                if view.accepted:
                    context = await restore_scrim_context(bot, state)
                    if context is not None:
                        context.clear_clans, context.eco_clans = await load_clan_pools()
                        await _delete_nsl_state(state_key)
                        await start_scrim_draft(bot, context)
            else:
                view = NslMatchReadyView(role_a, role_b, state_key, state)
                view.team_a_ready = bool(state.get("team_a_ready"))
                view.team_b_ready = bool(state.get("team_b_ready"))
                bot.add_view(view, message_id=message.id)
                view.message = message
                if view.accepted():
                    context = await restore_scrim_context(bot, state)
                    if context is not None:
                        context.clear_clans, context.eco_clans = await load_clan_pools()
                        await _delete_nsl_state(state_key)
                        await start_scrim_draft(bot, context)
        except (KeyError, TypeError, ValueError, discord.DiscordException):
            logger.exception("Failed to restore NSL entry workflow")


async def finish_scheduled_nsl_match_if_needed(bot: commands.Bot, context: ScrimContext) -> None:
    if context.scheduled_match_id is None:
        return
    active_nsl_match_ids.discard(context.scheduled_match_id)
    session_factory = get_session_factory()
    async with session_factory() as session:
        teams = NslTeamRepository(session)
        match = await teams.get_match_by_id(context.scheduled_match_id)
        if match is None or match.played_at is not None:
            return
        if context.team_a_nsl_id == match.team1_id:
            team1_wins = context.team_a_score
            team2_wins = context.team_b_score
        else:
            team1_wins = context.team_b_score
            team2_wins = context.team_a_score
        team1_elo_after, team2_elo_after = nsl_rating_update(
            match.team1.elo,
            match.team2.elo,
            team1_wins,
            team2_wins,
        )
        await teams.finish_match(
            match=match,
            winner_team_id=context.team_a_nsl_id if context.team_a_score > context.team_b_score else context.team_b_nsl_id,
            team1_game_wins=team1_wins,
            team2_game_wins=team2_wins,
            team1_elo_after=team1_elo_after,
            team2_elo_after=team2_elo_after,
        )
        await session.commit()
        await context.channel.send(
            "NSL scheduled match saved.\n"
            f"Elo: **{match.team1.team_name}** {match.team1_elo_before} -> {team1_elo_after}, "
            f"**{match.team2.team_name}** {match.team2_elo_before} -> {team2_elo_after}."
        )
    await refresh_nsl_leaderboard_message(bot)


async def create_scrim_channel(guild: discord.Guild, team_a: discord.Role, team_b: discord.Role) -> discord.TextChannel:
    category = await get_nsl_category(guild)
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
        reason="NSL scrim",
    )


def render_nsl_schedule(matches: list) -> str:
    if not matches:
        return "NSL schedule is empty."
    lines = ["# NSL Schedule"]
    weeks = sorted({match.week_number for match in matches})
    for week_number in weeks:
        week_matches = [match for match in matches if match.week_number == week_number]
        week_start = week_matches[0].week_start
        week_end = week_matches[0].week_end
        lines.append("")
        lines.append(f"## Week {week_number} ({format_week_range(week_start, week_end)})")
        team_ids = sorted({match.team1_id for match in week_matches} | {match.team2_id for match in week_matches})
        for team_id in team_ids:
            team_matches = [match for match in week_matches if match.team1_id == team_id or match.team2_id == team_id]
            team = team_matches[0].team1 if team_matches[0].team1_id == team_id else team_matches[0].team2
            lines.append("")
            lines.append(f"### {team.team_name}")
            for match in team_matches:
                status = " ✅" if match.played_at is not None else ""
                lines.append(f"<@&{match.team1.discord_role_id}> vs <@&{match.team2.discord_role_id}>{status}")
    return "\n".join(lines)


def nsl_leaderboard_rows(teams: list[NslTeam], matches: list) -> list[dict[str, object]]:
    stats = {
        team.id: {
            "team": team,
            "wins": 0,
            "losses": 0,
            "maps_won": 0,
            "maps_lost": 0,
        }
        for team in teams
    }
    for match in matches:
        if match.played_at is None or match.winner_team_id is None:
            continue
        if match.team1_id not in stats or match.team2_id not in stats:
            continue
        stats[match.team1_id]["maps_won"] += match.team1_game_wins
        stats[match.team1_id]["maps_lost"] += match.team2_game_wins
        stats[match.team2_id]["maps_won"] += match.team2_game_wins
        stats[match.team2_id]["maps_lost"] += match.team1_game_wins
        if match.winner_team_id == match.team1_id:
            stats[match.team1_id]["wins"] += 1
            stats[match.team2_id]["losses"] += 1
        elif match.winner_team_id == match.team2_id:
            stats[match.team2_id]["wins"] += 1
            stats[match.team1_id]["losses"] += 1

    rows = list(stats.values())
    rows.sort(
        key=lambda row: (
            -int(row["team"].elo),
            -int(row["wins"]),
            -(int(row["maps_won"]) - int(row["maps_lost"])),
            str(row["team"].team_name).lower(),
        )
    )
    return rows


def render_nsl_leaderboard(teams: list[NslTeam], matches: list) -> str:
    rows = nsl_leaderboard_rows(teams, matches)
    lines = [
        "# NSL Leaderboard",
        "",
        "```text",
        f"{'#':<3} {'Team':<24} {'Elo':>5} {'W':>3} {'L':>3} {'Maps':>6}",
    ]
    for index, row in enumerate(rows, start=1):
        team = row["team"]
        maps_diff = int(row["maps_won"]) - int(row["maps_lost"])
        lines.append(
            f"{index:<3} {team.team_name[:24]:<24} {team.elo:>5} "
            f"{int(row['wins']):>3} {int(row['losses']):>3} {maps_diff:>+6}"
        )
    lines.append("```")
    if not rows:
        lines.append("_No NSL teams yet._")
    return "\n".join(lines)


async def build_nsl_leaderboard_text() -> str:
    session_factory = get_session_factory()
    async with session_factory() as session:
        teams = NslTeamRepository(session)
        return render_nsl_leaderboard(await teams.list_teams(), await teams.list_matches())


async def find_existing_nsl_leaderboard_message(channel: discord.TextChannel, bot_user: discord.ClientUser | None) -> discord.Message | None:
    if bot_user is None:
        return None
    async for message in channel.history(limit=50):
        if message.author.id == bot_user.id and message.content.startswith("# NSL Leaderboard"):
            return message
    return None


async def upsert_nsl_leaderboard_message(bot: commands.Bot, channel: discord.TextChannel) -> discord.Message:
    global nsl_leaderboard_message_id
    text = await build_nsl_leaderboard_text()
    message: discord.Message | None = None
    if nsl_leaderboard_message_id is not None:
        try:
            message = await channel.fetch_message(nsl_leaderboard_message_id)
        except (discord.Forbidden, discord.NotFound):
            message = None
    if message is None:
        message = await find_existing_nsl_leaderboard_message(channel, bot.user)
    if message is None:
        message = await channel.send(text)
    else:
        await message.edit(content=text)
    nsl_leaderboard_message_id = message.id
    return message


async def refresh_nsl_leaderboard_message(bot: commands.Bot) -> None:
    channel = bot.get_channel(NSL_LEADERBOARD_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(NSL_LEADERBOARD_CHANNEL_ID)
        except (discord.Forbidden, discord.NotFound):
            return
    if not isinstance(channel, discord.TextChannel):
        return
    await upsert_nsl_leaderboard_message(bot, channel)


async def send_long_response(interaction: discord.Interaction, content: str) -> None:
    chunks = []
    current = ""
    for line in content.splitlines():
        next_value = f"{current}\n{line}" if current else line
        if len(next_value) > 1900:
            chunks.append(current)
            current = line
        else:
            current = next_value
    if current:
        chunks.append(current)

    if not chunks:
        chunks = ["-"]
    if interaction.response.is_done():
        await interaction.followup.send(chunks[0])
    else:
        await interaction.response.send_message(chunks[0])
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk)


def register(bot: commands.Bot, settings: Settings) -> None:
    async def restore_persistent_nsl_state() -> None:
        await restore_nsl_entry_views(bot)
        await restore_active_nsl_drafts(bot)
        await restore_active_nsl_results(bot)

    bot.add_listener(restore_persistent_nsl_state, "on_ready")

    @bot.tree.command(name="stop_scrim", description="Stop the active NSL scrim in this channel")
    async def stop_scrim(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not can_manage_nsl(interaction.user):
            await interaction.response.send_message("Only an organizer or bot admin can stop a scrim.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This command is only available in a text channel.", ephemeral=True)
            return
        stopped = await stop_scrim_in_channel(interaction.channel)
        await interaction.response.send_message(
            "The scrim was stopped and its saved state was cleared." if stopped else "There is no active scrim in this channel.",
            ephemeral=True,
        )

    @bot.tree.command(name="add_nsl_team", description="Create an NSL team role and private channels")
    @app_commands.describe(
        team_name="Team name",
        player1="First player",
        player2="Second player",
        player3="Third player",
        player4="Optional fourth player",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def add_nsl_team(
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
                "All NSL team members must be registered with /register first. Missing: "
                f"{team_mentions(missing_members)}",
                ephemeral=True,
            )
            return
        session_factory = get_session_factory()
        async with session_factory() as session:
            teams = NslTeamRepository(session)
            existing_team = await teams.get_by_name(team_name)
            if existing_team is not None:
                await interaction.followup.send("An NSL team with this name already exists.", ephemeral=True)
                return
            existing_members = []
            for member, user_id in zip(members, user_ids, strict=True):
                if await teams.get_for_user_id(user_id) is not None:
                    existing_members.append(member)
            if existing_members:
                await interaction.followup.send(
                    "These players are already in an NSL team: "
                    f"{team_mentions(existing_members)}",
                    ephemeral=True,
                )
                return
        category = await get_nsl_category(interaction.guild)
        if category is None:
            await interaction.followup.send("NSL category was not found or is not a category.", ephemeral=True)
            return
        try:
            role = await interaction.guild.create_role(name=team_name, mentionable=True, reason="NSL team")
            for member in members:
                await member.add_roles(role, reason="NSL team")
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True, manage_channels=True),
                role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, connect=True),
            }
            text_channel = await interaction.guild.create_text_channel(
                name=channel_name(team_name),
                overwrites=overwrites,
                category=category,
                reason="NSL team",
            )
            voice_channel = await interaction.guild.create_voice_channel(
                name=team_name,
                overwrites=overwrites,
                category=category,
                reason="NSL team",
            )
        except discord.Forbidden:
            await interaction.followup.send("The bot does not have permission to create roles/channels or assign roles.", ephemeral=True)
            return

        async with session_factory() as session:
            teams = NslTeamRepository(session)
            await teams.create(
                team_name=team_name,
                discord_role_id=role.id,
                text_channel_id=text_channel.id,
                voice_channel_id=voice_channel.id,
                user_ids=user_ids,
            )
            await session.commit()
        await interaction.followup.send(
            f"NSL team created: {role.mention}\n"
            f"Members: {team_mentions(members)}\n"
            f"Text channel: {text_channel.mention}\n"
            f"Voice channel: {voice_channel.mention}",
            ephemeral=True,
        )

    @bot.tree.command(name="create_schedule", description="Generate NSL double round-robin schedule")
    @app_commands.default_permissions(manage_guild=True)
    async def create_schedule(interaction: discord.Interaction) -> None:
        if not is_database_configured():
            await interaction.response.send_message("Database is not configured.", ephemeral=True)
            return
        await interaction.response.defer()
        session_factory = get_session_factory()
        async with session_factory() as session:
            teams = NslTeamRepository(session)
            if await teams.has_played_matches():
                await interaction.followup.send("Cannot recreate schedule after at least one NSL match has been played.")
                return
            nsl_teams = await teams.list_teams()
            try:
                generated = generate_nsl_schedule(
                    [team.id for team in nsl_teams],
                    next_monday(date.today()),
                )
            except ValueError as error:
                await interaction.followup.send(str(error))
                return
            await teams.clear_schedule()
            for match in generated:
                await teams.create_match(
                    week_number=match.week_number,
                    week_start=match.week_start,
                    week_end=match.week_end,
                    team1_id=match.team1_id,
                    team2_id=match.team2_id,
                )
            await session.commit()
            matches = await teams.list_matches()
        await send_long_response(interaction, render_nsl_schedule(matches))

    @bot.tree.command(name="nsl_schedule", description="Show current NSL schedule")
    async def nsl_schedule(interaction: discord.Interaction) -> None:
        if not is_database_configured():
            await interaction.response.send_message("Database is not configured.", ephemeral=True)
            return
        await interaction.response.defer()
        session_factory = get_session_factory()
        async with session_factory() as session:
            matches = await NslTeamRepository(session).list_matches()
        await send_long_response(interaction, render_nsl_schedule(matches))

    @bot.tree.command(name="nsl_leaderboard", description="Show NSL team leaderboard")
    async def nsl_leaderboard(interaction: discord.Interaction) -> None:
        if not is_database_configured():
            await interaction.response.send_message("Database is not configured.", ephemeral=True)
            return
        if interaction.channel_id != NSL_LEADERBOARD_CHANNEL_ID:
            await interaction.response.send_message(
                f"NSL leaderboard can only be used in <#{NSL_LEADERBOARD_CHANNEL_ID}>.",
                ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This command is only available in a text channel.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        message = await upsert_nsl_leaderboard_message(bot, interaction.channel)
        await interaction.followup.send(f"NSL leaderboard updated: {message.jump_url}", ephemeral=True)

    @bot.tree.command(name="start_match", description="Start this week's scheduled NSL match")
    @app_commands.describe(team1="First NSL team role", team2="Second NSL team role")
    async def start_match(interaction: discord.Interaction, team1: discord.Role, team2: discord.Role) -> None:
        if not is_database_configured():
            await interaction.response.send_message("Database is not configured.", ephemeral=True)
            return
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command is only available on a server.", ephemeral=True)
            return
        if team1.id == team2.id:
            await interaction.response.send_message("Choose two different teams.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        session_factory = get_session_factory()
        async with session_factory() as session:
            teams = NslTeamRepository(session)
            nsl_team1 = await teams.get_by_role_id(team1.id)
            nsl_team2 = await teams.get_by_role_id(team2.id)
            if nsl_team1 is None or nsl_team2 is None:
                await interaction.followup.send("Both roles must be registered NSL teams.", ephemeral=True)
                return
            match = await teams.find_current_week_match(nsl_team1.id, nsl_team2.id, date.today())
        if match is None:
            await interaction.followup.send("There is no unplayed scheduled match between these teams this week.", ephemeral=True)
            return
        if match.id in active_nsl_match_ids:
            await interaction.followup.send("This scheduled match is already active.", ephemeral=True)
            return
        if not can_manage_nsl(interaction.user) and not is_match_participant(interaction.user, team1, team2):
            await interaction.followup.send("Only an organizer or a participant of one of these teams can start this match.", ephemeral=True)
            return
        team1_members = await fetch_nsl_team_members(interaction.guild, nsl_team1)
        team2_members = await fetch_nsl_team_members(interaction.guild, nsl_team2)
        if len(team1_members) < 3 or len(team2_members) < 3:
            await interaction.followup.send(
                "Both NSL teams must have at least 3 registered server members in the database.",
                ephemeral=True,
            )
            return

        try:
            channel = await create_scrim_channel(interaction.guild, team1, team2)
        except discord.Forbidden:
            await interaction.followup.send("The bot cannot create a private match channel.", ephemeral=True)
            return

        active_nsl_match_ids.add(match.id)
        state_key = f"{NSL_READY_PREFIX}{match.id}"
        view = NslMatchReadyView(team1, team2, state_key, {
            "guild_id": interaction.guild.id, "team_a_role_id": team1.id, "team_b_role_id": team2.id,
            "team_a_nsl_id": nsl_team1.id, "team_b_nsl_id": nsl_team2.id,
            "team_a_member_ids": [member.id for member in team1_members],
            "team_b_member_ids": [member.id for member in team2_members],
            "clear_clans": [], "eco_clans": [], "scheduled_match_id": match.id,
            "game_number": 1, "team_a_score": 0, "team_b_score": 0,
            "team_a_previous_eco_picks": [], "team_b_previous_eco_picks": [],
            "team_a_magic_available": True, "team_b_magic_available": True,
        })
        message = await channel.send(view.render(), view=view)
        view.message = message
        await view.persist()
        await interaction.followup.send(f"NSL match ready-check created: {channel.mention}", ephemeral=True)
        await view.wait()
        await _delete_nsl_state(state_key)
        if not view.accepted():
            active_nsl_match_ids.discard(match.id)
            await channel.send("NSL match ready-check expired.")
            return

        clear_clans, eco_clans = await load_clan_pools()
        context = ScrimContext(
            guild=interaction.guild,
            channel=channel,
            team_a_role=team1,
            team_b_role=team2,
            team_a_nsl_id=nsl_team1.id,
            team_b_nsl_id=nsl_team2.id,
            team_a_members=team1_members,
            team_b_members=team2_members,
            clear_clans=clear_clans,
            eco_clans=eco_clans,
            scheduled_match_id=match.id,
        )
        active_scrim_channels.add(channel.id)
        await start_scrim_draft(bot, context)

    @bot.tree.command(name="scrim", description="Challenge another NSL team to a bo5 scrim")
    @app_commands.describe(team_role="Team role to challenge")
    async def scrim(interaction: discord.Interaction, team_role: discord.Role) -> None:
        if not is_database_configured():
            await interaction.response.send_message("Database is not configured.", ephemeral=True)
            return
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command is only available on a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        challenger_team = await nsl_team_for_member(interaction.user)
        if challenger_team is None:
            await interaction.followup.send("You are not a member of a registered NSL team.", ephemeral=True)
            return
        session_factory = get_session_factory()
        async with session_factory() as session:
            target_team = await NslTeamRepository(session).get_by_role_id(team_role.id)
        if target_team is None:
            await interaction.followup.send("The challenged role is not a registered NSL team.", ephemeral=True)
            return
        if team_role.id == challenger_team.discord_role_id:
            await interaction.followup.send("You cannot challenge your own team.", ephemeral=True)
            return
        if interaction.channel_id in active_scrim_channels:
            await interaction.followup.send("There is already an active scrim in this channel.", ephemeral=True)
            return

        challenger_role = interaction.guild.get_role(challenger_team.discord_role_id)
        if challenger_role is None:
            await interaction.followup.send("Your registered NSL team role was not found on Discord.", ephemeral=True)
            return
        challenger_members = await fetch_nsl_team_members(interaction.guild, challenger_team)
        target_members = await fetch_nsl_team_members(interaction.guild, target_team)
        if len(challenger_members) < 3 or len(target_members) < 3:
            await interaction.followup.send(
                "Both NSL teams must have at least 3 registered server members in the database.",
                ephemeral=True,
            )
            return

        try:
            channel = await create_scrim_channel(interaction.guild, challenger_role, team_role)
        except discord.Forbidden:
            await interaction.followup.send("The bot cannot create a private scrim channel.", ephemeral=True)
            return

        state_key = f"{NSL_INVITE_PREFIX}{channel.id}"
        workflow = {
            "guild_id": interaction.guild.id, "team_a_role_id": challenger_role.id, "team_b_role_id": team_role.id,
            "team_a_nsl_id": challenger_team.id, "team_b_nsl_id": target_team.id,
            "team_a_member_ids": [member.id for member in challenger_members],
            "team_b_member_ids": [member.id for member in target_members],
            "clear_clans": [], "eco_clans": [], "scheduled_match_id": None,
            "game_number": 1, "team_a_score": 0, "team_b_score": 0,
            "team_a_previous_eco_picks": [], "team_b_previous_eco_picks": [],
            "team_a_magic_available": True, "team_b_magic_available": True,
        }
        view = ScrimAcceptView(challenger_role, team_role, state_key, workflow)
        message = await channel.send(
            f"{team_role.mention}, **{challenger_role.name}** challenged you to a bo5 scrim.\n"
            f"At least one member of {team_role.mention} must accept within 2 minutes.",
            view=view,
        )
        view.message = message
        await view.persist()
        await interaction.followup.send(f"Scrim invite created: {channel.mention}", ephemeral=True)
        await view.wait()
        await _delete_nsl_state(state_key)
        if not view.accepted:
            await channel.send("⌛ Scrim invite expired.")
            return

        clear_clans, eco_clans = await load_clan_pools()
        context = ScrimContext(
            guild=interaction.guild,
            channel=channel,
            team_a_role=challenger_role,
            team_b_role=team_role,
            team_a_nsl_id=challenger_team.id,
            team_b_nsl_id=target_team.id,
            team_a_members=challenger_members,
            team_b_members=target_members,
            clear_clans=clear_clans,
            eco_clans=eco_clans,
        )
        active_scrim_channels.add(channel.id)
        await start_scrim_draft(bot, context)
