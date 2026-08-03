"""rename ncl to nsl

Revision ID: 0007_rename_ncl_to_nsl
Revises: 0006_remove_ncl_seed
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0007_rename_ncl_to_nsl"
down_revision: str | None = "0006_remove_ncl_seed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("ncl_teams", "nsl_teams")
    op.rename_table("ncl_team_members", "nsl_team_members")
    op.rename_table("ncl_matches", "nsl_matches")
    op.execute("ALTER TABLE nsl_team_members RENAME CONSTRAINT uq_ncl_team_members_team_user TO uq_nsl_team_members_team_user")
    op.execute("ALTER TABLE nsl_team_members RENAME CONSTRAINT uq_ncl_team_members_user TO uq_nsl_team_members_user")
    op.execute("ALTER TABLE nsl_matches RENAME CONSTRAINT uq_ncl_matches_week_pair TO uq_nsl_matches_week_pair")


def downgrade() -> None:
    op.execute("ALTER TABLE nsl_matches RENAME CONSTRAINT uq_nsl_matches_week_pair TO uq_ncl_matches_week_pair")
    op.execute("ALTER TABLE nsl_team_members RENAME CONSTRAINT uq_nsl_team_members_user TO uq_ncl_team_members_user")
    op.execute("ALTER TABLE nsl_team_members RENAME CONSTRAINT uq_nsl_team_members_team_user TO uq_ncl_team_members_team_user")
    op.rename_table("nsl_matches", "ncl_matches")
    op.rename_table("nsl_team_members", "ncl_team_members")
    op.rename_table("nsl_teams", "ncl_teams")
