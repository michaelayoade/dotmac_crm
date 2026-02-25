"""enforce unique active project links for quote_id and sales_order_id

Revision ID: p2b3c4d5e6f8
Revises: n2b3c4d5e6f7, m2a3b4c5d6e8
Create Date: 2026-02-24 10:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "p2b3c4d5e6f8"
down_revision = ("n2b3c4d5e6f7", "m2a3b4c5d6e8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Keep only the newest active project per quote link.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY metadata->>'quote_id'
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM projects
                WHERE is_active = true
                  AND COALESCE(metadata->>'quote_id', '') <> ''
            )
            UPDATE projects p
            SET is_active = false,
                updated_at = now()
            FROM ranked r
            WHERE p.id = r.id
              AND r.rn > 1
            """
        )
    )

    # Keep only the newest active project per sales-order link.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY metadata->>'sales_order_id'
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM projects
                WHERE is_active = true
                  AND COALESCE(metadata->>'sales_order_id', '') <> ''
            )
            UPDATE projects p
            SET is_active = false,
                updated_at = now()
            FROM ranked r
            WHERE p.id = r.id
              AND r.rn > 1
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_active_quote_id
            ON projects ((metadata->>'quote_id'))
            WHERE is_active = true
              AND COALESCE(metadata->>'quote_id', '') <> ''
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_active_sales_order_id
            ON projects ((metadata->>'sales_order_id'))
            WHERE is_active = true
              AND COALESCE(metadata->>'sales_order_id', '') <> ''
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(sa.text("DROP INDEX IF EXISTS uq_projects_active_sales_order_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_projects_active_quote_id"))

