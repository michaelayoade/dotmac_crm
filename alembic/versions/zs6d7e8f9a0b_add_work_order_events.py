"""add work order events for field transitions

Revision ID: zs6d7e8f9a0b
Revises: zr5c6d7e8f9a
Create Date: 2026-06-10 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zs6d7e8f9a0b"
down_revision = "zr5c6d7e8f9a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("work_order_events"):
        return

    field_job_event = postgresql.ENUM(
        "accept",
        "en_route",
        "start",
        "hold",
        "resume",
        "complete",
        name="fieldjobevent",
        create_type=False,
    )
    field_job_event.create(bind, checkfirst=True)

    op.create_table(
        "work_order_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event", field_job_event, nullable=False),
        sa.Column("actor_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
        sa.ForeignKeyConstraint(["actor_person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_event_id", name="uq_work_order_events_client_event_id"),
    )
    op.create_index("ix_work_order_events_work_order", "work_order_events", ["work_order_id"])
    op.create_index("ix_work_order_events_client_event_id", "work_order_events", ["client_event_id"])


def downgrade() -> None:
    op.drop_index("ix_work_order_events_client_event_id", table_name="work_order_events")
    op.drop_index("ix_work_order_events_work_order", table_name="work_order_events")
    op.drop_table("work_order_events")
    sa.Enum(name="fieldjobevent").drop(op.get_bind(), checkfirst=True)
