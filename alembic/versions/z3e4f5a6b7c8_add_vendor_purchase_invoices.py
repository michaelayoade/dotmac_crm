"""add vendor purchase invoices

Revision ID: z3e4f5a6b7c8
Revises: z2d3e4f5a6b7
Create Date: 2026-03-13 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "z3e4f5a6b7c8"
down_revision = "z2d3e4f5a6b7"
branch_labels = None
depends_on = None


vendorpurchaseinvoicestatus = postgresql.ENUM(
    "draft",
    "submitted",
    "under_review",
    "approved",
    "rejected",
    "revision_requested",
    name="vendorpurchaseinvoicestatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    vendorpurchaseinvoicestatus.create(bind, checkfirst=True)

    op.create_table(
        "vendor_purchase_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", vendorpurchaseinvoicestatus, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("subtotal", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("tax_total", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("total", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("created_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attachment_storage_key", sa.String(length=255), nullable=True),
        sa.Column("attachment_file_name", sa.String(length=255), nullable=True),
        sa.Column("attachment_mime_type", sa.String(length=120), nullable=True),
        sa.Column("attachment_file_size", sa.Integer(), nullable=True),
        sa.Column("erp_purchase_order_id", sa.String(length=100), nullable=True),
        sa.Column("erp_purchase_invoice_id", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "vendor_id", name="uq_vendor_purchase_invoice_project_vendor"),
    )
    op.create_index(
        op.f("ix_vendor_purchase_invoices_erp_purchase_invoice_id"),
        "vendor_purchase_invoices",
        ["erp_purchase_invoice_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vendor_purchase_invoices_erp_purchase_order_id"),
        "vendor_purchase_invoices",
        ["erp_purchase_order_id"],
        unique=False,
    )

    op.create_table(
        "vendor_purchase_invoice_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_type", sa.String(length=80), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["invoice_id"], ["vendor_purchase_invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("vendor_purchase_invoice_line_items")
    op.drop_index(op.f("ix_vendor_purchase_invoices_erp_purchase_order_id"), table_name="vendor_purchase_invoices")
    op.drop_index(op.f("ix_vendor_purchase_invoices_erp_purchase_invoice_id"), table_name="vendor_purchase_invoices")
    op.drop_table("vendor_purchase_invoices")
    vendorpurchaseinvoicestatus.drop(op.get_bind(), checkfirst=True)
