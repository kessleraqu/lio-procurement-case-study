"""organization-specific commodity groups

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-02

Adds ``commodity_groups.organization_id``: NULL for the shared taxonomy, set
for groups only one organization uses (e.g. Acme's "Office Supplies").
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "commodity_groups",
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_commodity_groups_organization_id", "commodity_groups", ["organization_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_commodity_groups_organization_id", table_name="commodity_groups")
    op.drop_column("commodity_groups", "organization_id")
