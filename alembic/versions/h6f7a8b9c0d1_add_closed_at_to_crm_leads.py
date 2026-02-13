"""Add closed_at to crm_leads and backfill for closed statuses.

Revision ID: h6f7a8b9c0d1
Revises: h5e6f7a8b9c0
Create Date: 2026-02-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h6f7a8b9c0d1"
down_revision: str | None = "h5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("crm_leads", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text(
            "UPDATE crm_leads "
            "SET closed_at = updated_at "
            "WHERE closed_at IS NULL "
            "AND status IN ('won','lost')"
        )
    )


def downgrade() -> None:
    op.drop_column("crm_leads", "closed_at")
