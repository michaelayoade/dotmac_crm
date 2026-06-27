"""add chat queue + capacity fields

- crm_agents.max_concurrent_chats
- crm_conversations.queued_at, first_assigned_at (+ indexes)
- backfill first_assigned_at from earliest agent assignment

Revision ID: qa2b3c4d5e6f
Revises: wa1b2c3d4e5f
Create Date: 2026-06-27 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "qa2b3c4d5e6f"
down_revision = "wa1b2c3d4e5f"
branch_labels = None
depends_on = None


def _columns(inspector, table):
    return {col["name"] for col in inspector.get_columns(table)}


def _indexes(inspector, table):
    return {ix["name"] for ix in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "max_concurrent_chats" not in _columns(inspector, "crm_agents"):
        op.add_column("crm_agents", sa.Column("max_concurrent_chats", sa.Integer(), nullable=True))

    conv_cols = _columns(inspector, "crm_conversations")
    if "queued_at" not in conv_cols:
        op.add_column("crm_conversations", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
    if "first_assigned_at" not in conv_cols:
        op.add_column("crm_conversations", sa.Column("first_assigned_at", sa.DateTime(timezone=True), nullable=True))

    conv_idx = _indexes(inspector, "crm_conversations")
    if "ix_crm_conversations_queued_at" not in conv_idx:
        op.create_index("ix_crm_conversations_queued_at", "crm_conversations", ["queued_at"])
    if "ix_crm_conversations_first_assigned_at" not in conv_idx:
        op.create_index("ix_crm_conversations_first_assigned_at", "crm_conversations", ["first_assigned_at"])

    # Backfill first_assigned_at from the earliest agent (non-team-only) assignment.
    op.execute(
        """
        UPDATE crm_conversations c
        SET first_assigned_at = sub.first_at
        FROM (
            SELECT conversation_id, MIN(assigned_at) AS first_at
            FROM crm_conversation_assignments
            WHERE agent_id IS NOT NULL AND assigned_at IS NOT NULL
            GROUP BY conversation_id
        ) sub
        WHERE c.id = sub.conversation_id AND c.first_assigned_at IS NULL
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    conv_idx = _indexes(inspector, "crm_conversations")
    if "ix_crm_conversations_first_assigned_at" in conv_idx:
        op.drop_index("ix_crm_conversations_first_assigned_at", table_name="crm_conversations")
    if "ix_crm_conversations_queued_at" in conv_idx:
        op.drop_index("ix_crm_conversations_queued_at", table_name="crm_conversations")

    conv_cols = _columns(inspector, "crm_conversations")
    if "first_assigned_at" in conv_cols:
        op.drop_column("crm_conversations", "first_assigned_at")
    if "queued_at" in conv_cols:
        op.drop_column("crm_conversations", "queued_at")

    if "max_concurrent_chats" in _columns(inspector, "crm_agents"):
        op.drop_column("crm_agents", "max_concurrent_chats")
