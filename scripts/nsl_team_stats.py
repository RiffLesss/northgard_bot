"""Print NSL draft statistics for a team.

Usage:
    python scripts/nsl_team_stats.py <nsl_team_id>

The script reads DATABASE_URL from the environment and accepts the same
PostgreSQL URL as the bot (postgresql:// or postgresql+asyncpg://...).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import Counter
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from bot.models import NslTeam
from bot.models.enums import DraftActionType, PickType
from bot.models.nsl_draft import NslDraftAction, NslDraftGame


Combo = tuple[str, ...]


def combo(names: Iterable[str]) -> Combo:
    return tuple(sorted(names, key=str.casefold))


def format_counter(values: Counter[tuple[str, ...] | str]) -> str:
    if not values:
        return "  нет данных"
    lines: list[str] = []
    for value, count in values.most_common():
        label = " + ".join(value) if isinstance(value, tuple) else value
        lines.append(f"  {label} — {count}")
    return "\n".join(lines)


async def load_stats(session: AsyncSession, team_id: int) -> tuple[NslTeam, dict[str, Counter], int, int, Counter[tuple[Combo, Combo]]]:
    team = await session.scalar(select(NslTeam).where(NslTeam.id == team_id))
    if team is None:
        raise ValueError(f"Команда с ID {team_id} не найдена")

    games = (
        await session.scalars(
            select(NslDraftGame)
            .options(selectinload(NslDraftGame.actions).selectinload(NslDraftAction.clan))
            .where((NslDraftGame.team_a_id == team_id) | (NslDraftGame.team_b_id == team_id))
            .order_by(NslDraftGame.created_at, NslDraftGame.id)
        )
    ).all()

    # Keep the counters separate so the printed sections map directly to the request.
    clear_bans: Counter[str] = Counter()
    eco_bans: Counter[str] = Counter()
    picked_combos: Counter[Combo] = Counter()
    mirror_eco = 0
    mirror_clear = 0
    losses: Counter[tuple[Combo, Combo]] = Counter()

    for game in games:
        own_actions = [action for action in game.actions if action.team_id == team_id and not action.reverted]
        opponent_id = game.team_b_id if game.team_a_id == team_id else game.team_a_id
        opponent_actions = [action for action in game.actions if action.team_id == opponent_id and not action.reverted]

        for action in own_actions:
            if action.action_type == DraftActionType.BAN:
                if action.pick_type == PickType.CLEAR:
                    clear_bans[action.clan.name] += 1
                elif action.pick_type == PickType.ECO:
                    eco_bans[action.clan.name] += 1

        own_picks = [action for action in own_actions if action.action_type == DraftActionType.PICK]
        opponent_picks = [action for action in opponent_actions if action.action_type == DraftActionType.PICK]
        if len(own_picks) == 3:
            picked_combos[combo(action.clan.name for action in own_picks)] += 1

        own_by_type = {
            pick_type: {action.clan.name for action in own_picks if action.pick_type == pick_type}
            for pick_type in PickType
        }
        opponent_by_type = {
            pick_type: {action.clan.name for action in opponent_picks if action.pick_type == pick_type}
            for pick_type in PickType
        }
        mirror_eco += len(own_by_type[PickType.ECO] & opponent_by_type[PickType.ECO])
        mirror_clear += len(own_by_type[PickType.CLEAR] & opponent_by_type[PickType.CLEAR])

        if game.winner_team_id is not None and game.winner_team_id != team_id and len(own_picks) == 3 and len(opponent_picks) == 3:
            losses[(combo(action.clan.name for action in opponent_picks), combo(action.clan.name for action in own_picks))] += 1

    return team, {"clear": clear_bans, "eco": eco_bans, "picks": picked_combos}, mirror_eco, mirror_clear, losses


async def main(team_id: int) -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Не задана переменная окружения DATABASE_URL")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            team, counters, mirror_eco, mirror_clear, losses = await load_stats(session, team_id)
    finally:
        await engine.dispose()

    print(f"Команда: {team.team_name} (ID: {team.id})")
    print("\n1. Клир баны")
    print(format_counter(counters["clear"]))
    print("\n2. Эко баны")
    print(format_counter(counters["eco"]))
    print("\n3. Пикнутые связки")
    print(format_counter(counters["picks"]))
    print(f"\n4. Количество мирорных эко пиков: {mirror_eco}")
    print(f"5. Количество мирорных клир пиков: {mirror_clear}")
    print("\n6. Связки, которым проигрывали (победившая связка → их пик)")
    print(format_counter(losses))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Статистика NSL-пиков команды")
    parser.add_argument("team_id", type=int, help="ID команды в таблице nsl_teams")
    args = parser.parse_args()
    asyncio.run(main(args.team_id))
