"""add tax_rate to crm_quotes

Revision ID: qt2026062800
Revises: ct2026062800
Create Date: 2026-06-28

Stores the applied tax rate percent on a quote so tax_total auto-derives from the
subtotal on every recalculation (instead of being a frozen manual amount).
"""

import sqlalchemy as sa
from alembic import op

revision = "qt2026062800"
down_revision = "ct2026062800"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("crm_quotes", sa.Column("tax_rate", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("crm_quotes", "tax_rate")
