"""Backfill owner_agent_id for closed leads using latest conversation assignment.

Revision ID: ha0b1c2d3e5
Revises: h9c0d1e2f3a4
Create Date: 2026-02-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ha0b1c2d3e5"
down_revision: str | None = "h9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "WITH latest_assignment AS (\n"
            "  SELECT DISTINCT ON (c.person_id)\n"
            "    c.person_id,\n"
            "    ca.agent_id\n"
            "  FROM crm_conversations c\n"
            "  JOIN crm_conversation_assignments ca ON ca.conversation_id = c.id\n"
            "  WHERE c.is_active = true\n"
            "    AND ca.is_active = true\n"
            "    AND ca.agent_id IS NOT NULL\n"
            "  ORDER BY c.person_id, ca.assigned_at DESC NULLS LAST, ca.created_at DESC\n"
            ")\n"
            "UPDATE crm_leads l\n"
            "SET owner_agent_id = la.agent_id\n"
            "FROM latest_assignment la\n"
            "WHERE l.is_active = true\n"
            "  AND l.owner_agent_id IS NULL\n"
            "  AND l.status IN ('won','lost')\n"
            "  AND l.person_id = la.person_id"
        )
    )


def downgrade() -> None:
    # Data backfill cannot be safely reversed.
    pass
