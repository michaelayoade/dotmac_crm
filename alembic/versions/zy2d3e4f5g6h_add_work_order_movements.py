"""add work order movements for en route and arrival tracking

Revision ID: zy2d3e4f5g6h
Revises: zx1c2d3e4f5g
Create Date: 2026-07-05 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zy2d3e4f5g6h"
down_revision = "zx1c2d3e4f5g"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE fieldjobevent ADD VALUE IF NOT EXISTS 'arrived' AFTER 'en_route'")

    inspector = sa.inspect(bind)
    if inspector.has_table("work_order_movements"):
        return

    uuid_type = postgresql.UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.String(36)
    op.create_table(
        "work_order_movements",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("work_order_id", uuid_type, nullable=False),
        sa.Column("actor_person_id", uuid_type, nullable=True),
        sa.Column("destination_type", sa.String(length=40), nullable=False),
        sa.Column("destination_id", sa.String(length=120), nullable=True),
        sa.Column("destination_label", sa.String(length=255), nullable=True),
        sa.Column("destination_latitude", sa.Float(), nullable=True),
        sa.Column("destination_longitude", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_latitude", sa.Float(), nullable=True),
        sa.Column("start_longitude", sa.Float(), nullable=True),
        sa.Column("arrival_latitude", sa.Float(), nullable=True),
        sa.Column("arrival_longitude", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("client_ref", uuid_type, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_work_order_movements_work_order_id", "work_order_movements", ["work_order_id"])
    op.create_index("ix_work_order_movements_client_ref", "work_order_movements", ["client_ref"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("work_order_movements"):
        op.drop_index("ix_work_order_movements_client_ref", table_name="work_order_movements")
        op.drop_index("ix_work_order_movements_work_order_id", table_name="work_order_movements")
        op.drop_table("work_order_movements")
    # PostgreSQL cannot DROP enum values without recreating the type.
