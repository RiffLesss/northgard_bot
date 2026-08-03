"""remove ncl seed

Revision ID: 0006_remove_ncl_seed
Revises: 0005_ncl_schedule
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0006_remove_ncl_seed"
down_revision: str | None = "0005_ncl_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_ncl_teams_seed", "ncl_teams", type_="unique")
    op.drop_column("ncl_teams", "seed")


def downgrade() -> None:
    op.add_column("ncl_teams", sa.Column("seed", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_ncl_teams_seed", "ncl_teams", ["seed"])
