"""clan metadata seed

Revision ID: 0003_clan_metadata_seed
Revises: 0002_bear_ladder
Create Date: 2026-06-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0003_clan_metadata_seed"
down_revision: str | None = "0002_bear_ladder"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CLANS = [
    ("Stag", False, False, 1),
    ("Goat", False, False, 2),
    ("Raven", False, False, 3),
    ("Wolf", True, False, 4),
    ("Bear", False, False, 5),
    ("Boar", False, False, 6),
    ("Snake", False, False, 7),
    ("Dragon", False, False, 8),
    ("Horse", False, False, 9),
    ("Kraken", False, False, 10),
    ("Ox", False, False, 11),
    ("Lynx", True, False, 12),
    ("Squirrel", False, False, 13),
    ("Rat", False, False, 14),
    ("Eagle", True, False, 15),
    ("Lion", False, True, 16),
    ("Stoat", False, True, 17),
    ("Owl", False, False, 18),
    ("Hound", True, False, 19),
    ("Turtle", False, False, 20),
    ("Hippo", False, True, 21),
]


def upgrade() -> None:
    op.add_column("clans", sa.Column("is_clear", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("clans", sa.Column("is_kingdom", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("clans", sa.Column("is_enabled", sa.Boolean(), server_default="true", nullable=False))
    op.add_column("clans", sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False))

    for name, is_clear, is_kingdom, sort_order in CLANS:
        op.execute(
            sa.text(
                """
                INSERT INTO clans (name, is_clear, is_kingdom, is_enabled, sort_order)
                VALUES (:name, :is_clear, :is_kingdom, true, :sort_order)
                ON CONFLICT (name) DO UPDATE SET
                    is_clear = EXCLUDED.is_clear,
                    is_kingdom = EXCLUDED.is_kingdom,
                    is_enabled = true,
                    sort_order = EXCLUDED.sort_order
                """
            ).bindparams(
                name=name,
                is_clear=is_clear,
                is_kingdom=is_kingdom,
                sort_order=sort_order,
            )
        )


def downgrade() -> None:
    op.drop_column("clans", "sort_order")
    op.drop_column("clans", "is_enabled")
    op.drop_column("clans", "is_kingdom")
    op.drop_column("clans", "is_clear")
