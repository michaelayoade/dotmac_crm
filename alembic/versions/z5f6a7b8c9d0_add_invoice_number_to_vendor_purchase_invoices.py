"""Add invoice number to vendor purchase invoices."""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "z5f6a7b8c9d0"
down_revision = "z4e5f6a7b8c9"
branch_labels = None
depends_on = None


def _prefix_from_vendor_name(value: str | None) -> str:
    raw_name = (value or "").strip().upper()
    cleaned = re.sub(r"[^A-Z0-9]+", "", raw_name)
    prefix = cleaned[:12]
    return prefix or "VENDOR"


def upgrade() -> None:
    op.add_column(
        "vendor_purchase_invoices",
        sa.Column("invoice_number", sa.String(length=80), nullable=True),
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT vpi.id, vpi.vendor_id, v.name AS vendor_name
            FROM vendor_purchase_invoices vpi
            JOIN vendors v ON v.id = vpi.vendor_id
            ORDER BY vpi.created_at ASC, vpi.id ASC
            """
        )
    ).fetchall()

    counters: dict[str, int] = {}
    for row in rows:
        prefix = _prefix_from_vendor_name(row.vendor_name)
        counters[prefix] = counters.get(prefix, 0) + 1
        invoice_number = f"{prefix}-INV-{counters[prefix]:04d}"
        bind.execute(
            sa.text(
                """
                UPDATE vendor_purchase_invoices
                SET invoice_number = :invoice_number
                WHERE id = :invoice_id
                """
            ),
            {"invoice_number": invoice_number, "invoice_id": row.id},
        )

    op.alter_column("vendor_purchase_invoices", "invoice_number", nullable=False)
    op.create_index(
        op.f("ix_vendor_purchase_invoices_invoice_number"),
        "vendor_purchase_invoices",
        ["invoice_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_vendor_purchase_invoices_invoice_number"), table_name="vendor_purchase_invoices")
    op.drop_column("vendor_purchase_invoices", "invoice_number")
