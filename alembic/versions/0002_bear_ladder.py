"""bear ladder

Revision ID: 0002_bear_ladder
Revises: 0001_initial_schema
Create Date: 2026-06-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "0002_bear_ladder"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


bear_challenge_format = postgresql.ENUM("bo1", "bo3", "bo5", name="bear_challenge_format", create_type=False)
bear_challenge_status = postgresql.ENUM(
    "pending",
    "in_progress",
    "finished",
    name="bear_challenge_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    bear_challenge_format.create(bind, checkfirst=True)
    bear_challenge_status.create(bind, checkfirst=True)

    op.create_table(
        "bear_tiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_capped", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("slots", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.bulk_insert(
        sa.table(
            "bear_tiers",
            sa.column("id", sa.Integer()),
            sa.column("name", sa.Text()),
            sa.column("is_capped", sa.Boolean()),
            sa.column("slots", sa.Integer()),
        ),
        [
            {"id": 1, "name": "Царь Медведь", "is_capped": True, "slots": 1},
            {"id": 2, "name": "Короли Леса", "is_capped": True, "slots": 3},
            {"id": 3, "name": "Большие медведи", "is_capped": True, "slots": 5},
            {"id": 4, "name": "Средние медведи", "is_capped": False, "slots": None},
            {"id": 5, "name": "Медвежата", "is_capped": False, "slots": None},
        ],
    )

    op.add_column("users", sa.Column("is_bear", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("users", sa.Column("bear_tier_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("tier_placement", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_users_bear_tier_id_bear_tiers", "users", "bear_tiers", ["bear_tier_id"], ["id"])

    op.create_table(
        "bear_matches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("player1_id", sa.BigInteger(), nullable=False),
        sa.Column("player2_id", sa.BigInteger(), nullable=False),
        sa.Column("games", sa.Integer(), server_default="0", nullable=False),
        sa.Column("player1_wins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("player2_wins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_played", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["player1_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["player2_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player1_id", "player2_id", name="uq_bear_matches_players"),
    )

    op.create_table(
        "bear_challenges",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("player1_id", sa.BigInteger(), nullable=False),
        sa.Column("player2_id", sa.BigInteger(), nullable=False),
        sa.Column("format", bear_challenge_format, nullable=False),
        sa.Column("player1_wins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("player2_wins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("winner_id", sa.BigInteger(), nullable=True),
        sa.Column("played", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", bear_challenge_status, server_default="pending", nullable=False),
        sa.ForeignKeyConstraint(["player1_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["player2_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["winner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("bear_challenges")
    op.drop_table("bear_matches")
    op.drop_constraint("fk_users_bear_tier_id_bear_tiers", "users", type_="foreignkey")
    op.drop_column("users", "tier_placement")
    op.drop_column("users", "bear_tier_id")
    op.drop_column("users", "is_bear")
    op.drop_table("bear_tiers")

    bind = op.get_bind()
    bear_challenge_status.drop(bind, checkfirst=True)
    bear_challenge_format.drop(bind, checkfirst=True)
