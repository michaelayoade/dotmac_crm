"""add resolved_to_ticket conversation status

Revision ID: zo2f3a4b5c6d
Revises: zn1e2f3a4b5c
Create Date: 2026-06-05 12:45:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zo2f3a4b5c6d"
down_revision: str | None = "zn1e2f3a4b5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE conversationstatus ADD VALUE IF NOT EXISTS 'resolved_to_ticket' BEFORE 'resolved'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed without rebuilding the type.
    pass
