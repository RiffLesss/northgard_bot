from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    steam_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    nickname: Mapped[str | None] = mapped_column(Text)
    duel_rating: Mapped[int] = mapped_column(Integer, nullable=False, default=500, server_default="500")
    team_rating: Mapped[int] = mapped_column(Integer, nullable=False, default=500, server_default="500")
    is_bear: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    bear_tier_id: Mapped[int | None] = mapped_column(ForeignKey("bear_tiers.id"))
    tier_placement: Mapped[int | None] = mapped_column(Integer)

    teams: Mapped[list["TeamMember"]] = relationship(back_populates="user")
    bear_tier: Mapped["BearTier | None"] = relationship(back_populates="users")
    blacklist_entries: Mapped[list["PlayerBlacklist"]] = relationship(
        foreign_keys="PlayerBlacklist.player_id",
        back_populates="player",
    )
    blacklisted_by_entries: Mapped[list["PlayerBlacklist"]] = relationship(
        foreign_keys="PlayerBlacklist.blacklisted_player_id",
        back_populates="blacklisted_player",
    )


class BotAdmin(Base):
    __tablename__ = "bot_admins"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
