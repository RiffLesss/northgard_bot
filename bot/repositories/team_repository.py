from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.team import Team, TeamMember


class TeamRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, team_id: int) -> Team | None:
        return await self.session.get(Team, team_id)

    async def add(self, user_ids: list[int]) -> Team:
        team = Team()
        self.session.add(team)
        await self.session.flush()

        for user_id in user_ids:
            self.session.add(TeamMember(team_id=team.id, user_id=user_id))

        await self.session.flush()
        return team
