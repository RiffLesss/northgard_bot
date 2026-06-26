from enum import Enum


class MatchFormat(str, Enum):
    DUEL = "duel"
    TEAM2 = "team2"
    TEAM3 = "team3"


class GameMode(str, Enum):
    RANKED = "ranked"
    TOURNAMENT = "tournament"
    CASUAL = "casual"


class BestOf(str, Enum):
    BO1 = "1"
    BO3 = "3"
    BO5 = "5"


class DraftActionType(str, Enum):
    BAN = "ban"
    PICK = "pick"


class PickType(str, Enum):
    CLEAR = "clear"
    ECO = "eco"


class BearChallengeFormat(str, Enum):
    BO1 = "bo1"
    BO3 = "bo3"
    BO5 = "bo5"


class BearChallengeStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"
