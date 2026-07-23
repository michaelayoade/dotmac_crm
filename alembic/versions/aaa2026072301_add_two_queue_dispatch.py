"""add durable two-queue CRM dispatch entries

Revision ID: aaa2026072301
Revises: zz3e4f5g6h7i
Create Date: 2026-07-23 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "aaa2026072301"
down_revision = "zz3e4f5g6h7i"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("crm_conversation_queue_entries"):
        op.create_table(
            "crm_conversation_queue_entries",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("conversation_id", sa.Uuid(), nullable=False),
            sa.Column("queue_type", sa.String(length=16), nullable=False),
            sa.Column("state", sa.String(length=16), nullable=False),
            sa.Column("original_arrival_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("current_agent_id", sa.Uuid(), nullable=True),
            sa.Column("previous_agent_id", sa.Uuid(), nullable=True),
            sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("classification_attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("notification_ledger", sa.JSON(), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["conversation_id"], ["crm_conversations.id"]),
            sa.ForeignKeyConstraint(["current_agent_id"], ["crm_agents.id"]),
            sa.ForeignKeyConstraint(["previous_agent_id"], ["crm_agents.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint("queue_type IN ('support', 'sales')", name="ck_crm_queue_entry_type"),
            sa.CheckConstraint(
                "state IN ('classifying', 'waiting', 'assigned', 'completed', 'cancelled')",
                name="ck_crm_queue_entry_state",
            ),
        )
        op.create_index(
            "uq_crm_conversation_one_live_queue_entry",
            "crm_conversation_queue_entries",
            ["conversation_id"],
            unique=True,
            postgresql_where=sa.text("state IN ('classifying', 'waiting', 'assigned')"),
            sqlite_where=sa.text("state IN ('classifying', 'waiting', 'assigned')"),
        )
        op.create_index(
            "ix_crm_queue_waiting_fifo",
            "crm_conversation_queue_entries",
            ["queue_type", "state", "original_arrival_at", "id"],
        )
        op.create_index(
            "ix_crm_queue_assigned_agent",
            "crm_conversation_queue_entries",
            ["current_agent_id", "state", "assigned_at"],
        )
    if not inspector.has_table("crm_conversation_queue_events"):
        op.create_table(
            "crm_conversation_queue_events",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("queue_entry_id", sa.Uuid(), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("actor_id", sa.Uuid(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["queue_entry_id"], ["crm_conversation_queue_entries.id"]),
            sa.ForeignKeyConstraint(["actor_id"], ["people.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_crm_queue_events_entry_created", "crm_conversation_queue_events", ["queue_entry_id", "created_at"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("crm_conversation_queue_events"):
        op.drop_table("crm_conversation_queue_events")
    if inspector.has_table("crm_conversation_queue_entries"):
        op.drop_table("crm_conversation_queue_entries")
