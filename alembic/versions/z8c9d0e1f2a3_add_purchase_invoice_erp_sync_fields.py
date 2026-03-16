"""Add ERP sync fields to vendor purchase invoices."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "z8c9d0e1f2a3"
down_revision = "z7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vendor_purchase_invoices",
        sa.Column("erp_sync_error", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "vendor_purchase_invoices",
        sa.Column("erp_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vendor_purchase_invoices", "erp_synced_at")
    op.drop_column("vendor_purchase_invoices", "erp_sync_error")
