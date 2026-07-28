from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.ncl import NclTeam, NclTeamMember


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
