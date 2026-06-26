import itertools
import random
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.enums import BestOf, DraftActionType, GameMode, MatchFormat, PickType
from bot.models.match import Match
from bot.models.team import Team
from bot.models.user import User
from bot.repositories.clan_repository import ClanRepository
from bot.repositories.draft_action_repository import DraftActionRepository
from bot.repositories.match_repository import MatchRepository
from bot.repositories.team_repository import TeamRepository
from bot.repositories.user_repository import UserRepository


CLEAR_CLANS = ["Wolf", "Lynx", "Eagle", "Hound"]
ALL_CLANS = [
    "Stag",
    "Goat",
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
ECO_CLANS = [clan for clan in ALL_CLANS if clan not in CLEAR_CLANS]
NORMAL_RATING_SPREAD = 300
K_FACTOR = 32


@dataclass(frozen=True)
class QueueEntry:
    user_id: int
    discord_id: int
    nickname: str
    rating: int
    wide: bool
    joined_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class TeamSplit:
    team_a: tuple[QueueEntry, QueueEntry, QueueEntry]
    team_b: tuple[QueueEntry, QueueEntry, QueueEntry]
    rating_diff: int


@dataclass(frozen=True)
class CasualGroup:
    users: tuple[User, ...]


@dataclass(frozen=True)
class CreatedTeam3Match:
    match: Match
    team1: Team
    team2: Team


@dataclass(frozen=True)
class Team3DraftStep:
    side: str
    action_type: DraftActionType
    pick_type: PickType


TEAM3_DRAFT_STEPS = [
    Team3DraftStep("A", DraftActionType.BAN, PickType.CLEAR),
    Team3DraftStep("B", DraftActionType.BAN, PickType.CLEAR),
    Team3DraftStep("B", DraftActionType.PICK, PickType.CLEAR),
    Team3DraftStep("A", DraftActionType.PICK, PickType.CLEAR),
    Team3DraftStep("B", DraftActionType.BAN, PickType.ECO),
    Team3DraftStep("A", DraftActionType.BAN, PickType.ECO),
    Team3DraftStep("A", DraftActionType.BAN, PickType.ECO),
    Team3DraftStep("B", DraftActionType.BAN, PickType.ECO),
    Team3DraftStep("B", DraftActionType.PICK, PickType.ECO),
    Team3DraftStep("A", DraftActionType.PICK, PickType.ECO),
    Team3DraftStep("B", DraftActionType.BAN, PickType.ECO),
    Team3DraftStep("A", DraftActionType.BAN, PickType.ECO),
    Team3DraftStep("A", DraftActionType.PICK, PickType.ECO),
    Team3DraftStep("B", DraftActionType.PICK, PickType.ECO),
]


def find_best_ranked_match(entries: list[QueueEntry]) -> TeamSplit | None:
    if len(entries) < 6:
        return None

    best: TeamSplit | None = None
    for six in itertools.combinations(entries, 6):
        ratings = [entry.rating for entry in six]
        is_normal_match = max(ratings) - min(ratings) <= NORMAL_RATING_SPREAD
        is_wide_match = all(entry.wide for entry in six)
        if not is_normal_match and not is_wide_match:
            continue
        split = best_team_split(six)
        if best is None or split.rating_diff < best.rating_diff:
            best = split
    return best


def best_team_split(entries: tuple[QueueEntry, ...]) -> TeamSplit:
    first = entries[0]
    best: TeamSplit | None = None
    for combo in itertools.combinations(entries[1:], 2):
        team_a = (first, *combo)
        team_b = tuple(entry for entry in entries if entry not in team_a)
        diff = abs(sum(entry.rating for entry in team_a) - sum(entry.rating for entry in team_b))
        split = TeamSplit(team_a=team_a, team_b=team_b, rating_diff=diff)  # type: ignore[arg-type]
        if best is None or diff < best.rating_diff:
            best = split
    if best is None:
        raise ValueError("Need exactly 6 entries")
    return best


def split_casual_groups(groups: list[CasualGroup]) -> tuple[list[User], list[User]]:
    shuffled = groups.copy()
    random.shuffle(shuffled)
    team_a: list[User] = []
    team_b: list[User] = []
    for group in sorted(shuffled, key=lambda item: len(item.users), reverse=True):
        if len(team_a) <= len(team_b) and len(team_a) + len(group.users) <= 3:
            team_a.extend(group.users)
        elif len(team_b) + len(group.users) <= 3:
            team_b.extend(group.users)
        elif len(team_a) + len(group.users) <= 3:
            team_a.extend(group.users)
        else:
            raise ValueError("Cannot split casual groups into two teams")
    if len(team_a) != 3 or len(team_b) != 3:
        raise ValueError("Casual match needs exactly 3 players per team")
    return team_a, team_b


def expected_score(team_rating: float, opponent_rating: float) -> float:
    return 1 / (1 + 10 ** ((opponent_rating - team_rating) / 400))


def rating_delta(winner_avg: float, loser_avg: float) -> int:
    delta = round(K_FACTOR * (1 - expected_score(winner_avg, loser_avg)))
    return max(1, delta)


class Team3Service:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.users = UserRepository(session)
        self.teams = TeamRepository(session)
        self.matches = MatchRepository(session)
        self.clans = ClanRepository(session)
        self.draft_actions = DraftActionRepository(session)

    async def get_registered_user(self, discord_id: int) -> User:
        user = await self.users.get_by_discord_id(discord_id)
        if user is None:
            raise ValueError("Сначала нужно зарегистрироваться через /register.")
        return user

    async def create_match(
        self,
        team1_user_ids: list[int],
        team2_user_ids: list[int],
        game_mode: GameMode,
        best_of: BestOf = BestOf.BO1,
    ) -> CreatedTeam3Match:
        team1 = await self.teams.add(team1_user_ids)
        team2 = await self.teams.add(team2_user_ids)
        match = await self.matches.add(team1.id, team2.id, MatchFormat.TEAM3, game_mode, best_of)
        await self.session.commit()
        return CreatedTeam3Match(match=match, team1=team1, team2=team2)

    async def record_draft_action(
        self,
        match_id: int,
        team_id: int,
        clan_name: str,
        action_type: DraftActionType,
        pick_type: PickType,
    ) -> None:
        clan = await self.clans.get_by_name(clan_name)
        if clan is None:
            clan = await self.clans.add(clan_name)
        await self.draft_actions.add(match_id, team_id, clan.id, action_type, pick_type)
        await self.session.commit()

    async def finish_ranked_match(
        self,
        match_id: int,
        winner_team_id: int,
        winner_user_ids: list[int],
        loser_user_ids: list[int],
    ) -> int:
        winners = [await self.users.get_by_id(user_id) for user_id in winner_user_ids]
        losers = [await self.users.get_by_id(user_id) for user_id in loser_user_ids]
        winner_users = [user for user in winners if user is not None]
        loser_users = [user for user in losers if user is not None]
        winner_avg = sum(user.team_rating for user in winner_users) / len(winner_users)
        loser_avg = sum(user.team_rating for user in loser_users) / len(loser_users)
        delta = rating_delta(winner_avg, loser_avg)
        for user in winner_users:
            user.team_rating += delta
        for user in loser_users:
            user.team_rating = max(0, user.team_rating - delta)

        match = await self.matches.get_by_id(match_id)
        if match is not None:
            match.winner_team_id = winner_team_id
            match.played_at = datetime.utcnow()
        await self.session.commit()
        return delta

    async def finish_casual_match(self, match_id: int, winner_team_id: int) -> None:
        match = await self.matches.get_by_id(match_id)
        if match is not None:
            match.winner_team_id = winner_team_id
            match.played_at = datetime.utcnow()
        await self.session.commit()
