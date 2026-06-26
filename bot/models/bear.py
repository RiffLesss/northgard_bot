from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SqlEnum, ForeignKey, Identity, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base
from bot.models.enums import BearChallengeFormat, BearChallengeStatus
from bot.models.match import enum_values


class BearTier(Base):
    __tablename__ = "bear_tiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    is_capped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    slots: Mapped[int | None] = mapped_column(Integer)

    users: Mapped[list["User"]] = relationship(back_populates="bear_tier")


class BearMatch(Base):
    __tablename__ = "bear_matches"
    __table_args__ = (UniqueConstraint("player1_id", "player2_id", name="uq_bear_matches_players"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    player1_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    player2_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    games: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    player1_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    player2_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_played: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    player1: Mapped["User"] = relationship(foreign_keys=[player1_id])
    player2: Mapped["User"] = relationship(foreign_keys=[player2_id])


class BearChallenge(Base):
    __tablename__ = "bear_challenges"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    player1_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    player2_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    format: Mapped[BearChallengeFormat] = mapped_column(
        SqlEnum(BearChallengeFormat, name="bear_challenge_format", values_callable=enum_values),
        nullable=False,
    )
    player1_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    player2_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    played: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[BearChallengeStatus] = mapped_column(
        SqlEnum(BearChallengeStatus, name="bear_challenge_status", values_callable=enum_values),
        nullable=False,
        default=BearChallengeStatus.PENDING,
        server_default=BearChallengeStatus.PENDING.value,
    )

    player1: Mapped["User"] = relationship(foreign_keys=[player1_id])
    player2: Mapped["User"] = relationship(foreign_keys=[player2_id])
    winner: Mapped["User | None"] = relationship(foreign_keys=[winner_id])
