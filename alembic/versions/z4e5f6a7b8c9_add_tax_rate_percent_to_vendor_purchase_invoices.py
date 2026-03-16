"""Add tax rate percent to vendor purchase invoices."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "z4e5f6a7b8c9"
down_revision = "z3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vendor_purchase_invoices",
        sa.Column("tax_rate_percent", sa.Numeric(5, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vendor_purchase_invoices", "tax_rate_percent")
