"""Add serial numbers to material request items.

Revision ID: zr5c6d7e8f9a
Revises: zq4b5c6d7e8f
Create Date: 2026-06-10 15:05:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "zr5c6d7e8f9a"
down_revision = "zq4b5c6d7e8f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("material_request_items")}
    if "serial_numbers" not in existing_columns:
        op.add_column("material_request_items", sa.Column("serial_numbers", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("material_request_items")}
    if "serial_numbers" in existing_columns:
        op.drop_column("material_request_items", "serial_numbers")
