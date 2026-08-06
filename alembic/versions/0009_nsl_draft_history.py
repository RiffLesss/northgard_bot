"""store NSL draft actions and game results

Revision ID: 0009_nsl_draft_history
Revises: 0008_runtime_states
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0009_nsl_draft_history"
down_revision: str | None = "0008_runtime_states"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    draft_action_type = postgresql.ENUM("ban", "pick", name="draft_action_type", create_type=False)
    pick_type = postgresql.ENUM("clear", "eco", name="pick_type", create_type=False)
    op.create_table(
        "nsl_draft_games",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("game_number", sa.Integer(), nullable=False),
        sa.Column("team_a_id", sa.BigInteger(), nullable=False),
        sa.Column("team_b_id", sa.BigInteger(), nullable=False),
        sa.Column("winner_team_id", sa.BigInteger(), nullable=True),
        sa.Column("scheduled_match_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["team_a_id"], ["nsl_teams.id"]),
        sa.ForeignKeyConstraint(["team_b_id"], ["nsl_teams.id"]),
        sa.ForeignKeyConstraint(["winner_team_id"], ["nsl_teams.id"]),
        sa.ForeignKeyConstraint(["scheduled_match_id"], ["nsl_matches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", "game_number", name="uq_nsl_draft_games_channel_game"),
    )
    op.create_table(
        "nsl_draft_actions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("game_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("clan_id", sa.Integer(), nullable=False),
        sa.Column("action_type", draft_action_type, nullable=False),
        sa.Column("pick_type", pick_type, nullable=False),
        sa.Column("reverted", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["nsl_draft_games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["nsl_teams.id"]),
        sa.ForeignKeyConstraint(["clan_id"], ["clans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("nsl_draft_actions")
    op.drop_table("nsl_draft_games")
    op.drop_table("nsl_draft_games")
