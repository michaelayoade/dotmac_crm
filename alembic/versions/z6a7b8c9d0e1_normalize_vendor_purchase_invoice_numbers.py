"""Normalize vendor purchase invoice numbers to INV sequence."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "z6a7b8c9d0e1"
down_revision = "z5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id
            FROM vendor_purchase_invoices
            ORDER BY created_at ASC, id ASC
            """
        )
    ).fetchall()

    for index, row in enumerate(rows, start=1):
        bind.execute(
            sa.text(
                """
                UPDATE vendor_purchase_invoices
                SET invoice_number = :invoice_number
                WHERE id = :invoice_id
                """
            ),
            {"invoice_number": f"INV-{index:04d}", "invoice_id": row.id},
        )


def downgrade() -> None:
    # Historical vendor-name-based invoice numbers cannot be reconstructed reliably.
    pass
