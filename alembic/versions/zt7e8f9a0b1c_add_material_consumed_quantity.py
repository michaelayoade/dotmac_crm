"""add consumed_quantity to work order materials

Revision ID: zt7e8f9a0b1c
Revises: zs6d7e8f9a0b
Create Date: 2026-06-10 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "zt7e8f9a0b1c"
down_revision = "zs6d7e8f9a0b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("work_order_materials")}
    if "consumed_quantity" in columns:
        return
    op.add_column(
        "work_order_materials",
        sa.Column("consumed_quantity", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("work_order_materials", "consumed_quantity")
