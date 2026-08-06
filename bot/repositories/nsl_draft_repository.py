from sqlalchemy import select, update

from bot.models.enums import DraftActionType, PickType
from bot.models.nsl_draft import NslDraftAction, NslDraftGame


class NslDraftRepository:
    def __init__(self, session):
        self.session = session

    async def create_game(
        self,
        channel_id: int,
        game_number: int,
        team_a_id: int,
        team_b_id: int,
        scheduled_match_id: int | None = None,
    ) -> NslDraftGame:
        game = NslDraftGame(
            channel_id=channel_id,
            game_number=game_number,
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            scheduled_match_id=scheduled_match_id,
        )
        self.session.add(game)
        await self.session.flush()
        return game

    async def add_action(
        self,
        game_id: int,
        team_id: int,
        clan_id: int,
        action_type: DraftActionType,
        pick_type: PickType,
    ) -> NslDraftAction:
        action = NslDraftAction(
            game_id=game_id,
            team_id=team_id,
            clan_id=clan_id,
            action_type=action_type,
            pick_type=pick_type,
        )
        self.session.add(action)
        await self.session.flush()
        return action

    async def mark_reverted(self, game_id: int, clan_id: int) -> None:
        await self.session.execute(
            update(NslDraftAction)
            .where(NslDraftAction.game_id == game_id, NslDraftAction.clan_id == clan_id)
            .values(reverted=True)
        )

    async def finish_game(self, game_id: int, winner_team_id: int) -> None:
        await self.session.execute(
            update(NslDraftGame)
            .where(NslDraftGame.id == game_id)
            .values(winner_team_id=winner_team_id)
        )

    async def get_game(self, game_id: int) -> NslDraftGame | None:
        return await self.session.scalar(select(NslDraftGame).where(NslDraftGame.id == game_id))
