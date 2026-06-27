import asyncio
import json
import random
from pathlib import Path

import discord
from discord.ext import commands

from bot.database.session import get_session_factory, is_database_configured
from bot.repositories.clan_repository import ClanRepository


BOT_ADMINS_FILE = Path("data/bot_admins.json")
MASTER_ADMIN_IDS = {
    624650680778489856,
}
BOT_ADMIN_IDS = {
    # Add Discord user IDs here, for example:
    # 123456789012345678,
}

ALL_CLANS = [
    "Stag",
    "Goat",
    "Raven",
    "Wolf",
    "Bear",
    "Boar",
    "Snake",
    "Dragon",
    "Horse",
    "Kraken",
    "Ox",
    "Lynx",
    "Squirrel",
    "Rat",
    "Eagle",
    "Lion",
    "Stoat",
    "Owl",
    "Hound",
    "Turtle",
    "Hippo",
]
CLEAR_CLANS = {"Wolf", "Lynx", "Eagle", "Hound"}
KINGDOM_CLANS = {"Lion", "Stoat", "Hippo"}
DRAFT_FORMATS = {"bo1": 1, "bo3": 2, "bo5": 3}
TIMER_UPDATE_SECONDS = 3

active_drafts: dict[int, "DraftSession"] = {}


class ClanRules:
    def __init__(
        self,
        all_clans: list[str] | None = None,
        clear_clans: set[str] | None = None,
        kingdom_clans: set[str] | None = None,
    ):
        self.all_clans = all_clans or ALL_CLANS
        self.clear_clans = clear_clans or CLEAR_CLANS
        self.kingdom_clans = kingdom_clans or KINGDOM_CLANS


DEFAULT_CLAN_RULES = ClanRules()


async def load_clan_rules() -> ClanRules:
    if not is_database_configured():
        return DEFAULT_CLAN_RULES
    session_factory = get_session_factory()
    async with session_factory() as session:
        clans = ClanRepository(session)
        all_clans = await clans.enabled_names()
        if not all_clans:
            return DEFAULT_CLAN_RULES
        clear_clans = set(await clans.clear_names())
        kingdom_clans = set(await clans.kingdom_names())
        return ClanRules(all_clans, clear_clans, kingdom_clans)


def load_bot_admins() -> None:
    if not BOT_ADMINS_FILE.exists():
        return

    with BOT_ADMINS_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    BOT_ADMIN_IDS.update(int(user_id) for user_id in data.get("admin_ids", []))


def save_bot_admins() -> None:
    data = {"admin_ids": sorted(BOT_ADMIN_IDS)}
    BOT_ADMINS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with BOT_ADMINS_FILE.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def configure_admins_file(path: Path) -> None:
    global BOT_ADMINS_FILE
    BOT_ADMINS_FILE = path


def add_bot_admin(user_id: int) -> None:
    BOT_ADMIN_IDS.add(user_id)
    save_bot_admins()


def is_admin(member: discord.Member) -> bool:
    return member.id in BOT_ADMIN_IDS or member.id in MASTER_ADMIN_IDS


def is_master_admin(member: discord.Member) -> bool:
    return member.id in MASTER_ADMIN_IDS


def format_countdown(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def clan_options(clans: list[str]) -> list[discord.SelectOption]:
    return [discord.SelectOption(label=clan, value=clan) for clan in clans]


async def delete_message(message: discord.Message | None) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except discord.HTTPException:
        pass


class PlayerBanSelect(discord.ui.Select):
    def __init__(self, draft_view: "PlayerBanView"):
        super().__init__(
            placeholder="Выберите 1-2 клана для бана",
            min_values=1,
            max_values=2,
            options=clan_options(draft_view.clan_rules.all_clans),
        )
        self.draft_view = draft_view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.draft_view.submit(interaction, list(self.values))


class SkipBansButton(discord.ui.Button):
    def __init__(self, draft_view: "PlayerBanView"):
        super().__init__(label="Пропустить баны", style=discord.ButtonStyle.secondary)
        self.draft_view = draft_view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.draft_view.submit(interaction, [])


class PlayerBanView(discord.ui.View):
    def __init__(self, player: discord.Member, clan_rules: ClanRules):
        super().__init__(timeout=None)
        self.player = player
        self.clan_rules = clan_rules
        self.result: set[str] | None = None
        self.prompt_message: discord.Message | None = None
        self.add_item(PlayerBanSelect(self))
        self.add_item(SkipBansButton(self))

    async def submit(self, interaction: discord.Interaction, clans: list[str]) -> None:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message("Это меню банов другого игрока.", ephemeral=True)
            return
        if self.result is not None:
            await interaction.response.send_message("Твои баны уже приняты.", ephemeral=True)
            return

        self.result = set(clans)
        await interaction.response.send_message(
            f"Баны приняты: {', '.join(clans) if clans else 'нет банов'}.",
            ephemeral=True,
        )
        self.stop()
        await delete_message(self.prompt_message or interaction.message)

    def disable_all_items(self) -> None:
        for item in self.children:
            item.disabled = True


class SinglePickSelect(discord.ui.Select):
    def __init__(self, draft_view: "SinglePickView"):
        super().__init__(
            placeholder="Выберите клан",
            min_values=1,
            max_values=1,
            options=clan_options(draft_view.options),
        )
        self.draft_view = draft_view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.draft_view.submit(interaction, self.values[0])


class SinglePickView(discord.ui.View):
    def __init__(
        self,
        player: discord.Member,
        available_clans: list[str],
        current_team_picks: list[str],
        clan_rules: ClanRules,
    ):
        super().__init__(timeout=None)
        self.player = player
        self.available_clans = available_clans
        self.current_team_picks = current_team_picks
        self.clan_rules = clan_rules
        self.options = valid_single_pick_options(available_clans, current_team_picks, clan_rules)
        self.result: str | None = None
        self.prompt_message: discord.Message | None = None
        self.add_item(SinglePickSelect(self))

    async def submit(self, interaction: discord.Interaction, clan: str) -> None:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message("Сейчас выбирает другой игрок.", ephemeral=True)
            return

        error = validate_team_pair([*self.current_team_picks, clan], self.clan_rules)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        self.result = clan
        await interaction.response.send_message(f"Пик принят: {clan}.", ephemeral=True)
        self.stop()
        await delete_message(self.prompt_message or interaction.message)

    def disable_all_items(self) -> None:
        for item in self.children:
            item.disabled = True


class DraftSession:
    def __init__(
        self,
        bot_: commands.Bot,
        channel: discord.abc.Messageable,
        player_1: discord.Member,
        player_2: discord.Member,
        bo: str,
    ):
        self.bot = bot_
        self.channel = channel
        self.players = [player_1, player_2]
        self.bo = bo
        self.wins_to_take = DRAFT_FORMATS[bo]
        self.score = {player_1.id: 0, player_2.id: 0}
        self.game_number = 1
        self.waiting_for_winner = False
        self.is_active = True
        self.current_task: asyncio.Task[None] | None = None
        self.status_message: discord.Message | None = None
        self.last_draft_players: list[discord.Member] | None = None
        self.last_banned_clans: set[str] | None = None
        self.last_available_clans: list[str] | None = None
        self.last_picks_by_player: dict[int, list[str]] | None = None

    def start_next_game(self) -> None:
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
        self.current_task = self.bot.loop.create_task(self.run_next_game())

    async def stop(self) -> None:
        self.is_active = False
        self.waiting_for_winner = False
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
        active_drafts.pop(self.channel.id, None)
        await self.update_status("Остановлен", "Драфт 2v2 остановлен админом.")

    async def restart_current_game_draft(self) -> None:
        if not self.is_active:
            await self.channel.send("Этот драфт уже остановлен.")
            return

        self.waiting_for_winner = False
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()

        await self.update_status(
            "Перезапуск",
            f"Драфт Game {self.game_number} перезапущен. Счет сохраняется: **{self.score_text()}**",
        )
        self.status_message = None
        self.current_task = self.bot.loop.create_task(self.run_next_game())

    def score_text(self) -> str:
        return (
            f"{self.players[0].display_name} {self.score[self.players[0].id]}:"
            f"{self.score[self.players[1].id]} {self.players[1].display_name}"
        )

    def draft_order_for_current_game(self) -> list[discord.Member]:
        if self.game_number % 2 == 1:
            return self.players
        return [self.players[1], self.players[0]]

    async def run_next_game(self) -> None:
        if not self.is_active:
            return

        self.status_message = None
        self.waiting_for_winner = False
        draft_players = self.draft_order_for_current_game()
        self.last_draft_players = draft_players
        self.last_banned_clans = None
        self.last_available_clans = None
        self.last_picks_by_player = None
        clan_rules = await load_clan_rules()
        try:
            await self.update_status(
                "Баны",
                (
                    f"{self.players[0].mention} и {self.players[1].mention}, выберите до 2 банов.\n"
                    f"Первым в этой игре пикает {draft_players[0].mention}."
                ),
                draft_players=draft_players,
            )

            bans_by_player = await self.collect_bans(draft_players, clan_rules)
            banned_clans = set().union(*bans_by_player.values())
            available_clans = [clan for clan in clan_rules.all_clans if clan not in banned_clans]
            self.last_banned_clans = banned_clans
            self.last_available_clans = available_clans

            picks_1: list[str] = []
            picks_2: list[str] = []
            picks_by_player = {draft_players[0].id: picks_1, draft_players[1].id: picks_2}
            self.last_picks_by_player = picks_by_player

            await self.update_status(
                "Пик-фаза",
                "Баны завершены. Начинается пик-фаза.",
                draft_players=draft_players,
                banned_clans=banned_clans,
                available_clans=available_clans,
                picks_by_player=picks_by_player,
            )

            picks_1.append(
                await self.collect_single_pick(
                    draft_players[0],
                    available_clans,
                    picks_1,
                    draft_players,
                    banned_clans,
                    picks_by_player,
                    clan_rules,
                    60,
                    "первый клан",
                )
            )

            player_2_deadline = discord.utils.utcnow().timestamp() + 120
            picks_2.append(
                await self.collect_single_pick_until(
                    draft_players[1],
                    available_clans,
                    picks_2,
                    draft_players,
                    banned_clans,
                    picks_by_player,
                    clan_rules,
                    player_2_deadline,
                    "первый клан",
                )
            )
            picks_2.append(
                await self.collect_single_pick_until(
                    draft_players[1],
                    available_clans,
                    picks_2,
                    draft_players,
                    banned_clans,
                    picks_by_player,
                    clan_rules,
                    player_2_deadline,
                    "второй клан",
                )
            )

            picks_1.append(
                await self.collect_single_pick(
                    draft_players[0],
                    available_clans,
                    picks_1,
                    draft_players,
                    banned_clans,
                    picks_by_player,
                    clan_rules,
                    60,
                    "второй клан",
                )
            )

            await self.update_status(
                "Ожидание результата",
                "Админ должен написать `win @player`, чтобы записать победителя.",
                draft_players=draft_players,
                banned_clans=banned_clans,
                available_clans=available_clans,
                picks_by_player=picks_by_player,
            )
            self.waiting_for_winner = True
        except asyncio.CancelledError:
            return

    async def update_status(
        self,
        phase: str,
        details: str,
        *,
        remaining: float | None = None,
        draft_players: list[discord.Member] | None = None,
        banned_clans: set[str] | None = None,
        available_clans: list[str] | None = None,
        picks_by_player: dict[int, list[str]] | None = None,
        ban_status_by_player: dict[int, str] | None = None,
    ) -> None:
        content = self.build_status_text(
            phase,
            details,
            remaining=remaining,
            draft_players=draft_players,
            banned_clans=banned_clans,
            available_clans=available_clans,
            picks_by_player=picks_by_player,
            ban_status_by_player=ban_status_by_player,
        )

        if self.status_message is None:
            self.status_message = await self.channel.send(content)
        else:
            await self.status_message.edit(content=content, view=None)

    def build_status_text(
        self,
        phase: str,
        details: str,
        *,
        remaining: float | None = None,
        draft_players: list[discord.Member] | None = None,
        banned_clans: set[str] | None = None,
        available_clans: list[str] | None = None,
        picks_by_player: dict[int, list[str]] | None = None,
        ban_status_by_player: dict[int, str] | None = None,
    ) -> str:
        lines = [
            f"## Game {self.game_number} draft",
            f"**Фаза:** {phase}",
            f"**Счет:** {self.score_text()}",
        ]
        if remaining is not None:
            lines.append(f"**Таймер:** `{format_countdown(remaining)}`")
        if draft_players is not None:
            lines.append(f"**Порядок пиков:** {draft_players[0].mention} -> {draft_players[1].mention} -> {draft_players[0].mention}")
        lines.extend(["", details])

        if ban_status_by_player:
            lines.append("")
            lines.append("**Баны:**")
            for player in self.players:
                lines.append(f"{player.mention}: {ban_status_by_player.get(player.id, 'ожидание')}")

        if banned_clans is not None:
            bans_text = ", ".join(sorted(banned_clans)) if banned_clans else "нет"
            lines.append("")
            lines.append(f"**Забанены:** {bans_text}")

        if available_clans is not None:
            lines.append(f"**Доступны:** {', '.join(available_clans)}")

        if picks_by_player and draft_players is not None:
            lines.append("")
            lines.append("**Пики:**")
            for player in draft_players:
                picks = picks_by_player.get(player.id, [])
                picks_text = ", ".join(picks) if picks else "-"
                lines.append(f"{player.mention}: {picks_text}")

        return "\n".join(lines)

    async def collect_bans(self, draft_players: list[discord.Member], clan_rules: ClanRules) -> dict[int, set[str]]:
        views = {player.id: PlayerBanView(player, clan_rules) for player in self.players}
        prompt_messages: list[discord.Message] = []
        end_time = discord.utils.utcnow().timestamp() + 60

        for player in self.players:
            message = await self.channel.send(f"{player.mention}, выбери баны или пропусти бан-фазу.", view=views[player.id])
            views[player.id].prompt_message = message
            prompt_messages.append(message)

        try:
            while any(view.result is None for view in views.values()):
                remaining = end_time - discord.utils.utcnow().timestamp()
                if remaining <= 0:
                    break

                await self.update_status(
                    "Баны",
                    "Игроки выбирают до 2 банов через личные меню ниже.",
                    remaining=remaining,
                    draft_players=draft_players,
                    ban_status_by_player={
                        player_id: ("готово" if view.result is not None else "ожидание")
                        for player_id, view in views.items()
                    },
                )
                await asyncio.sleep(min(TIMER_UPDATE_SECONDS, remaining))
        finally:
            for view in views.values():
                view.disable_all_items()
            for message in prompt_messages:
                await delete_message(message)

        return {player_id: (view.result or set()) for player_id, view in views.items()}

    async def collect_single_pick(
        self,
        player: discord.Member,
        available_clans: list[str],
        current_team_picks: list[str],
        draft_players: list[discord.Member],
        banned_clans: set[str],
        picks_by_player: dict[int, list[str]],
        clan_rules: ClanRules,
        timeout: int,
        pick_label: str,
    ) -> str:
        deadline = discord.utils.utcnow().timestamp() + timeout
        return await self.collect_single_pick_until(
            player,
            available_clans,
            current_team_picks,
            draft_players,
            banned_clans,
            picks_by_player,
            clan_rules,
            deadline,
            pick_label,
        )

    async def collect_single_pick_until(
        self,
        player: discord.Member,
        available_clans: list[str],
        current_team_picks: list[str],
        draft_players: list[discord.Member],
        banned_clans: set[str],
        picks_by_player: dict[int, list[str]],
        clan_rules: ClanRules,
        deadline: float,
        pick_label: str,
    ) -> str:
        view = SinglePickView(player, available_clans, current_team_picks, clan_rules)
        prompt_message = await self.channel.send(f"{player.mention}, выбери {pick_label}.", view=view)
        view.prompt_message = prompt_message

        try:
            while not view.is_finished():
                remaining = deadline - discord.utils.utcnow().timestamp()
                if remaining <= 0:
                    auto_pick = random_valid_picks(1, available_clans, current_team_picks, clan_rules)[0]
                    await self.update_status(
                        "Пик-фаза",
                        f"{player.mention} не успел выбрать {pick_label}. Рандомный пик: **{auto_pick}**",
                        remaining=0,
                        draft_players=draft_players,
                        banned_clans=banned_clans,
                        available_clans=available_clans,
                        picks_by_player=picks_by_player,
                    )
                    return auto_pick

                await self.update_status(
                    "Пик-фаза",
                    f"{player.mention} выбирает {pick_label}.",
                    remaining=remaining,
                    draft_players=draft_players,
                    banned_clans=banned_clans,
                    available_clans=available_clans,
                    picks_by_player=picks_by_player,
                )
                await asyncio.sleep(min(TIMER_UPDATE_SECONDS, remaining))
        finally:
            view.disable_all_items()
            await delete_message(prompt_message)

        return view.result or random_valid_picks(1, available_clans, current_team_picks, clan_rules)[0]

    async def record_win(self, winner: discord.Member) -> None:
        if not self.waiting_for_winner:
            await self.channel.send("Сейчас бот не ждет результат игры.")
            return
        if winner.id not in self.score:
            await self.channel.send("Этот игрок не участвует в текущем драфте.")
            return

        self.score[winner.id] += 1
        self.waiting_for_winner = False
        await self.update_completed_game_status(winner)

        if self.score[winner.id] >= self.wins_to_take:
            await self.channel.send(f"Матч завершен. Победитель: {winner.mention}. Финальный счет: **{self.score_text()}**")
            active_drafts.pop(self.channel.id, None)
            return

        self.game_number += 1
        self.start_next_game()

    async def update_completed_game_status(self, winner: discord.Member) -> None:
        match_finished = self.score[winner.id] >= self.wins_to_take
        phase = "Матч завершен" if match_finished else "Игра завершена"
        details = f"Победа в игре записана за {winner.mention}. Счет: **{self.score_text()}**"
        if match_finished:
            details += f"\nПобедитель матча: {winner.mention}."

        await self.update_status(
            phase,
            details,
            draft_players=self.last_draft_players,
            banned_clans=self.last_banned_clans,
            available_clans=self.last_available_clans,
            picks_by_player=self.last_picks_by_player,
        )


def validate_team_pair(picks: list[str], clan_rules: ClanRules = DEFAULT_CLAN_RULES) -> str | None:
    if len(picks) != len(set(picks)):
        return "Нельзя брать 2 одинаковых клана."
    if "Snake" in picks and any(clan in clan_rules.clear_clans for clan in picks):
        return "Нельзя брать Snake вместе с клир-кланом."
    if sum(clan in clan_rules.kingdom_clans for clan in picks) > 1:
        return "Нельзя брать 2 клана королевств."
    if sum(clan in clan_rules.clear_clans for clan in picks) > 1:
        return "Нельзя брать 2 клир-клана."
    return None


def validate_pick(
    clans: list[str],
    count: int,
    available_clans: list[str],
    current_team_picks: list[str],
    clan_rules: ClanRules = DEFAULT_CLAN_RULES,
) -> str | None:
    if len(clans) != count:
        return f"Нужно выбрать ровно {count} клан(а) из доступного пула."
    unavailable = [clan for clan in clans if clan not in available_clans]
    if unavailable:
        return f"Этот клан забанен или недоступен: {', '.join(unavailable)}."
    return validate_team_pair([*current_team_picks, *clans], clan_rules)


def valid_single_pick_options(
    available_clans: list[str],
    current_team_picks: list[str],
    clan_rules: ClanRules = DEFAULT_CLAN_RULES,
) -> list[str]:
    options = [
        clan
        for clan in available_clans
        if validate_pick([clan], 1, available_clans, current_team_picks, clan_rules) is None
    ]
    return options or available_clans


def random_valid_picks(
    count: int,
    available_clans: list[str],
    current_team_picks: list[str],
    clan_rules: ClanRules = DEFAULT_CLAN_RULES,
) -> list[str]:
    candidates = available_clans.copy()
    random.shuffle(candidates)
    if count == 1:
        for clan in candidates:
            if validate_team_pair([*current_team_picks, clan], clan_rules) is None:
                return [clan]
    else:
        for index, clan_1 in enumerate(candidates):
            for clan_2 in candidates[index + 1 :]:
                if validate_team_pair([*current_team_picks, clan_1, clan_2], clan_rules) is None:
                    return [clan_1, clan_2]
    raise RuntimeError("Could not find a valid random pick")
