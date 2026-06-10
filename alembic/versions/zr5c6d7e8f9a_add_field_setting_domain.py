"""add field setting domain

Revision ID: zr5c6d7e8f9a
Revises: zq4b5c6d7e8f
Create Date: 2026-06-10 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision = "zr5c6d7e8f9a"
down_revision = "zq4b5c6d7e8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE settingdomain ADD VALUE IF NOT EXISTS 'field'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely without recreating the type.
    pass
