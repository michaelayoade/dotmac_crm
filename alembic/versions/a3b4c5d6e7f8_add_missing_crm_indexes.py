"""Add missing CRM inbox indexes for performance.

Adds indexes that were missing from the original CRM indexes migration:
- idx_crm_conversations_person_status: for correlated subquery in exclude_superseded_resolved
- idx_crm_assignments_agent: for "assigned to me" filtering
- idx_crm_assignments_active: for "unassigned" filtering

Revision ID: a3b4c5d6e7f8
Revises: b2d3e4f5a6c7
Create Date: 2026-02-02 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "b2d3e4f5a6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
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
