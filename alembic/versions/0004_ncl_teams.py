"""add ncl teams

Revision ID: 0004_ncl_teams
Revises: 0003_clan_metadata_seed
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0004_ncl_teams"
down_revision: str | None = "0003_clan_metadata_seed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ncl_teams",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("team_name", sa.Text(), nullable=False),
        sa.Column("elo", sa.Integer(), server_default="500", nullable=False),
        sa.Column("discord_role_id", sa.BigInteger(), nullable=False),
        sa.Column("text_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("voice_channel_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("discord_role_id"),
        sa.UniqueConstraint("team_name"),
        sa.UniqueConstraint("text_channel_id"),
        sa.UniqueConstraint("voice_channel_id"),
    )
    op.create_table(
        "ncl_team_members",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["ncl_teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_ncl_team_members_team_user"),
        sa.UniqueConstraint("user_id", name="uq_ncl_team_members_user"),
    )


def downgrade() -> None:
    op.drop_table("ncl_team_members")
    op.drop_table("ncl_teams")
