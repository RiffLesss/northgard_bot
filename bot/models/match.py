from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum as SqlEnum, ForeignKey, Identity
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base
from bot.models.enums import BestOf, GameMode, MatchFormat


def enum_values(enum_class: type) -> list[str]:
    return [item.value for item in enum_class]


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    team1_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    team2_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    format: Mapped[MatchFormat] = mapped_column(
        SqlEnum(MatchFormat, name="match_format", values_callable=enum_values),
        nullable=False,
    )
    game_mode: Mapped[GameMode] = mapped_column(
        SqlEnum(GameMode, name="game_mode", values_callable=enum_values),
        nullable=False,
    )
    best_of: Mapped[BestOf] = mapped_column(
        SqlEnum(BestOf, name="best_of", values_callable=enum_values),
        nullable=False,
    )
    winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    team1: Mapped["Team"] = relationship(foreign_keys=[team1_id], back_populates="matches_as_team1")
    team2: Mapped["Team"] = relationship(foreign_keys=[team2_id], back_populates="matches_as_team2")
    winner_team: Mapped["Team | None"] = relationship(foreign_keys=[winner_team_id], back_populates="won_matches")
    draft_actions: Mapped[list["DraftAction"]] = relationship(back_populates="match", cascade="all, delete-orphan")
