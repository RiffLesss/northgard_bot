from datetime import datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.bear import BearChallenge, BearMatch, BearTier
from bot.models.enums import BearChallengeFormat, BearChallengeStatus
from bot.models.user import User


def ordered_pair(player1_id: int, player2_id: int) -> tuple[int, int]:
    return (player1_id, player2_id) if player1_id < player2_id else (player2_id, player1_id)


class BearRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_discord_id(self, discord_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.discord_id == discord_id))

    async def get_tier(self, tier_id: int) -> BearTier | None:
        return await self.session.get(BearTier, tier_id)

    async def get_last_tier(self) -> BearTier:
        tier = await self.session.scalar(select(BearTier).order_by(BearTier.id.desc()).limit(1))
        if tier is None:
            raise RuntimeError("Bear tiers are not initialized")
        return tier

    async def list_tiers(self) -> list[BearTier]:
        result = await self.session.scalars(select(BearTier).order_by(BearTier.id))
        return list(result)

    async def count_tier_members(self, tier_id: int) -> int:
        return await self.session.scalar(
            select(func.count()).select_from(User).where(User.is_bear.is_(True), User.bear_tier_id == tier_id)
        ) or 0

    async def max_placement(self, tier_id: int) -> int:
        return await self.session.scalar(
            select(func.coalesce(func.max(User.tier_placement), 0)).where(
                User.is_bear.is_(True),
                User.bear_tier_id == tier_id,
            )
        ) or 0

    async def tier_members(self, tier_id: int) -> list[User]:
        result = await self.session.scalars(
            select(User)
            .where(User.is_bear.is_(True), User.bear_tier_id == tier_id)
            .order_by(User.tier_placement.asc().nullslast(), User.id)
        )
        return list(result)

    async def get_bear_match(self, player1_id: int, player2_id: int) -> BearMatch | None:
        first_id, second_id = ordered_pair(player1_id, player2_id)
        return await self.session.scalar(
            select(BearMatch).where(BearMatch.player1_id == first_id, BearMatch.player2_id == second_id)
        )

    async def record_bear_match(self, player1_id: int, player2_id: int, winner_id: int) -> BearMatch:
        first_id, second_id = ordered_pair(player1_id, player2_id)
        bear_match = await self.get_bear_match(first_id, second_id)
        if bear_match is None:
            bear_match = BearMatch(player1_id=first_id, player2_id=second_id)
            self.session.add(bear_match)
            await self.session.flush()

        bear_match.games += 1
        bear_match.last_played = datetime.utcnow()
        if winner_id == bear_match.player1_id:
            bear_match.player1_wins += 1
        elif winner_id == bear_match.player2_id:
            bear_match.player2_wins += 1
        else:
            raise ValueError("Winner is not part of this bear match")
        return bear_match

    async def create_challenge(
        self,
        player1_id: int,
        player2_id: int,
        challenge_format: BearChallengeFormat,
    ) -> BearChallenge:
        challenge = BearChallenge(
            player1_id=player1_id,
            player2_id=player2_id,
            format=challenge_format,
            status=BearChallengeStatus.PENDING,
        )
        self.session.add(challenge)
        await self.session.flush()
        return challenge

    async def active_challenge_between(self, player1_id: int, player2_id: int) -> BearChallenge | None:
        return await self.session.scalar(
            select(BearChallenge).where(
                BearChallenge.status != BearChallengeStatus.FINISHED,
                or_(
                    (BearChallenge.player1_id == player1_id) & (BearChallenge.player2_id == player2_id),
                    (BearChallenge.player1_id == player2_id) & (BearChallenge.player2_id == player1_id),
                ),
            )
        )

    def users_in_same_or_higher_tier_query(self) -> Select[tuple[User]]:
        return select(User).where(User.is_bear.is_(True), User.bear_tier_id.is_not(None))
