"""Backfill owner_agent_id for closed leads using last agent-authored message.

Revision ID: h9c0d1e2f3a4
Revises: h7a8b9c0d1e2
Create Date: 2026-02-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h9c0d1e2f3a4"
down_revision: str | None = "h7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # For any closed lead with no owner, infer owner from the last message authored by a CRM agent
    # in any conversation for that person.
    op.execute(
        sa.text(
            "WITH latest_agent_message AS (\n"
            "  SELECT DISTINCT ON (c.person_id)\n"
            "    c.person_id,\n"
            "    a.id AS agent_id\n"
            "  FROM crm_conversations c\n"
            "  JOIN crm_messages m ON m.conversation_id = c.id\n"
            "  JOIN crm_agents a ON a.person_id = m.author_id\n"
            "  WHERE c.is_active = true\n"
            "    AND m.author_id IS NOT NULL\n"
            "  ORDER BY c.person_id, m.created_at DESC\n"
            ")\n"
            "UPDATE crm_leads l\n"
            "SET owner_agent_id = lam.agent_id\n"
            "FROM latest_agent_message lam\n"
            "WHERE l.is_active = true\n"
            "  AND l.owner_agent_id IS NULL\n"
            "  AND l.status IN ('won','lost')\n"
            "  AND l.person_id = lam.person_id"
        )
    )


def downgrade() -> None:
    # Data backfill cannot be safely reversed.
    pass
