"""add crm inbox indexes

Revision ID: 9f8e7d6c5b4a
Revises: 7c9d0e1f2a3b, b1c2d3e4f5a6, 2a4ea51a31ac, e7c1b2a3d4f5, c4d5e6f7a8b9
Create Date: 2026-02-02 12:30:00.000000

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "9f8e7d6c5b4a"
down_revision: str | Sequence[str] | None = (
    "7c9d0e1f2a3b",
    "b1c2d3e4f5a6",
    "2a4ea51a31ac",
    "e7c1b2a3d4f5",
    "c4d5e6f7a8b9",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_crm_messages_conv_last_ts
            ON crm_messages (conversation_id, (COALESCE(received_at, sent_at, created_at)) DESC);
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_crm_messages_channel_target
            ON crm_messages (channel_type, channel_target_id, conversation_id);
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_crm_messages_unread
            ON crm_messages (conversation_id)
            WHERE direction = 'inbound' AND status = 'received' AND read_at IS NULL;
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_crm_conversations_last_msg
            ON crm_conversations (last_message_at DESC, updated_at DESC)
            WHERE is_active IS TRUE;
            """
        )
        # Index for exclude_superseded_resolved correlated subquery
        # Checks: person_id, status IN (open, pending), is_active, updated_at comparison
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_crm_conversations_person_status
            ON crm_conversations (person_id, status, is_active, updated_at DESC);
            """
        )
        # Index for conversation assignments - agent filter
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_crm_assignments_agent
            ON crm_conversation_assignments (agent_id, conversation_id)
            WHERE is_active IS TRUE;
            """
        )
        # Index for conversation assignments - unassigned filter
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_crm_assignments_active
            ON crm_conversation_assignments (conversation_id)
            WHERE is_active IS TRUE;
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_crm_assignments_active;"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_crm_assignments_agent;"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_crm_conversations_person_status;"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_crm_conversations_last_msg;"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_crm_messages_unread;"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_crm_messages_channel_target;"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_crm_messages_conv_last_ts;"
        )
