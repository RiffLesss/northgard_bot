from sqlalchemy import BigInteger, ForeignKey, Identity, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base


class NclTeam(Base):
    __tablename__ = "ncl_teams"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    team_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    elo: Mapped[int] = mapped_column(Integer, nullable=False, default=500, server_default="500")
    discord_role_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    text_channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    voice_channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

    members: Mapped[list["NclTeamMember"]] = relationship(back_populates="team", cascade="all, delete-orphan")


class NclTeamMember(Base):
    __tablename__ = "ncl_team_members"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_ncl_team_members_team_user"),
        UniqueConstraint("user_id", name="uq_ncl_team_members_user"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("ncl_teams.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    team: Mapped[NclTeam] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="ncl_teams")
