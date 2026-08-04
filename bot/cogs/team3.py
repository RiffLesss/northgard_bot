import asyncio
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.database.session import get_session_factory, is_database_configured
from bot.models.enums import BestOf, DraftActionType, GameMode, PickType
from bot.models.user import User
from bot.repositories.runtime_state_repository import RuntimeStateRepository
from bot.services.draft_service import is_admin
from bot.services.team3_service import (
    TEAM3_DRAFT_STEPS,
    QueueEntry,
    Team3DraftStep,
    Team3Service,
    find_best_ranked_match,
    split_casual_players,
)


logger = logging.getLogger(__name__)

DISPUTE_CHANNEL_ID = 1520167921194766518
TEAM3_ANNOUNCEMENTS_CHANNEL_ID = 1521152955590508764
TEAM3_AFTER_MATCH_VOICE_CHANNEL_ID = 1527279399005589554
DRAFT_STEP_SECONDS = 120
DRAFT_TIMER_UPDATE_SECONDS = 5
ranked_queue: dict[int, QueueEntry] = {}
casual_lobbies: dict[int, list[tuple[int, ...]]] = {}
pending_ready_checks: set[int] = set()
team3_panel_messages: dict[int, int] = {}
active_team3_players: set[int] = set()
TEAM3_STATE_KEY = "team3"
TEAM3_DRAFT_PREFIX = "team3:draft:"
TEAM3_RESULT_PREFIX = "team3:result:"
TEAM3_DISPUTE_PREFIX = "team3:dispute:"
TEAM3_READY_PREFIX = "team3:ready:"
_team3_bot: commands.Bot | None = None
_active_team3_drafts: dict[int, "Team3DraftView"] = {}
_active_team3_results: dict[int, "ResultConfirmView"] = {}
_active_team3_disputes: dict[int, "DisputeResolveView"] = {}
_active_team3_ready_checks: dict[int, "ReadyCheckView"] = {}


def _team3_state() -> dict:
    return {
        "panel_messages": {str(channel_id): message_id for channel_id, message_id in team3_panel_messages.items()},
        "ranked_queue": {
            str(discord_id): {
                "user_id": entry.user_id,
                "discord_id": entry.discord_id,
                "nickname": entry.nickname,
                "rating": entry.rating,
                "wide": entry.wide,
                "joined_at": entry.joined_at.isoformat(),
            }
            for discord_id, entry in ranked_queue.items()
        },
        "casual_lobbies": {str(channel_id): [list(group) for group in groups] for channel_id, groups in casual_lobbies.items()},
        "active_players": sorted(active_team3_players),
    }


def schedule_team3_state_save() -> None:
    if _team3_bot is not None and _team3_bot.is_ready():
        _team3_bot.loop.create_task(_save_team3_state())


async def _save_team3_state() -> None:
    if not is_database_configured():
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        await RuntimeStateRepository(session).put(TEAM3_STATE_KEY, _team3_state())
        await session.commit()


async def restore_team3_state() -> None:
    if not is_database_configured():
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        state = await RuntimeStateRepository(session).get(TEAM3_STATE_KEY)
    if not state:
        return
    team3_panel_messages.clear()
    team3_panel_messages.update({int(k): int(v) for k, v in state.get("panel_messages", {}).items()})
    ranked_queue.clear()
    for raw in state.get("ranked_queue", {}).values():
        ranked_queue[int(raw["discord_id"])] = QueueEntry(
            user_id=int(raw["user_id"]),
            discord_id=int(raw["discord_id"]),
            nickname=str(raw["nickname"]),
            rating=int(raw["rating"]),
            wide=bool(raw["wide"]),
            joined_at=datetime.fromisoformat(raw["joined_at"]),
        )
    casual_lobbies.clear()
    casual_lobbies.update({int(k): [tuple(int(player) for player in group) for group in groups] for k, groups in state.get("casual_lobbies", {}).items()})
    active_team3_players.clear()
    active_team3_players.update(int(player) for player in state.get("active_players", []))


def _draft_context_state(context: "Team3MatchContext") -> dict:
    return {
        "guild_id": context.team1_members[0].guild.id,
        "match_id": context.match_id,
        "team1_id": context.team1_id,
        "team2_id": context.team2_id,
        "team1_user_ids": context.team1_user_ids,
        "team2_user_ids": context.team2_user_ids,
        "team1_members": [member.id for member in context.team1_members],
        "team2_members": [member.id for member in context.team2_members],
        "game_mode": context.game_mode.value,
        "best_of": context.best_of.value,
        "game_number": context.game_number,
        "team1_score": context.team1_score,
        "team2_score": context.team2_score,
        "clear_clans": context.clear_clans or [],
        "eco_clans": context.eco_clans or [],
        "text_channel_id": context.text_channel.id if context.text_channel else None,
        "team1_role_id": context.team1_role.id if context.team1_role else None,
        "team2_role_id": context.team2_role.id if context.team2_role else None,
        "team1_voice_id": context.team1_channel.id if context.team1_channel else None,
        "team2_voice_id": context.team2_channel.id if context.team2_channel else None,
        "managed_text_channel": context.managed_text_channel,
    }


async def _delete_team3_draft_state(match_id: int) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        await RuntimeStateRepository(session).delete(f"{TEAM3_DRAFT_PREFIX}{match_id}")
        await session.commit()


async def _context_from_state(bot: commands.Bot, state: dict) -> tuple["Team3MatchContext", discord.TextChannel] | None:
    guild = bot.get_guild(int(state["guild_id"]))
    if guild is None:
        return None
    members_a = [await fetch_member(guild, int(member_id)) for member_id in state["team1_members"]]
    members_b = [await fetch_member(guild, int(member_id)) for member_id in state["team2_members"]]
    if any(member is None for member in [*members_a, *members_b]):
        return None
    channel = await bot.fetch_channel(int(state["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        return None
    context = Team3MatchContext(
        match_id=int(state["match_id"]), team1_id=int(state["team1_id"]), team2_id=int(state["team2_id"]),
        team1_members=[member for member in members_a if member is not None],
        team2_members=[member for member in members_b if member is not None],
        team1_user_ids=[int(value) for value in state["team1_user_ids"]],
        team2_user_ids=[int(value) for value in state["team2_user_ids"]],
        game_mode=GameMode(state["game_mode"]), best_of=BestOf(state["best_of"]),
        game_number=int(state["game_number"]), team1_score=int(state["team1_score"]),
        team2_score=int(state["team2_score"]), clear_clans=list(state["clear_clans"]),
        eco_clans=list(state["eco_clans"]), text_channel=channel,
        managed_text_channel=bool(state["managed_text_channel"]),
    )
    return context, channel


async def restore_team3_drafts(bot: commands.Bot) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        rows = await RuntimeStateRepository(session).list_prefix(TEAM3_DRAFT_PREFIX)
    for _, state in rows:
        try:
            match_id = int(state["match_id"])
            if match_id in _active_team3_drafts:
                continue
            restored = await _context_from_state(bot, state)
            if restored is None:
                continue
            context, channel = restored
            view = Team3DraftView(context, channel, lambda result_channel, match_context: start_result_confirmation(bot, result_channel, match_context))
            view.step_index = int(state["step_index"])
            view.bans = list(state["bans"])
            view.picks = {side: list(values) for side, values in state["picks"].items()}
            view.draft_results = list(state["draft_results"])
            view.step_deadline = time.monotonic() + max(0, float(state["deadline"] - time.time()))
            view.refresh_items()
            message = await channel.fetch_message(int(state["message_id"]))
            view.message = message
            bot.add_view(view, message_id=message.id)
            _active_team3_drafts[context.match_id] = view
            view.start_timer()
        except (KeyError, TypeError, ValueError, discord.DiscordException):
            logger.exception("Failed to restore 3v3 draft state")


async def _delete_team3_result_state(match_id: int) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        await RuntimeStateRepository(session).delete(f"{TEAM3_RESULT_PREFIX}{match_id}")
        await session.commit()


async def restore_team3_results(bot: commands.Bot) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        rows = await RuntimeStateRepository(session).list_prefix(TEAM3_RESULT_PREFIX)
    for _, state in rows:
        try:
            restored = await _context_from_state(bot, state)
            if restored is None:
                continue
            context, channel = restored
            message = await channel.fetch_message(int(state["message_id"]))
            view = ResultConfirmView(bot, context)
            view.votes = {int(user_id): int(team_id) for user_id, team_id in state.get("votes", {}).items()}
            view.timeout = max(0, float(state["deadline"] - time.time()))
            view.deadline = float(state["deadline"])
            bot.add_view(view, message_id=message.id)
            view.message = message
            _active_team3_results[context.match_id] = view
        except (KeyError, TypeError, ValueError, discord.DiscordException):
            logger.exception("Failed to restore 3v3 result confirmation")


async def _delete_team3_dispute_state(match_id: int) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        await RuntimeStateRepository(session).delete(f"{TEAM3_DISPUTE_PREFIX}{match_id}")
        await session.commit()


async def restore_team3_disputes(bot: commands.Bot) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        rows = await RuntimeStateRepository(session).list_prefix(TEAM3_DISPUTE_PREFIX)
    for _, state in rows:
        try:
            match_id = int(state["match_id"])
            if match_id in _active_team3_disputes:
                continue
            restored = await _context_from_state(bot, state)
            if restored is None:
                continue
            context, _ = restored
            channel = bot.get_channel(int(state["dispute_channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                channel = await bot.fetch_channel(int(state["dispute_channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                continue
            message = await channel.fetch_message(int(state["message_id"]))
            view = DisputeResolveView(bot, context)
            view.votes = {int(user_id): int(team_id) for user_id, team_id in state["votes"].items()}
            view.message = message
            bot.add_view(view, message_id=message.id)
            _active_team3_disputes[match_id] = view
        except (KeyError, TypeError, ValueError, discord.DiscordException):
            logger.exception("Failed to restore 3v3 dispute")


async def _delete_team3_ready_state(state_key: str) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        await RuntimeStateRepository(session).delete(state_key)
        await session.commit()


async def resume_team3_ready_match(bot: commands.Bot, state: dict) -> None:
    guild = bot.get_guild(int(state["guild_id"]))
    if guild is None:
        return
    source = await bot.fetch_channel(int(state["source_channel_id"]))
    if not isinstance(source, discord.TextChannel):
        return
    team_a = [await fetch_member(guild, int(member_id)) for member_id in state["team_a_ids"]]
    team_b = [await fetch_member(guild, int(member_id)) for member_id in state["team_b_ids"]]
    if any(member is None for member in [*team_a, *team_b]):
        return
    members_a = [member for member in team_a if member is not None]
    members_b = [member for member in team_b if member is not None]
    if state["mode"] == GameMode.RANKED.value:
        for member in [*members_a, *members_b]:
            ranked_queue.pop(member.id, None)
    active_team3_players.update(member.id for member in [*members_a, *members_b])
    remove_from_all_searches({member.id for member in [*members_a, *members_b]})
    await update_team3_panel(source, source.id)
    context = await build_context(guild, members_a, members_b, GameMode(state["mode"]), BestOf(state["best_of"]))
    ready_channel = await bot.fetch_channel(int(state["ready_channel_id"]))
    if isinstance(ready_channel, discord.TextChannel):
        context.text_channel = ready_channel
        context.managed_text_channel = ready_channel.id != source.id
        await rename_match_text_channel(ready_channel, context.match_id)
    if state["mode"] == GameMode.RANKED.value:
        try:
            await create_ranked_resources(guild, context)
            await move_match_members(context)
        except discord.Forbidden:
            pass
    else:
        try:
            await create_casual_voice_channels(guild, context, source)
            await move_match_members(context)
        except discord.Forbidden:
            pass
    await send_team3_announcement(
        guild,
        source,
        f"{state['mode'].capitalize()} 3v3 ready. Match #{context.match_id}\n"
        f"Team A: {team_mentions(context.team1_members)}\n"
        f"Team B: {team_mentions(context.team2_members)}\n"
        f"Draft channel: {(context.text_channel or source).mention}",
    )
    await start_team3_draft(bot, context.text_channel or source, context)
    if state.get("state_key"):
        await _delete_team3_ready_state(state["state_key"])


async def restore_team3_ready_checks(bot: commands.Bot) -> None:
    if not is_database_configured():
        return
    async with get_session_factory()() as session:
        rows = await RuntimeStateRepository(session).list_prefix(TEAM3_READY_PREFIX)
    for state_key, state in rows:
        try:
            message_id = int(state["message_id"])
            if message_id in _active_team3_ready_checks:
                continue
            guild = bot.get_guild(int(state["guild_id"]))
            if guild is None:
                continue
            members = [await fetch_member(guild, int(member_id)) for member_id in state["member_ids"]]
            if any(member is None for member in members):
                continue
            channel = await bot.fetch_channel(int(state["channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                continue
            message = await channel.fetch_message(message_id)
            workflow = {
                key: state[key]
                for key in ("guild_id", "source_channel_id", "ready_channel_id", "mode", "best_of", "team_a_ids", "team_b_ids")
                if key in state
            }
            view = ReadyCheckView([member for member in members if member is not None], state["title"], state_key, workflow)
            view.accepted_ids = {int(member_id) for member_id in state.get("accepted_ids", [])}
            view.declined_id = state.get("declined_id")
            view.timeout = max(0, float(state["deadline"] - time.time()))
            bot.add_view(view, message_id=message.id)
            view.message = message
            _active_team3_ready_checks[message_id] = view
            if view.accepted():
                state["state_key"] = state_key
                asyncio.create_task(resume_team3_ready_match(bot, state))
        except (KeyError, TypeError, ValueError, discord.DiscordException):
            logger.exception("Failed to restore 3v3 ready-check")


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


def voice_search_reminder() -> str:
    return "Stay in any server voice channel while searching, otherwise the bot will not be able to move you to a team voice channel."


async def team3_announcement_channel(
    guild: discord.Guild,
    fallback: discord.abc.Messageable,
) -> discord.abc.Messageable:
    channel = guild.get_channel(TEAM3_ANNOUNCEMENTS_CHANNEL_ID)
    if channel is None:
        try:
            channel = await guild.fetch_channel(TEAM3_ANNOUNCEMENTS_CHANNEL_ID)
        except (discord.Forbidden, discord.NotFound):
            return fallback
    if isinstance(channel, discord.abc.Messageable):
        return channel
    return fallback


async def send_team3_announcement(
    guild: discord.Guild,
    fallback: discord.abc.Messageable,
    content: str,
) -> None:
    channel = await team3_announcement_channel(guild, fallback)
    await channel.send(content)


async def edit_interaction_message(
    interaction: discord.Interaction,
    content: str,
    view: discord.ui.View | None,
) -> None:
    if interaction.response.is_done():
        await interaction.edit_original_response(content=content, view=view)
        return
    await interaction.response.edit_message(content=content, view=view)


class LoggedView(discord.ui.View):
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        logger.error(
            "Unhandled 3v3 UI interaction error: view=%s item=%s user_id=%s channel_id=%s",
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
            logger.exception("Failed to notify user about 3v3 UI interaction error")


def render_team3_panel(channel_id: int) -> str:
    return (
        "# Northgard 3v3\n"
        "Choose a mode with the buttons below.\n\n"
        f"**Casual 3v3:** {casual_lobby_count(channel_id)}/6 in lobby\n"
        f"**Ranked 3v3:** {ranked_queue_count()} in queue\n"
        f"**Ranked wide:** {ranked_wide_count()} in wide queue\n\n"
        "**Casual 3v3** - solo lobby for 6 players, teams are randomized.\n"
        "**Ranked 3v3** - rating-limited matchmaking.\n"
        "**Ranked wide** - matchmaking with a wider rating spread."
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
    schedule_team3_state_save()


def load_team3_panel_messages() -> None:
    """Kept as a compatibility hook; state is restored asynchronously from the DB."""


def save_team3_panel_messages() -> None:
    schedule_team3_state_save()


async def fetch_saved_team3_panel(channel: discord.abc.Messageable, channel_id: int) -> discord.Message | None:
    message_id = team3_panel_messages.get(channel_id)
    if message_id is None:
        return None
    try:
        return await channel.fetch_message(message_id)  # type: ignore[attr-defined]
    except (AttributeError, discord.Forbidden, discord.NotFound):
        team3_panel_messages.pop(channel_id, None)
        schedule_team3_state_save()
        return None


def missing_voice_members(members: list[discord.Member]) -> list[discord.Member]:
    return [member for member in members if member.voice is None or member.voice.channel is None]


def user_display(user: User) -> str:
    return user.nickname or str(user.discord_id)


def is_in_casual_lobby(channel_id: int, discord_id: int) -> bool:
    return any(discord_id in group for group in casual_lobbies.get(channel_id, []))


def clear_lobby(channel_id: int) -> None:
    casual_lobbies.pop(channel_id, None)
    schedule_team3_state_save()


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
    schedule_team3_state_save()


def remove_from_all_searches(discord_ids: set[int]) -> None:
    for discord_id in discord_ids:
        ranked_queue.pop(discord_id, None)
    for channel_id in list(casual_lobbies):
        remove_users_from_lobby(channel_id, discord_ids)
    schedule_team3_state_save()


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


async def fetch_voice_channel(guild: discord.Guild, channel_id: int) -> discord.VoiceChannel | None:
    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound):
            return None
    return channel if isinstance(channel, discord.VoiceChannel) else None


async def move_members_to_after_match_voice(context: Team3MatchContext) -> None:
    guild = None
    for member in [*context.team1_members, *context.team2_members]:
        guild = member.guild
        break
    if guild is None:
        return

    target_channel = await fetch_voice_channel(guild, TEAM3_AFTER_MATCH_VOICE_CHANNEL_ID)
    if target_channel is None:
        return

    for member in [*context.team1_members, *context.team2_members]:
        if member.voice is None or member.voice.channel is None:
            continue
        try:
            await member.move_to(target_channel, reason="3v3 match finished")
        except (discord.Forbidden, discord.HTTPException):
            pass


def channel_has_match_members(channel: discord.abc.GuildChannel | None) -> bool:
    if not isinstance(channel, discord.VoiceChannel):
        return False
    return bool(channel.members)


async def cleanup_match_resources(context: Team3MatchContext) -> None:
    await move_members_to_after_match_voice(context)

    channels: list[discord.abc.GuildChannel | None] = [context.team1_channel, context.team2_channel]
    if context.managed_text_channel:
        channels.append(context.text_channel)
    for channel in channels:
        if channel is not None:
            if channel_has_match_members(channel):
                continue
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


class Team3DraftView(LoggedView):
    def __init__(self, context: Team3MatchContext, channel: discord.abc.Messageable, on_finish):
        super().__init__(timeout=None)
        self.context = context
        self.channel = channel
        self.on_finish = on_finish
        self.step_index = 0
        self.bans: list[str] = []
        self.picks: dict[str, list[str]] = {"A": [], "B": []}
        self.draft_results: list[str] = []
        self.message: discord.Message | None = None
        self.step_deadline = time.monotonic() + DRAFT_STEP_SECONDS
        self.timer_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.finished = False
        self.refresh_items()

    async def persist(self) -> None:
        if not is_database_configured() or self.message is None:
            return
        state = _draft_context_state(self.context)
        state.update(
            {
                "channel_id": self.channel.id,
                "message_id": self.message.id,
                "step_index": self.step_index,
                "bans": self.bans,
                "picks": self.picks,
                "draft_results": self.draft_results,
                "deadline": time.time() + self.remaining_seconds(),
            }
        )
        async with get_session_factory()() as session:
            await RuntimeStateRepository(session).put(f"{TEAM3_DRAFT_PREFIX}{self.context.match_id}", state)
            await session.commit()

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

    def format_clans(self, clans: list[str]) -> str:
        return " · ".join(f"`{clan}`" for clan in clans) if clans else "-"

    def render_draft_table(self) -> str:
        lines = []
        for index, draft_step in enumerate(TEAM3_DRAFT_STEPS):
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

    def render(self) -> str:
        step = self.current_step()
        phase = "Draft finished" if step is None else f"Team {step.side}: **{step.action_type.value} {step.pick_type.value}**"
        available_clear = self.format_clans(self.clan_pool(PickType.CLEAR))
        available_eco = self.format_clans(self.clan_pool(PickType.ECO))
        return (
            f"# ⚔️ 3v3 Draft · Match #{self.context.match_id} · Game {self.context.game_number}\n\n"
            f"## 👥 Teams\n"
            f"**Series score:** {series_score(self.context)}\n"
            f"**Team A:** {member_names(self.context.team1_members)}\n"
            f"**Team B:** {member_names(self.context.team2_members)}\n"
            f"**Draft side A:** {member_names(self.side_members('A'))}\n"
            f"**Draft side B:** {member_names(self.side_members('B'))}\n\n"
            f"## ⏳ Current Action\n"
            f"{phase}\n"
            f"Time left: **{self.remaining_text() if step is not None else '-'}**\n\n"
            f"## 📋 Draft Table\n"
            f"```text\n{self.render_draft_table()}\n```\n"
            f"## 🚫 Bans\n"
            f"{self.format_clans(self.bans)}\n\n"
            f"## ✅ Available Picks\n"
            f"**Available clear:** {available_clear}\n"
            f"**Available eco:** {available_eco}\n\n"
            f"⬇️ Choose a clan from the menu below."
        )
    async def record_current_step(self, step: Team3DraftStep, clan: str) -> None:
        if step.action_type == DraftActionType.BAN:
            self.bans.append(clan)
        else:
            self.picks[step.side].append(clan)
        self.draft_results.append(clan)

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
            await _delete_team3_draft_state(self.context.match_id)
            _active_team3_drafts.pop(self.context.match_id, None)
            if interaction is not None:
                await edit_interaction_message(interaction, self.render(), None)
            elif self.message is not None:
                await self.message.edit(content=self.render(), view=None)
            await self.on_finish(self.channel, self.context)
            self.stop()
            return

        self.step_deadline = time.monotonic() + DRAFT_STEP_SECONDS
        await self.persist()
        self.start_timer()
        if interaction is not None:
            await edit_interaction_message(interaction, self.render(), self)
        elif self.message is not None:
            await self.message.edit(content=self.render(), view=self)

    async def handle_pick(self, interaction: discord.Interaction, clan: str) -> None:
        async with self.lock:
            step = self.current_step()
            if step is None:
                await interaction.response.send_message("The draft is already finished.", ephemeral=True)
                return
            allowed_ids = {member.id for member in self.side_members(step.side)}
            if interaction.user.id not in allowed_ids:
                await interaction.response.send_message(f"Team {step.side} is choosing now.", ephemeral=True)
                return
            if clan not in self.available_options(step):
                await interaction.response.send_message("This clan is not available right now.", ephemeral=True)
                return

            await interaction.response.defer()
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
                await cancel_team3_match(self.context, "No clans are available in the draft. The match has been cancelled.")
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
        super().__init__(
            placeholder=f"Team {step.side}: {step.action_type.value} {step.pick_type.value}",
            custom_id=f"team3_draft:{step.side}:{step.action_type.value}:{step.pick_type.value}",
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, Team3DraftView):
            return
        await view.handle_pick(interaction, self.values[0])


class ResultConfirmView(LoggedView):
    def __init__(self, bot: commands.Bot, context: Team3MatchContext):
        super().__init__(timeout=result_timeout_seconds(context.best_of))
        self.bot = bot
        self.context = context
        self.votes: dict[int, int] = {}
        self.finished = False
        self.message: discord.Message | None = None
        self.deadline = time.time() + self.timeout

    async def persist(self) -> None:
        if not is_database_configured() or self.message is None:
            return
        state = _draft_context_state(self.context)
        state.update(
            {
                "channel_id": self.message.channel.id,
                "message_id": self.message.id,
                "votes": {str(user_id): team_id for user_id, team_id in self.votes.items()},
                "deadline": self.deadline,
            }
        )
        async with get_session_factory()() as session:
            await RuntimeStateRepository(session).put(f"{TEAM3_RESULT_PREFIX}{self.context.match_id}", state)
            await session.commit()

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
            await interaction.response.send_message("The result has already been confirmed.", ephemeral=True)
            return
        if self.team_for_user(interaction.user.id) is None:
            await interaction.response.send_message("You are not a participant in this match.", ephemeral=True)
            return
        self.votes[interaction.user.id] = winner_team_id
        await self.persist()
        accepted = self.accepted_winner()
        if accepted is None:
            if self.has_conflict():
                self.finished = True
                await interaction.response.send_message(
                    "The teams did not agree on the winner. The match has been sent to admins for a decision.",
                    ephemeral=True,
                )
                await send_result_dispute(self.bot, self.context, self.votes)
                await _delete_team3_result_state(self.context.match_id)
                _active_team3_results.pop(self.context.match_id, None)
                self.stop()
                return
            await interaction.response.send_message("Vote accepted. Waiting for confirmation from 2 players on each team.", ephemeral=True)
            return

        self.finished = True
        await interaction.response.defer(ephemeral=True)
        await finish_team3_match(self.bot, interaction, self.context, accepted)
        await _delete_team3_result_state(self.context.match_id)
        _active_team3_results.pop(self.context.match_id, None)
        self.stop()

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.finished = True
        await _delete_team3_result_state(self.context.match_id)
        _active_team3_results.pop(self.context.match_id, None)
        await cancel_team3_match(self.context, "Result confirmation timed out. The match has been cancelled.")

    @discord.ui.button(label="Team A won", style=discord.ButtonStyle.success, custom_id="team3_result:team_a")
    async def team_a_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.vote(interaction, self.context.team1_id)

    @discord.ui.button(label="Team B won", style=discord.ButtonStyle.success, custom_id="team3_result:team_b")
    async def team_b_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.vote(interaction, self.context.team2_id)


class ReadyCheckView(LoggedView):
    def __init__(self, members: list[discord.Member], title: str, state_key: str | None = None, workflow: dict | None = None):
        super().__init__(timeout=60)
        self.members = members
        self.title = title
        self.state_key = state_key
        self.workflow = workflow or {}
        self.accepted_ids: set[int] = set()
        self.declined_id: int | None = None
        self.message: discord.Message | None = None
        self.deadline = time.time() + 60

    async def persist(self) -> None:
        if not is_database_configured() or self.message is None or self.state_key is None:
            return
        state = {
            "guild_id": self.members[0].guild.id,
            "channel_id": self.message.channel.id,
            "message_id": self.message.id,
            "member_ids": [member.id for member in self.members],
            "title": self.title,
            "accepted_ids": sorted(self.accepted_ids),
            "declined_id": self.declined_id,
            "deadline": self.deadline,
            **self.workflow,
        }
        async with get_session_factory()() as session:
            await RuntimeStateRepository(session).put(self.state_key, state)
            await session.commit()

    async def update_message(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=self.render(), view=self)

    def render(self) -> str:
        accepted = len(self.accepted_ids)
        mentions = " ".join(member.mention for member in self.members)
        return (
            f"**{self.title} found.**\n"
            f"{mentions}\n\n"
            f"Players ready: **{accepted}/6**\n"
            f"Confirmation time: 60 seconds."
        )

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="team3_ready:accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        allowed_ids = {member.id for member in self.members}
        if interaction.user.id not in allowed_ids:
            await interaction.response.send_message("You are not a participant in this ready-check.", ephemeral=True)
            return
        self.accepted_ids.add(interaction.user.id)
        await self.persist()
        if len(self.accepted_ids) == len(self.members):
            for item in self.children:
                item.disabled = True
            await self.update_message(interaction)
            if self.state_key is not None:
                await _delete_team3_ready_state(self.state_key)
            _active_team3_ready_checks.pop(self.message.id if self.message else 0, None)
            self.stop()
            return
        await self.update_message(interaction)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, custom_id="team3_ready:decline")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        allowed_ids = {member.id for member in self.members}
        if interaction.user.id not in allowed_ids:
            await interaction.response.send_message("You are not a participant in this ready-check.", ephemeral=True)
            return
        self.declined_id = interaction.user.id
        await self.persist()
        for item in self.children:
            item.disabled = True
        await self.update_message(interaction)
        if self.state_key is not None:
            await _delete_team3_ready_state(self.state_key)
        _active_team3_ready_checks.pop(self.message.id if self.message else 0, None)
        self.stop()

    def accepted(self) -> bool:
        return self.declined_id is None and len(self.accepted_ids) == len(self.members)

    async def on_timeout(self) -> None:
        if self.state_key is not None:
            await _delete_team3_ready_state(self.state_key)
        _active_team3_ready_checks.pop(self.message.id if self.message else 0, None)


async def run_ready_check(
    channel: discord.abc.Messageable,
    members: list[discord.Member],
    title: str,
    workflow: dict | None = None,
) -> ReadyCheckView:
    state_key = f"{TEAM3_READY_PREFIX}{channel.id}:{int(time.time() * 1000)}"
    view = ReadyCheckView(members, title, state_key, workflow)
    message = await channel.send(view.render(), view=view)
    view.message = message
    _active_team3_ready_checks[message.id] = view
    await view.persist()
    await view.wait()
    if view.state_key is not None:
        await _delete_team3_ready_state(view.state_key)
    _active_team3_ready_checks.pop(message.id, None)
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
            f"Game {finished_game} finished.\n"
            f"Winner: **{winner_label}**.\n"
            f"Series score: **{series_score(context)}**.\n"
            f"Starting Game {context.game_number}."
        )
        if next_channel is not None:
            await next_channel.send(message_text)
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

    rating_text = f"\nRating: +{delta}/-{delta}" if context.game_mode == GameMode.RANKED else ""
    message_text = (
        f"Match #{context.match_id} finished.\n"
        f"Final score: **{series_score(context)}**.\n"
        f"Winner: **{winner_label}**.{rating_text}\n"
        "Temporary channels and roles will be deleted in 10 seconds."
    )
    result_channel = context.text_channel or interaction.channel
    if result_channel is not None:
        await result_channel.send(message_text)
    active_team3_players.difference_update(match_discord_ids(context))
    await asyncio.sleep(10)
    await cleanup_match_resources(context)


async def cancel_team3_match(context: Team3MatchContext, reason: str) -> None:
    active_team3_players.difference_update(match_discord_ids(context))
    if context.text_channel is not None:
        try:
            await context.text_channel.send(f"{reason}\nTemporary channels and roles will be deleted in 10 seconds.")
        except (discord.Forbidden, discord.NotFound):
            pass
    await asyncio.sleep(10)
    await cleanup_match_resources(context)


class DisputeResolveView(LoggedView):
    def __init__(self, bot: commands.Bot, context: Team3MatchContext):
        super().__init__(timeout=None)
        self.bot = bot
        self.context = context
        self.resolved = False
        self.message: discord.Message | None = None
        self.votes: dict[int, int] = {}

    async def persist(self) -> None:
        if not is_database_configured() or self.message is None:
            return
        state = _draft_context_state(self.context)
        state.update(
            {
                "channel_id": self.message.channel.id,
                "dispute_channel_id": self.message.channel.id,
                "message_id": self.message.id,
                "votes": {str(user_id): team_id for user_id, team_id in self.votes.items()},
            }
        )
        async with get_session_factory()() as session:
            await RuntimeStateRepository(session).put(f"{TEAM3_DISPUTE_PREFIX}{self.context.match_id}", state)
            await session.commit()

    async def resolve(self, interaction: discord.Interaction, winner_team_id: int) -> None:
        if self.resolved:
            await interaction.response.send_message("This dispute has already been resolved.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("Only a bot admin can resolve this dispute.", ephemeral=True)
            return
        self.resolved = True
        await interaction.response.defer(ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        await finish_team3_match(self.bot, interaction, self.context, winner_team_id)
        await _delete_team3_dispute_state(self.context.match_id)
        _active_team3_disputes.pop(self.context.match_id, None)

    @discord.ui.button(label="Team A won", style=discord.ButtonStyle.success, custom_id="team3_dispute:team_a")
    async def team_a_won(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.resolve(interaction, self.context.team1_id)

    @discord.ui.button(label="Team B won", style=discord.ButtonStyle.success, custom_id="team3_dispute:team_b")
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
            await context.text_channel.send("Could not send the dispute to admins. Please contact an admin manually.")
        return

    def vote_label(member: discord.Member) -> str:
        vote = votes.get(member.id)
        if vote == context.team1_id:
            return f"{member.mention}: Team A"
        if vote == context.team2_id:
            return f"{member.mention}: Team B"
        return f"{member.mention}: no vote"

    view = DisputeResolveView(bot, context)
    view.votes = dict(votes)
    message = await channel.send(
        "@here\n"
        f"Disputed result for 3v3 match #{context.match_id}.\n"
        f"Game {context.game_number}. Series score: {series_score(context)}.\n"
        f"Team A: {team_mentions(context.team1_members)}\n"
        f"Team B: {team_mentions(context.team2_members)}\n\n"
        "**Team A votes:**\n"
        + "\n".join(vote_label(member) for member in context.team1_members)
        + "\n\n**Team B votes:**\n"
        + "\n".join(vote_label(member) for member in context.team2_members)
        + "\n\nChoose the winner:",
        view=view,
    )
    view.message = message
    _active_team3_disputes[context.match_id] = view
    await view.persist()


async def start_result_confirmation(bot: commands.Bot, channel: discord.abc.Messageable, context: Team3MatchContext) -> None:
    view = ResultConfirmView(bot, context)
    message = await channel.send(
        f"Match #{context.match_id}, Game {context.game_number} has started.\n"
        f"Series score: **{series_score(context)}**.\n"
        f"After the game, confirm the winner. At least 2 votes from each team are required.\n"
        f"Time limit: {'2 hours' if context.best_of == BestOf.BO1 else '24 hours'}.",
        view=view,
    )
    view.message = message
    _active_team3_results[context.match_id] = view
    await view.persist()


async def start_team3_draft(bot: commands.Bot, channel: discord.abc.Messageable, context: Team3MatchContext) -> None:
    async def on_finish(result_channel: discord.abc.Messageable, match_context: Team3MatchContext) -> None:
        await start_result_confirmation(bot, result_channel, match_context)

    view = Team3DraftView(context, channel, on_finish)
    message = await channel.send(view.render(), view=view)
    view.message = message
    _active_team3_drafts[context.match_id] = view
    await view.persist()
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


class Team3PanelView(LoggedView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Casual 3v3", style=discord.ButtonStyle.primary, custom_id="team3_panel:casual")
    async def casual(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await join_casual_queue(interaction)

    @discord.ui.button(label="Ranked 3v3", style=discord.ButtonStyle.success, custom_id="team3_panel:ranked")
    async def ranked(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await join_ranked_queue(interaction, wide=False)

    @discord.ui.button(label="Ranked wide", style=discord.ButtonStyle.danger, custom_id="team3_panel:ranked_wide")
    async def ranked_wide(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await join_ranked_queue(interaction, wide=True)

    @discord.ui.button(label="Leave queue", style=discord.ButtonStyle.secondary, custom_id="team3_panel:leave")
    async def leave_queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        ranked_queue.pop(interaction.user.id, None)
        if interaction.channel_id is not None and is_in_casual_lobby(interaction.channel_id, interaction.user.id):
            casual_lobbies[interaction.channel_id] = [
                group for group in casual_lobbies.get(interaction.channel_id, []) if interaction.user.id not in group
            ]
        if interaction.channel is not None and interaction.channel_id is not None:
            await update_team3_panel(interaction.channel, interaction.channel_id)
        await interaction.response.send_message("You left the queue/lobby.", ephemeral=True)


async def join_ranked_queue(interaction: discord.Interaction, wide: bool) -> None:
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message("Search is only available on a server.", ephemeral=True)
        return
    if not is_database_configured():
        await interaction.response.send_message("Database is not configured.", ephemeral=True)
        return
    if interaction.user.id in active_team3_players:
        await interaction.response.send_message("You are already in an active 3v3 match.", ephemeral=True)
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
    await interaction.response.send_message(
        f"OK. You joined the Ranked 3v3 queue.\n"
        f"Mode: **{mode}**.\n"
        f"Players in ranked queue: **{ranked_queue_count()}**.\n"
        f"Players in wide queue: **{ranked_wide_count()}**.\n\n"
        f"{voice_search_reminder()}",
        ephemeral=True,
    )
    await update_team3_panel(interaction.channel, interaction.channel.id)
    asyncio.create_task(maybe_start_ranked_match(interaction))


async def join_casual_queue(interaction: discord.Interaction) -> None:
    if interaction.guild is None or interaction.channel_id is None or interaction.channel is None:
        await interaction.response.send_message("Search is only available on a server.", ephemeral=True)
        return
    if not is_database_configured():
        await interaction.response.send_message("Database is not configured.", ephemeral=True)
        return
    if interaction.user.id in active_team3_players:
        await interaction.response.send_message("You are already in an active 3v3 match.", ephemeral=True)
        return
    if is_in_casual_lobby(interaction.channel_id, interaction.user.id):
        await interaction.response.send_message("You are already in the casual lobby.", ephemeral=True)
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
        await interaction.response.send_message("Casual lobby is already full.", ephemeral=True)
        return
    lobby.append((interaction.user.id,))
    await interaction.response.send_message(
        f"OK. You joined the Casual 3v3 lobby.\n"
        f"Players in lobby: **{casual_lobby_count(interaction.channel_id)}/6**.\n\n"
        f"{voice_search_reminder()}",
        ephemeral=True,
    )
    await update_team3_panel(interaction.channel, interaction.channel_id)
    asyncio.create_task(maybe_start_casual_match(interaction))


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
        await send_team3_announcement(interaction.guild, interaction.channel, "Could not find all players on the server. Match cancelled.")
        return
    members = [member for member in [*team1_members, *team2_members] if member is not None]
    missing_voice = missing_voice_members(members)
    if missing_voice:
        for member in missing_voice:
            ranked_queue.pop(member.id, None)
        await update_team3_panel(interaction.channel, interaction.channel.id)
        await send_team3_announcement(
            interaction.guild,
            interaction.channel,
            "Match found, but not all players are in a voice channel. "
            f"Removed from queue: {team_mentions(missing_voice)}",
        )
        return

    try:
        ready_channel = await create_ready_text_channel(interaction.guild, members, interaction.channel, "ranked-3v3")
        await send_team3_announcement(interaction.guild, interaction.channel, f"Ranked 3v3 found. Ready-check: {ready_channel.mention}")
    except discord.Forbidden:
        ready_channel = interaction.channel
        await send_team3_announcement(interaction.guild, interaction.channel, "The bot cannot create a ready-check channel. Ready-check will happen here.")

    pending_ready_checks.add(interaction.channel.id)
    ready_check = await run_ready_check(
        ready_channel,
        members,
        "Ranked 3v3",
        {
            "guild_id": interaction.guild.id,
            "source_channel_id": interaction.channel.id,
            "ready_channel_id": ready_channel.id,
            "mode": GameMode.RANKED.value,
            "best_of": BestOf.BO1.value,
            "team_a_ids": [member.id for member in team1_members if member is not None],
            "team_b_ids": [member.id for member in team2_members if member is not None],
        },
    )
    pending_ready_checks.discard(interaction.channel.id)
    if not ready_check.accepted():
        ready_ids = {member.id for member in members}
        declined_or_missing = ready_ids - ready_check.accepted_ids
        await send_team3_announcement(
            interaction.guild,
            interaction.channel,
            "Ready-check was not accepted by all players. Match cancelled; players who accepted remain in queue."
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
        await send_team3_announcement(
            interaction.guild,
            interaction.channel,
            "Match created, but the bot cannot create roles/voice channels or move players."
        )
    draft_channel = context.text_channel or interaction.channel

    await send_team3_announcement(
        interaction.guild,
        interaction.channel,
        f"Ranked 3v3 found. Match #{context.match_id}\n"
        f"Team A: {team_mentions(context.team1_members)}\n"
        f"Team B: {team_mentions(context.team2_members)}\n"
        f"Team rating difference: {split.rating_diff}\n"
        f"Draft channel: {draft_channel.mention if isinstance(draft_channel, discord.TextChannel) else 'this channel'}",
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
        await send_team3_announcement(
            interaction.guild,
            interaction.channel,
            "Casual lobby is full, but two teams cannot be created without blacklist conflicts. "
            "Someone needs to leave the lobby or update their blacklist.",
        )
        return
    team1_members = [await fetch_member(interaction.guild, user.discord_id) for user in team1_users]
    team2_members = [await fetch_member(interaction.guild, user.discord_id) for user in team2_users]
    if any(member is None for member in [*team1_members, *team2_members]):
        await send_team3_announcement(interaction.guild, interaction.channel, "Could not find all players on the server. Lobby cleared.")
        clear_lobby(interaction.channel_id)
        return
    members = [member for member in [*team1_members, *team2_members] if member is not None]
    missing_voice = missing_voice_members(members)
    if missing_voice:
        remove_users_from_lobby(interaction.channel_id, {member.id for member in missing_voice})
        remaining_count = sum(len(group) for group in casual_lobbies.get(interaction.channel_id, []))
        await update_team3_panel(interaction.channel, interaction.channel_id)
        await send_team3_announcement(
            interaction.guild,
            interaction.channel,
            "Match found, but not all players are in a voice channel. "
            f"Removed from lobby: {team_mentions(missing_voice)}. Remaining: {remaining_count}/6.",
        )
        return

    try:
        ready_channel = await create_ready_text_channel(interaction.guild, members, interaction.channel, "casual-3v3")
        await send_team3_announcement(interaction.guild, interaction.channel, f"Casual 3v3 found. Ready-check: {ready_channel.mention}")
    except discord.Forbidden:
        ready_channel = interaction.channel
        await send_team3_announcement(interaction.guild, interaction.channel, "The bot cannot create a ready-check channel. Ready-check will happen here.")

    pending_ready_checks.add(interaction.channel_id)
    ready_check = await run_ready_check(
        ready_channel,
        members,
        "Casual 3v3",
        {
            "guild_id": interaction.guild.id,
            "source_channel_id": interaction.channel.id,
            "ready_channel_id": ready_channel.id,
            "mode": GameMode.CASUAL.value,
            "best_of": BestOf.BO1.value,
            "team_a_ids": [member.id for member in team1_members if member is not None],
            "team_b_ids": [member.id for member in team2_members if member is not None],
        },
    )
    pending_ready_checks.discard(interaction.channel_id)
    if not ready_check.accepted():
        ready_ids = {member.id for member in members}
        declined_or_missing = ready_ids - ready_check.accepted_ids
        remove_users_from_lobby(interaction.channel_id, declined_or_missing)
        remaining_count = sum(len(group) for group in casual_lobbies.get(interaction.channel_id, []))
        await update_team3_panel(interaction.channel, interaction.channel_id)
        await send_team3_announcement(
            interaction.guild,
            interaction.channel,
            "Ready-check was not accepted by all players. "
            f"Players who accepted remain in lobby: {remaining_count}/6.",
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
        await send_team3_announcement(
            interaction.guild,
            interaction.channel,
            "Match created, but the bot cannot create voice channels or move players."
        )
    draft_channel = context.text_channel or interaction.channel

    await send_team3_announcement(
        interaction.guild,
        interaction.channel,
        f"Casual 3v3 ready. Match #{context.match_id}\n"
        f"Team A: {team_mentions(context.team1_members)}\n"
        f"Team B: {team_mentions(context.team2_members)}\n"
        f"Draft channel: {draft_channel.mention if isinstance(draft_channel, discord.TextChannel) else 'this channel'}",
    )
    await start_team3_draft(interaction.client, draft_channel, context)


def register(bot: commands.Bot, settings: Settings) -> None:
    global _team3_bot
    _team3_bot = bot
    load_team3_panel_messages()
    bot.add_view(Team3PanelView())

    async def restore_persistent_team3_state() -> None:
        await restore_team3_state()
        await restore_team3_drafts(bot)
        await restore_team3_results(bot)
        await restore_team3_disputes(bot)
        await restore_team3_ready_checks(bot)

    bot.add_listener(restore_persistent_team3_state, "on_ready")

    @bot.tree.command(name="team3_panel", description="Create the 3v3 matchmaking panel")
    @app_commands.default_permissions(manage_guild=True)
    async def team3_panel(interaction: discord.Interaction) -> None:
        if interaction.channel_id is None or interaction.channel is None:
            await interaction.response.send_message("Could not determine the channel.", ephemeral=True)
            return
        existing_message = await fetch_saved_team3_panel(interaction.channel, interaction.channel_id)
        if existing_message is not None:
            await existing_message.edit(content=render_team3_panel(interaction.channel_id), view=Team3PanelView())
            await interaction.response.send_message(
                f"3v3 panel is already active and has been refreshed: {existing_message.jump_url}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(render_team3_panel(interaction.channel_id), view=Team3PanelView())
        message = await interaction.original_response()
        team3_panel_messages[interaction.channel_id] = message.id
        save_team3_panel_messages()

    @bot.tree.command(name="tournament_3v3_start", description="Start a tournament 3v3 draft")
    @app_commands.describe(
        team_a_role="First team role",
        team_b_role="Second team role",
        best_of="Series format",
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
            await interaction.response.send_message("Database is not configured.", ephemeral=True)
            return
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("This command is only available on a server.", ephemeral=True)
            return

        team_a_members = [member for member in team_a_role.members if not member.bot]
        team_b_members = [member for member in team_b_role.members if not member.bot]
        if len(team_a_members) != 3 or len(team_b_members) != 3:
            await interaction.response.send_message(
                "Each team role must contain exactly 3 registered players.",
                ephemeral=True,
            )
            return
        busy_members = [member for member in [*team_a_members, *team_b_members] if member.id in active_team3_players]
        if busy_members:
            await interaction.response.send_message(
                "Cannot start the match: these players are already in an active 3v3 match: "
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
            f"Tournament 3v3 created. Match #{context.match_id}, bo{best_of.value}.\n"
            f"Team A: {team_mentions(context.team1_members)}\n"
            f"Team B: {team_mentions(context.team2_members)}"
        )
        active_team3_players.update(member.id for member in [*context.team1_members, *context.team2_members])
        remove_from_all_searches(match_discord_ids(context))
        if isinstance(interaction.channel, discord.TextChannel):
            context.text_channel = interaction.channel
        await start_team3_draft(bot, interaction.channel, context)
