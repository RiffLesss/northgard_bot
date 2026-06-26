from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.enums import BestOf, GameMode, MatchFormat
from bot.models.match import Match


class MatchRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, match_id: int) -> Match | None:
        return await self.session.get(Match, match_id)

    async def add(
        self,
        team1_id: int,
        team2_id: int,
        match_format: MatchFormat,
        game_mode: GameMode,
        best_of: BestOf,
        played_at: datetime | None = None,
    ) -> Match:
        match = Match(
            team1_id=team1_id,
            team2_id=team2_id,
            format=match_format,
            game_mode=game_mode,
            best_of=best_of,
            played_at=played_at,
        )
        self.session.add(match)
        await self.session.flush()
        return match

    async def list_recent(self, limit: int = 20) -> list[Match]:
        result = await self.session.scalars(select(Match).order_by(Match.id.desc()).limit(limit))
        return list(result)
