"""persist runtime state for recoverable Discord workflows

Revision ID: 0008_runtime_states
Revises: 0007_rename_ncl_to_nsl
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0008_runtime_states"
down_revision: str | None = "0007_rename_ncl_to_nsl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_states",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("runtime_states")
