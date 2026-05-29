"""add missing setting domains

Revision ID: zm0e1f2a3b4c
Revises: zl0d1e2f3a4c
Create Date: 2026-05-26 09:20:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision = "zm0e1f2a3b4c"
down_revision = "zl0d1e2f3a4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE settingdomain ADD VALUE IF NOT EXISTS 'integration'")
        op.execute("ALTER TYPE settingdomain ADD VALUE IF NOT EXISTS 'campaigns'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely without recreating the type.
    pass
