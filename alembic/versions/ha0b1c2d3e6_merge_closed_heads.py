"""Merge heads created around nextcloud + sales owner backfills.

Revision ID: ha0b1c2d3e6
Revises: h8b9c0d1e2f3, ha0b1c2d3e5
Create Date: 2026-02-12
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "ha0b1c2d3e6"
down_revision: tuple[str, str] = ("h8b9c0d1e2f3", "ha0b1c2d3e5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
