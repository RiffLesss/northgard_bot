from sqlalchemy import BigInteger, Enum as SqlEnum, ForeignKey, Identity
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base
from bot.models.enums import DraftActionType, PickType
from bot.models.match import enum_values


class DraftAction(Base):
    __tablename__ = "draft_actions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    clan_id: Mapped[int] = mapped_column(ForeignKey("clans.id"), nullable=False)
    action_type: Mapped[DraftActionType] = mapped_column(
        SqlEnum(DraftActionType, name="draft_action_type", values_callable=enum_values),
        nullable=False,
    )
    pick_type: Mapped[PickType | None] = mapped_column(
        SqlEnum(PickType, name="pick_type", values_callable=enum_values),
    )

    match: Mapped["Match"] = relationship(back_populates="draft_actions")
    team: Mapped["Team"] = relationship(back_populates="draft_actions")
    clan: Mapped["Clan"] = relationship(back_populates="draft_actions")
