"""Add index on crm_leads.closed_at.

Revision ID: h7a8b9c0d1e2
Revises: h6f7a8b9c0d1
Create Date: 2026-02-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h7a8b9c0d1e2"
down_revision: str | None = "h6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_crm_leads_closed_at", "crm_leads", ["closed_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_crm_leads_closed_at", table_name="crm_leads")
