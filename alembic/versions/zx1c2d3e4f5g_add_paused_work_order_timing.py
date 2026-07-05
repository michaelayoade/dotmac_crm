"""add paused work order timing fields

Revision ID: zx1c2d3e4f5g
Revises: zw0b1c2d3e4f
Create Date: 2026-07-05 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "zx1c2d3e4f5g"
down_revision = "zw0b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE workorderstatus ADD VALUE IF NOT EXISTS 'paused' AFTER 'in_progress'")
            op.execute("ALTER TYPE fieldjobevent ADD VALUE IF NOT EXISTS 'pause' AFTER 'start'")

    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("work_orders")}
    if "paused_at" not in columns:
        op.add_column("work_orders", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
    if "resumed_at" not in columns:
        op.add_column("work_orders", sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True))
    if "total_active_seconds" not in columns:
        op.add_column("work_orders", sa.Column("total_active_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("work_orders")}
    with op.batch_alter_table("work_orders") as batch_op:
        if "total_active_seconds" in columns:
            batch_op.drop_column("total_active_seconds")
        if "resumed_at" in columns:
            batch_op.drop_column("resumed_at")
        if "paused_at" in columns:
            batch_op.drop_column("paused_at")
    # PostgreSQL cannot DROP enum values without recreating the type.
