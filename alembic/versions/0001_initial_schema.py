"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


match_format = postgresql.ENUM("duel", "team2", "team3", name="match_format", create_type=False)
game_mode = postgresql.ENUM("ranked", "tournament", "casual", name="game_mode", create_type=False)
best_of = postgresql.ENUM("1", "3", "5", name="best_of", create_type=False)
draft_action_type = postgresql.ENUM("ban", "pick", name="draft_action_type", create_type=False)
pick_type = postgresql.ENUM("clear", "eco", name="pick_type", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    match_format.create(bind, checkfirst=True)
    game_mode.create(bind, checkfirst=True)
    best_of.create(bind, checkfirst=True)
    draft_action_type.create(bind, checkfirst=True)
    pick_type.create(bind, checkfirst=True)

    op.create_table(
        "bot_admins",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "clans",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("discord_id", sa.BigInteger(), nullable=False),
        sa.Column("steam_id", sa.BigInteger(), nullable=False),
        sa.Column("nickname", sa.Text(), nullable=True),
        sa.Column("duel_rating", sa.Integer(), server_default="500", nullable=False),
        sa.Column("team_rating", sa.Integer(), server_default="500", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("discord_id"),
        sa.UniqueConstraint("steam_id"),
    )
    op.create_table(
        "matches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("team1_id", sa.BigInteger(), nullable=False),
        sa.Column("team2_id", sa.BigInteger(), nullable=False),
        sa.Column("format", match_format, nullable=False),
        sa.Column("game_mode", game_mode, nullable=False),
        sa.Column("best_of", best_of, nullable=False),
        sa.Column("winner_team_id", sa.BigInteger(), nullable=True),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team1_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["team2_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["winner_team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "player_blacklist",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("blacklisted_player_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["blacklisted_player_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "blacklisted_player_id", name="uq_player_blacklist_pair"),
    )
    op.create_table(
        "team_members",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
    )
    op.create_table(
        "draft_actions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("clan_id", sa.Integer(), nullable=False),
        sa.Column("action_type", draft_action_type, nullable=False),
        sa.Column("pick_type", pick_type, nullable=True),
        sa.ForeignKeyConstraint(["clan_id"], ["clans.id"]),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("draft_actions")
    op.drop_table("team_members")
    op.drop_table("player_blacklist")
    op.drop_table("matches")
    op.drop_table("users")
    op.drop_table("teams")
    op.drop_table("clans")
    op.drop_table("bot_admins")

    bind = op.get_bind()
    pick_type.drop(bind, checkfirst=True)
    draft_action_type.drop(bind, checkfirst=True)
    best_of.drop(bind, checkfirst=True)
    game_mode.drop(bind, checkfirst=True)
    match_format.drop(bind, checkfirst=True)
