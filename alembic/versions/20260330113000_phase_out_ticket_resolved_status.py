"""phase out ticket resolved status

Revision ID: 20260330113000
Revises: za1b2c3d4e5f
Create Date: 2026-03-30 11:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260330113000"
down_revision: str | Sequence[str] | None = "za1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            """
            UPDATE tickets
            SET
                closed_at = COALESCE(closed_at, resolved_at, updated_at, created_at),
                resolved_at = COALESCE(resolved_at, closed_at, updated_at, created_at)
            WHERE status = 'resolved'
            """
        )
    )
    op.execute(sa.text("UPDATE tickets SET status = 'closed' WHERE status = 'resolved'"))

    op.execute(
        sa.text(
            """
            CREATE TYPE ticketstatus_new AS ENUM (
                'new',
                'open',
                'pending',
                'waiting_on_customer',
                'lastmile_rerun',
                'site_under_construction',
                'on_hold',
                'closed',
                'canceled',
                'merged'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE tickets
            ALTER COLUMN status
            TYPE ticketstatus_new
            USING status::text::ticketstatus_new
            """
        )
    )
    op.execute(sa.text("DROP TYPE ticketstatus"))
    op.execute(sa.text("ALTER TYPE ticketstatus_new RENAME TO ticketstatus"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            """
            CREATE TYPE ticketstatus_old AS ENUM (
                'new',
                'open',
                'pending',
                'waiting_on_customer',
                'lastmile_rerun',
                'site_under_construction',
                'on_hold',
                'resolved',
                'closed',
                'canceled',
                'merged'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE tickets
            ALTER COLUMN status
            TYPE ticketstatus_old
            USING status::text::ticketstatus_old
            """
        )
    )
    op.execute(sa.text("DROP TYPE ticketstatus"))
    op.execute(sa.text("ALTER TYPE ticketstatus_old RENAME TO ticketstatus"))
