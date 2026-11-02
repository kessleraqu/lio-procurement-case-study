"""catalog alternatives on requests

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02

Adds ``procurement_requests.catalog_alternatives``: a snapshot of the
negotiated catalog articles that were suggested when the request was created,
so the approver sees them (prices as they were at the time).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "procurement_requests", sa.Column("catalog_alternatives", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("procurement_requests", "catalog_alternatives")
