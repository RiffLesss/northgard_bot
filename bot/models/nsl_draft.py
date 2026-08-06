from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SqlEnum, ForeignKey, Identity, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base
from bot.models.enums import DraftActionType, PickType
from bot.models.match import enum_values


class NslDraftGame(Base):
    __tablename__ = "nsl_draft_games"
    __table_args__ = (UniqueConstraint("channel_id", "game_number", name="uq_nsl_draft_games_channel_game"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    game_number: Mapped[int] = mapped_column(Integer, nullable=False)
    team_a_id: Mapped[int] = mapped_column(ForeignKey("nsl_teams.id"), nullable=False)
    team_b_id: Mapped[int] = mapped_column(ForeignKey("nsl_teams.id"), nullable=False)
    winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("nsl_teams.id"))
    scheduled_match_id: Mapped[int | None] = mapped_column(ForeignKey("nsl_matches.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    actions: Mapped[list["NslDraftAction"]] = relationship(back_populates="game", cascade="all, delete-orphan")


class NslDraftAction(Base):
    __tablename__ = "nsl_draft_actions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("nsl_draft_games.id", ondelete="CASCADE"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("nsl_teams.id"), nullable=False)
    clan_id: Mapped[int] = mapped_column(ForeignKey("clans.id"), nullable=False)
    action_type: Mapped[DraftActionType] = mapped_column(
        SqlEnum(DraftActionType, name="draft_action_type", values_callable=enum_values), nullable=False
    )
    pick_type: Mapped[PickType] = mapped_column(
        SqlEnum(PickType, name="pick_type", values_callable=enum_values), nullable=False
    )
    reverted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    game: Mapped[NslDraftGame] = relationship(back_populates="actions")
    clan: Mapped["Clan"] = relationship()
