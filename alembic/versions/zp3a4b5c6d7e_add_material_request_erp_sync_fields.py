"""Add ERP sync tracking fields to material requests.

Revision ID: zp3a4b5c6d7e
Revises: zo2f3a4b5c6d
Create Date: 2026-06-10 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zp3a4b5c6d7e"
down_revision = "zo2f3a4b5c6d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("material_requests")}

    sync_status = postgresql.ENUM(
        "pending",
        "synced",
        "failed",
        "retrying",
        "not_configured",
        name="materialrequesterpsyncstatus",
        create_type=False,
    )
    sync_status.create(bind, checkfirst=True)

    if "erp_sync_status" not in existing_columns:
        op.add_column(
            "material_requests",
            sa.Column("erp_sync_status", sync_status, nullable=True),
        )
    if "erp_sync_error" not in existing_columns:
        op.add_column(
            "material_requests",
            sa.Column("erp_sync_error", sa.String(length=500), nullable=True),
        )
    if "erp_synced_at" not in existing_columns:
        op.add_column(
            "material_requests",
            sa.Column("erp_synced_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "erp_sync_attempts" not in existing_columns:
        op.add_column(
            "material_requests",
            sa.Column("erp_sync_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    if "erp_material_status" not in existing_columns:
        op.add_column(
            "material_requests",
            sa.Column("erp_material_status", sa.String(length=40), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("material_requests")}

    if "erp_sync_attempts" in existing_columns:
        op.drop_column("material_requests", "erp_sync_attempts")
    if "erp_material_status" in existing_columns:
        op.drop_column("material_requests", "erp_material_status")
    if "erp_synced_at" in existing_columns:
        op.drop_column("material_requests", "erp_synced_at")
    if "erp_sync_error" in existing_columns:
        op.drop_column("material_requests", "erp_sync_error")
    if "erp_sync_status" in existing_columns:
        op.drop_column("material_requests", "erp_sync_status")

    sync_status = postgresql.ENUM(name="materialrequesterpsyncstatus")
    sync_status.drop(bind, checkfirst=True)
