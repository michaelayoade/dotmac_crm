"""Add missing ERP material status to material requests.

Revision ID: mr2026061002
Revises: mr2026061001
Create Date: 2026-06-10 14:20:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "mr2026061002"
down_revision = "mr2026061001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("material_requests")}
    if "erp_material_status" not in existing_columns:
        op.add_column(
            "material_requests",
            sa.Column("erp_material_status", sa.String(length=40), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("material_requests")}
    if "erp_material_status" in existing_columns:
        op.drop_column("material_requests", "erp_material_status")
