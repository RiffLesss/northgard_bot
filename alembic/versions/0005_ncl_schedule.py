"""add ncl schedule

Revision ID: 0005_ncl_schedule
Revises: 0004_ncl_teams
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0005_ncl_schedule"
down_revision: str | None = "0004_ncl_teams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ncl_teams", sa.Column("seed", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_ncl_teams_seed", "ncl_teams", ["seed"])
    op.create_table(
        "ncl_matches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("week_number", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column("team1_id", sa.BigInteger(), nullable=False),
        sa.Column("team2_id", sa.BigInteger(), nullable=False),
        sa.Column("winner_team_id", sa.BigInteger(), nullable=True),
        sa.Column("team1_game_wins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("team2_game_wins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("team1_elo_before", sa.Integer(), nullable=True),
        sa.Column("team2_elo_before", sa.Integer(), nullable=True),
        sa.Column("team1_elo_after", sa.Integer(), nullable=True),
        sa.Column("team2_elo_after", sa.Integer(), nullable=True),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team1_id"], ["ncl_teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team2_id"], ["ncl_teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["winner_team_id"], ["ncl_teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("week_number", "team1_id", "team2_id", name="uq_ncl_matches_week_pair"),
    )


def downgrade() -> None:
    op.drop_table("ncl_matches")
    op.drop_constraint("uq_ncl_teams_seed", "ncl_teams", type_="unique")
    op.drop_column("ncl_teams", "seed")
