from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.bear import BearChallenge, BearMatch, BearTier
from bot.models.enums import BearChallengeFormat, BearChallengeStatus
from bot.models.user import User
from bot.repositories.bear_repository import BearRepository


BEAR_ROLE_ID = 1519832582093803743
DEFAULT_BEAR_TIER_ID = 5
TSAR_BEAR_TIER_ID = 1


@dataclass(frozen=True)
class ChallengePlan:
    challenge_format: BearChallengeFormat
    wins_required: int


class BearService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.bears = BearRepository(session)

    async def become_a_bear(self, discord_id: int) -> tuple[User, bool]:
        user = await self.bears.get_user_by_discord_id(discord_id)
        if user is None:
            raise ValueError("Сначала нужно зарегистрироваться через /register.")
        if user.is_bear:
            return user, False

        last_tier = await self.bears.get_last_tier()
        user.is_bear = True
        user.bear_tier_id = last_tier.id
        user.tier_placement = await self.bears.max_placement(last_tier.id) + 1
        await self.session.commit()
        return user, True

    async def get_registered_bear(self, discord_id: int) -> User:
        user = await self.bears.get_user_by_discord_id(discord_id)
        if user is None:
            raise ValueError("Игрок не зарегистрирован через /register.")
        if not user.is_bear:
            raise ValueError("Игрок еще не медведь.")
        return user

    async def tierlist(self) -> list[tuple[BearTier, list[User]]]:
        tiers = await self.bears.list_tiers()
        return [(tier, await self.bears.tier_members(tier.id)) for tier in tiers]

    async def record_duel(self, player1: User, player2: User, winner: User) -> BearMatch:
        bear_match = await self.bears.record_bear_match(player1.id, player2.id, winner.id)
        await self.session.commit()
        return bear_match

    async def create_challenge(self, challenger: User, opponent: User) -> tuple[BearChallenge, ChallengePlan]:
        if challenger.id == opponent.id:
            raise ValueError("Нельзя вызвать самого себя.")
        if challenger.bear_tier_id is None or opponent.bear_tier_id is None:
            raise ValueError("Оба игрока должны быть размещены в медвежьем тире.")
        if opponent.bear_tier_id > challenger.bear_tier_id:
            raise ValueError("Можно вызвать игрока только из своего тира или тира выше.")
        existing = await self.bears.active_challenge_between(challenger.id, opponent.id)
        if existing is not None:
            raise ValueError("Между этими игроками уже есть активный медвежий вызов.")

        challenge_format = BearChallengeFormat.BO5 if opponent.bear_tier_id == TSAR_BEAR_TIER_ID else BearChallengeFormat.BO3
        wins_required = 3 if challenge_format == BearChallengeFormat.BO5 else 2
        challenge = await self.bears.create_challenge(challenger.id, opponent.id, challenge_format)
        await self.session.commit()
        return challenge, ChallengePlan(challenge_format=challenge_format, wins_required=wins_required)

    async def start_challenge(self, challenge: BearChallenge) -> None:
        challenge.status = BearChallengeStatus.IN_PROGRESS
        await self.session.commit()

    async def finish_challenge(self, challenge: BearChallenge, winner: User, loser: User, p1_wins: int, p2_wins: int) -> None:
        challenge.player1_wins = p1_wins
        challenge.player2_wins = p2_wins
        challenge.winner_id = winner.id
        challenge.played = datetime.utcnow()
        challenge.status = BearChallengeStatus.FINISHED
        await self.apply_ladder_result(winner, loser)
        await self.session.commit()

    async def apply_ladder_result(self, winner: User, loser: User) -> None:
        if winner.bear_tier_id is None or loser.bear_tier_id is None:
            return

        winner_tier = winner.bear_tier_id
        loser_tier = loser.bear_tier_id
        winner_place = winner.tier_placement or 999_999
        loser_place = loser.tier_placement or 999_999

        if winner_tier == loser_tier:
            if winner_place > loser_place:
                await self.move_within_tier(winner, loser_place)
            return

        if winner_tier > loser_tier:
            target_tier = loser_tier
            target_tier_row = await self.bears.get_tier(target_tier)
            if target_tier_row is None:
                return

            target_count = await self.bears.count_tier_members(target_tier)
            was_full = target_tier_row.is_capped and target_tier_row.slots is not None and target_count >= target_tier_row.slots

            await self.remove_from_current_tier(winner)
            winner.bear_tier_id = target_tier
            winner.tier_placement = await self.bears.max_placement(target_tier) + 1

            if was_full:
                await self.remove_from_current_tier(loser)
                await self.insert_at_first_place_with_cascade(loser, loser_tier + 1)

    async def move_within_tier(self, user: User, new_position: int) -> None:
        if user.bear_tier_id is None or user.tier_placement is None:
            return
        old_position = user.tier_placement
        if new_position >= old_position:
            return

        result = await self.session.scalars(
            select(User).where(
                User.is_bear.is_(True),
                User.bear_tier_id == user.bear_tier_id,
                User.id != user.id,
                User.tier_placement >= new_position,
                User.tier_placement < old_position,
            )
        )
        for other in result:
            other.tier_placement = (other.tier_placement or 0) + 1
        user.tier_placement = new_position

    async def remove_from_current_tier(self, user: User) -> None:
        if user.bear_tier_id is None or user.tier_placement is None:
            return
        old_tier = user.bear_tier_id
        old_position = user.tier_placement
        result = await self.session.scalars(
            select(User).where(
                User.is_bear.is_(True),
                User.bear_tier_id == old_tier,
                User.id != user.id,
                User.tier_placement > old_position,
            )
        )
        for other in result:
            other.tier_placement = (other.tier_placement or 0) - 1

    async def insert_at_position(self, user: User, tier_id: int, position: int) -> None:
        result = await self.session.scalars(
            select(User).where(
                User.is_bear.is_(True),
                User.bear_tier_id == tier_id,
                User.id != user.id,
                User.tier_placement >= position,
            )
        )
        for other in result:
            other.tier_placement = (other.tier_placement or 0) + 1
        user.bear_tier_id = tier_id
        user.tier_placement = position

    async def insert_at_first_place_with_cascade(self, user: User, tier_id: int) -> None:
        tier = await self.bears.get_tier(tier_id)
        if tier is None:
            last_tier = await self.bears.get_last_tier()
            await self.insert_at_position(user, last_tier.id, await self.bears.max_placement(last_tier.id) + 1)
            return

        displaced_user: User | None = None
        members = await self.bears.tier_members(tier_id)
        if tier.is_capped and tier.slots is not None and len(members) >= tier.slots:
            displaced_user = members[-1]
            await self.remove_from_current_tier(displaced_user)

        await self.insert_at_position(user, tier_id, 1)

        if displaced_user is not None:
            await self.insert_at_first_place_with_cascade(displaced_user, tier_id + 1)
