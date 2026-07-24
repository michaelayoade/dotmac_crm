"""harden durable two-queue dispatch state

Revision ID: ab2026072401
Revises: aaa2026072301
Create Date: 2026-07-24 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "ab2026072401"
down_revision = "aaa2026072301"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("crm_conversation_queue_dispatch_states"):
        op.create_table(
            "crm_conversation_queue_dispatch_states",
            sa.Column("queue_type", sa.String(length=16), primary_key=True, nullable=False),
            sa.Column("round_robin_cursor_agent_id", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["round_robin_cursor_agent_id"], ["crm_agents.id"]),
            sa.CheckConstraint("queue_type IN ('support', 'sales')", name="ck_crm_queue_dispatch_state_type"),
        )
        op.execute(
            "INSERT INTO crm_conversation_queue_dispatch_states "
            "(queue_type, created_at, updated_at) VALUES "
            "('support', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
            "('sales', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    columns = {column["name"] for column in inspector.get_columns("crm_conversation_queue_entries")}
    if "position_tracking" not in columns:
        op.add_column("crm_conversation_queue_entries", sa.Column("position_tracking", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("crm_conversation_queue_entries"):
        columns = {column["name"] for column in inspector.get_columns("crm_conversation_queue_entries")}
        if "position_tracking" in columns:
            op.drop_column("crm_conversation_queue_entries", "position_tracking")
    if inspector.has_table("crm_conversation_queue_dispatch_states"):
        op.drop_table("crm_conversation_queue_dispatch_states")
