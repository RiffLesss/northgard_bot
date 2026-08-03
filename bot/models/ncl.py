from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Identity, Integer, Text, UniqueConstraint
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
    home_matches: Mapped[list["NclMatch"]] = relationship(
        foreign_keys="NclMatch.team1_id",
        back_populates="team1",
    )
    away_matches: Mapped[list["NclMatch"]] = relationship(
        foreign_keys="NclMatch.team2_id",
        back_populates="team2",
    )


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


class NclMatch(Base):
    __tablename__ = "ncl_matches"
    __table_args__ = (UniqueConstraint("week_number", "team1_id", "team2_id", name="uq_ncl_matches_week_pair"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    week_number: Mapped[int] = mapped_column(Integer, nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    week_end: Mapped[date] = mapped_column(Date, nullable=False)
    team1_id: Mapped[int] = mapped_column(ForeignKey("ncl_teams.id", ondelete="CASCADE"), nullable=False)
    team2_id: Mapped[int] = mapped_column(ForeignKey("ncl_teams.id", ondelete="CASCADE"), nullable=False)
    winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("ncl_teams.id"))
    team1_game_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    team2_game_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    team1_elo_before: Mapped[int | None] = mapped_column(Integer)
    team2_elo_before: Mapped[int | None] = mapped_column(Integer)
    team1_elo_after: Mapped[int | None] = mapped_column(Integer)
    team2_elo_after: Mapped[int | None] = mapped_column(Integer)
    played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    team1: Mapped[NclTeam] = relationship(foreign_keys=[team1_id], back_populates="home_matches")
    team2: Mapped[NclTeam] = relationship(foreign_keys=[team2_id], back_populates="away_matches")
    winner_team: Mapped[NclTeam | None] = relationship(foreign_keys=[winner_team_id])
