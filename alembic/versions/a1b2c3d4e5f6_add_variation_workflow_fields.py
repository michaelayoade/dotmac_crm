"""Add variation workflow fields to as_built_routes.

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the variation type enum
    variation_type_enum = sa.Enum(
        "scope_change", "route_deviation", "material_change",
        "additional_work", "reduction",
        name="variationtype",
    )
    variation_type_enum.create(op.get_bind(), checkfirst=True)

    # Add new columns to as_built_routes
    op.add_column("as_built_routes", sa.Column("variation_type", variation_type_enum, nullable=True))
    op.add_column("as_built_routes", sa.Column("variation_reason", sa.Text(), nullable=True))
    op.add_column("as_built_routes", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("as_built_routes", sa.Column("work_order_ref", sa.String(120), nullable=True))
    op.add_column("as_built_routes", sa.Column("erp_sync_status", sa.String(40), nullable=True))
    op.add_column("as_built_routes", sa.Column("erp_reference", sa.String(120), nullable=True))
    op.add_column("as_built_routes", sa.Column("erp_sync_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("as_built_routes", "erp_sync_at")
    op.drop_column("as_built_routes", "erp_reference")
    op.drop_column("as_built_routes", "erp_sync_status")
    op.drop_column("as_built_routes", "work_order_ref")
    op.drop_column("as_built_routes", "version")
    op.drop_column("as_built_routes", "variation_reason")
    op.drop_column("as_built_routes", "variation_type")
    sa.Enum(name="variationtype").drop(op.get_bind(), checkfirst=True)
