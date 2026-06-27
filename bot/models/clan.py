from sqlalchemy import Boolean, Identity, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base


class Clan(Base):
    __tablename__ = "clans"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    is_clear: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    is_kingdom: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    draft_actions: Mapped[list["DraftAction"]] = relationship(back_populates="clan")
