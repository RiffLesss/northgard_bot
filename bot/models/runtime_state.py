from datetime import datetime

from sqlalchemy import DateTime, JSON, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.models.base import Base


class RuntimeState(Base):
    """Durable state used to rebuild Discord UI after a bot restart."""

    __tablename__ = "runtime_states"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
