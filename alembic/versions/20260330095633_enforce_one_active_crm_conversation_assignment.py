"""enforce one active crm conversation assignment

Revision ID: 20260330095633
Revises: t5b6c7d8e9f0, v1a2b3c4d5e6, za1b2c3d4e5f
Create Date: 2026-03-30 09:56:33.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260330095633"
down_revision = ("t5b6c7d8e9f0", "v1a2b3c4d5e6", "za1b2c3d4e5f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY conversation_id
                        ORDER BY assigned_at DESC NULLS LAST, created_at DESC, id DESC
                    ) AS rn
                FROM crm_conversation_assignments
                WHERE is_active = true
            )
            UPDATE crm_conversation_assignments assignment
            SET is_active = false,
                updated_at = now()
            FROM ranked
            WHERE assignment.id = ranked.id
              AND ranked.rn > 1
            """
        )
    )

    op.create_index(
        "uq_crm_conversation_one_active_assignment",
        "crm_conversation_assignments",
        ["conversation_id"],
        unique=True,
        if_not_exists=True,
        postgresql_where=sa.text("is_active IS TRUE"),
        sqlite_where=sa.text("is_active IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_crm_conversation_one_active_assignment",
        table_name="crm_conversation_assignments",
        if_exists=True,
    )
