"""Add issued status to material request enum.

Revision ID: h3c4d5e6f7a8
Revises: h2b3c4d5e6f7
Create Date: 2026-02-11
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision = "h3c4d5e6f7a8"
down_revision = "h2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE materialrequeststatus ADD VALUE IF NOT EXISTS 'issued'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values safely in-place.
    pass
