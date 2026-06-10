"""link ont assignments to subscribers and work orders

Revision ID: zu8f9a0b1c2d
Revises: zt7e8f9a0b1c
Create Date: 2026-06-10 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zu8f9a0b1c2d"
down_revision = "zt7e8f9a0b1c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("ont_assignments")}

    if "subscriber_id" not in columns:
        op.add_column("ont_assignments", sa.Column("subscriber_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            "fk_ont_assignments_subscriber_id",
            "ont_assignments",
            "subscribers",
            ["subscriber_id"],
            ["id"],
        )
        op.create_index("ix_ont_assignments_subscriber_id", "ont_assignments", ["subscriber_id"])
    if "work_order_id" not in columns:
        op.add_column("ont_assignments", sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            "fk_ont_assignments_work_order_id",
            "ont_assignments",
            "work_orders",
            ["work_order_id"],
            ["id"],
        )

    # Field installs record the physical unit before the NOC knows the PON
    # port, so the column becomes nullable.
    op.alter_column("ont_assignments", "pon_port_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    op.alter_column("ont_assignments", "pon_port_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_index("ix_ont_assignments_subscriber_id", table_name="ont_assignments")
    op.drop_constraint("fk_ont_assignments_work_order_id", "ont_assignments", type_="foreignkey")
    op.drop_constraint("fk_ont_assignments_subscriber_id", "ont_assignments", type_="foreignkey")
    op.drop_column("ont_assignments", "work_order_id")
    op.drop_column("ont_assignments", "subscriber_id")
