from sqlalchemy import BigInteger, ForeignKey, Identity, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    members: Mapped[list["TeamMember"]] = relationship(back_populates="team", cascade="all, delete-orphan")
    matches_as_team1: Mapped[list["Match"]] = relationship(
        foreign_keys="Match.team1_id",
        back_populates="team1",
    )
    matches_as_team2: Mapped[list["Match"]] = relationship(
        foreign_keys="Match.team2_id",
        back_populates="team2",
    )
    won_matches: Mapped[list["Match"]] = relationship(
        foreign_keys="Match.winner_team_id",
        back_populates="winner_team",
    )
    draft_actions: Mapped[list["DraftAction"]] = relationship(back_populates="team")


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    team: Mapped[Team] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="teams")
