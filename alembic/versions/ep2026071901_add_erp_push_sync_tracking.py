"""Add ERP push sync tracking to work orders and vendor purchase invoices.

Audit item D1: crm->erp money pushes (purchase orders anchored on work
orders, vendor purchase invoices) get a persisted sweepable sync marker so a
post-write push failure lands in a reconciler-swept recovery state instead of
a terminal log line. Mirrors the material-request marker pattern; the
work-order columns also record which quote the PO push was enqueued with so
the re-drive sweep can re-enqueue the task with the same args.

Revision ID: ep2026071901
Revises: zz3e4f5g6h7i
Create Date: 2026-07-19 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ep2026071901"
down_revision = "zz3e4f5g6h7i"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    wo_columns = {column["name"] for column in inspector.get_columns("work_orders")}
    if "erp_sync_status" not in wo_columns:
        op.add_column("work_orders", sa.Column("erp_sync_status", sa.String(length=40), nullable=True))
        op.create_index("ix_work_orders_erp_sync_status", "work_orders", ["erp_sync_status"])
    if "erp_sync_error" not in wo_columns:
        op.add_column("work_orders", sa.Column("erp_sync_error", sa.String(length=500), nullable=True))
    if "erp_synced_at" not in wo_columns:
        op.add_column("work_orders", sa.Column("erp_synced_at", sa.DateTime(timezone=True), nullable=True))
    if "erp_po_quote_id" not in wo_columns:
        op.add_column("work_orders", sa.Column("erp_po_quote_id", postgresql.UUID(as_uuid=True), nullable=True))

    pinv_columns = {column["name"] for column in inspector.get_columns("vendor_purchase_invoices")}
    if "erp_sync_status" not in pinv_columns:
        op.add_column("vendor_purchase_invoices", sa.Column("erp_sync_status", sa.String(length=40), nullable=True))
        op.create_index("ix_vendor_purchase_invoices_erp_sync_status", "vendor_purchase_invoices", ["erp_sync_status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    pinv_columns = {column["name"] for column in inspector.get_columns("vendor_purchase_invoices")}
    if "erp_sync_status" in pinv_columns:
        op.drop_index("ix_vendor_purchase_invoices_erp_sync_status", table_name="vendor_purchase_invoices")
        op.drop_column("vendor_purchase_invoices", "erp_sync_status")

    wo_columns = {column["name"] for column in inspector.get_columns("work_orders")}
    if "erp_po_quote_id" in wo_columns:
        op.drop_column("work_orders", "erp_po_quote_id")
    if "erp_synced_at" in wo_columns:
        op.drop_column("work_orders", "erp_synced_at")
    if "erp_sync_error" in wo_columns:
        op.drop_column("work_orders", "erp_sync_error")
    if "erp_sync_status" in wo_columns:
        op.drop_index("ix_work_orders_erp_sync_status", table_name="work_orders")
        op.drop_column("work_orders", "erp_sync_status")
