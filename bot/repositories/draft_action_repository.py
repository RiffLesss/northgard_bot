from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.draft_action import DraftAction
from bot.models.enums import DraftActionType, PickType


class DraftActionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        match_id: int,
        team_id: int,
        clan_id: int,
        action_type: DraftActionType,
        pick_type: PickType | None = None,
    ) -> DraftAction:
        action = DraftAction(
            match_id=match_id,
            team_id=team_id,
            clan_id=clan_id,
            action_type=action_type,
            pick_type=pick_type,
        )
        self.session.add(action)
        await self.session.flush()
        return action
