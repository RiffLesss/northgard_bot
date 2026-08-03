from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.ncl import NclMatch, NclTeam, NclTeamMember


class NclTeamRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_role_id(self, role_id: int) -> NclTeam | None:
        return await self.session.scalar(
            select(NclTeam)
            .options(selectinload(NclTeam.members).selectinload(NclTeamMember.user))
            .where(NclTeam.discord_role_id == role_id)
        )

    async def get_by_name(self, team_name: str) -> NclTeam | None:
        return await self.session.scalar(select(NclTeam).where(NclTeam.team_name == team_name))

    async def get_for_user_id(self, user_id: int) -> NclTeam | None:
        return await self.session.scalar(
            select(NclTeam)
            .join(NclTeamMember)
            .options(selectinload(NclTeam.members).selectinload(NclTeamMember.user))
            .where(NclTeamMember.user_id == user_id)
        )

    async def create(
        self,
        team_name: str,
        discord_role_id: int,
        text_channel_id: int,
        voice_channel_id: int,
        user_ids: list[int],
        elo: int = 500,
    ) -> NclTeam:
        team = NclTeam(
            team_name=team_name,
            elo=elo,
            discord_role_id=discord_role_id,
            text_channel_id=text_channel_id,
            voice_channel_id=voice_channel_id,
        )
        self.session.add(team)
        await self.session.flush()
        for user_id in user_ids:
            self.session.add(NclTeamMember(team_id=team.id, user_id=user_id))
        await self.session.flush()
        return team

    async def list_teams(self) -> list[NclTeam]:
        result = await self.session.scalars(select(NclTeam).order_by(NclTeam.team_name))
        return list(result)

    async def clear_schedule(self) -> None:
        for match in await self.list_matches():
            await self.session.delete(match)
        await self.session.flush()

    async def create_match(
        self,
        week_number: int,
        week_start: date,
        week_end: date,
        team1_id: int,
        team2_id: int,
    ) -> NclMatch:
        match = NclMatch(
            week_number=week_number,
            week_start=week_start,
            week_end=week_end,
            team1_id=team1_id,
            team2_id=team2_id,
        )
        self.session.add(match)
        await self.session.flush()
        return match

    async def list_matches(self) -> list[NclMatch]:
        result = await self.session.scalars(
            select(NclMatch)
            .options(selectinload(NclMatch.team1), selectinload(NclMatch.team2), selectinload(NclMatch.winner_team))
            .order_by(NclMatch.week_number, NclMatch.id)
        )
        return list(result)

    async def has_played_matches(self) -> bool:
        return (
            await self.session.scalar(
                select(NclMatch.id).where(NclMatch.played_at.is_not(None)).limit(1)
            )
            is not None
        )

    async def get_match_by_id(self, match_id: int) -> NclMatch | None:
        return await self.session.scalar(
            select(NclMatch)
            .options(selectinload(NclMatch.team1), selectinload(NclMatch.team2), selectinload(NclMatch.winner_team))
            .where(NclMatch.id == match_id)
        )

    async def find_current_week_match(self, team_a_id: int, team_b_id: int, current_date: date) -> NclMatch | None:
        left_id = min(team_a_id, team_b_id)
        right_id = max(team_a_id, team_b_id)
        return await self.session.scalar(
            select(NclMatch)
            .options(selectinload(NclMatch.team1), selectinload(NclMatch.team2), selectinload(NclMatch.winner_team))
            .where(
                NclMatch.week_start <= current_date,
                NclMatch.week_end >= current_date,
                NclMatch.team1_id == left_id,
                NclMatch.team2_id == right_id,
                NclMatch.played_at.is_(None),
            )
        )

    async def finish_match(
        self,
        match: NclMatch,
        winner_team_id: int,
        team1_game_wins: int,
        team2_game_wins: int,
        team1_elo_after: int,
        team2_elo_after: int,
    ) -> None:
        match.team1_elo_before = match.team1.elo
        match.team2_elo_before = match.team2.elo
        match.team1_elo_after = team1_elo_after
        match.team2_elo_after = team2_elo_after
        match.team1_game_wins = team1_game_wins
        match.team2_game_wins = team2_game_wins
        match.winner_team_id = winner_team_id
        match.played_at = datetime.utcnow()
        match.team1.elo = team1_elo_after
        match.team2.elo = team2_elo_after
        await self.session.flush()
