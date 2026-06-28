"""enforce one open lead per (person, pipeline)

Adds a partial unique index so the "one open lead per person" rule is guaranteed
at the database level (not just in the application), closing the concurrent-create
race. Scope is per-(person, pipeline); a null pipeline is COALESCEd to a sentinel
UUID so multiple null-pipeline open leads for the same person still collide
(Postgres treats NULLs as distinct in unique indexes).

Revision ID: ld2026062900
Revises: ms2026062800
Create Date: 2026-06-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ld2026062900"
down_revision = "ms2026062800"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "uq_crm_leads_one_open_per_person_pipeline"
_SENTINEL = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Index uses Postgres-specific COALESCE-to-uuid casting; other backends
        # (e.g. the sqlite test harness) rely on the application-level guard.
        return

    # Collapse pre-existing violations so the unique index can be built: keep the
    # most recent open lead per (person, COALESCEd pipeline) active, soft-delete
    # the older duplicates.
    op.execute(
        sa.text(
            f"""
            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY person_id, COALESCE(pipeline_id, '{_SENTINEL}'::uuid)
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM crm_leads
                WHERE is_active
                  AND status NOT IN ('won', 'lost')
            )
            UPDATE crm_leads lead
            SET is_active = false,
                updated_at = now()
            FROM ranked
            WHERE lead.id = ranked.id
              AND ranked.rn > 1
            """
        )
    )

    # Note: created without CONCURRENTLY because alembic runs migrations inside a
    # transaction (see alembic/env.py); CONCURRENTLY cannot run in a transaction.
    op.execute(
        sa.text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME}
            ON crm_leads (person_id, COALESCE(pipeline_id, '{_SENTINEL}'::uuid))
            WHERE is_active AND status NOT IN ('won', 'lost')
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
