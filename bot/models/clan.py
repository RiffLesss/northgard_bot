from sqlalchemy import Identity, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base


class Clan(Base):
    __tablename__ = "clans"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)

    draft_actions: Mapped[list["DraftAction"]] = relationship(back_populates="clan")
