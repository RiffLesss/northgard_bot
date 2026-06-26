from sqlalchemy import BigInteger, ForeignKey, Identity, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base


class PlayerBlacklist(Base):
    __tablename__ = "player_blacklist"
    __table_args__ = (
        UniqueConstraint("player_id", "blacklisted_player_id", name="uq_player_blacklist_pair"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    blacklisted_player_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    player: Mapped["User"] = relationship(
        foreign_keys=[player_id],
        back_populates="blacklist_entries",
    )
    blacklisted_player: Mapped["User"] = relationship(
        foreign_keys=[blacklisted_player_id],
        back_populates="blacklisted_by_entries",
    )
