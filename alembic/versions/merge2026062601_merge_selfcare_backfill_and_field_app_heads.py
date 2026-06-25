"""Merge Selfcare backfill and field app migration heads.

Revision ID: merge2026062601
Revises: 20260624120000, merge2026062602
Create Date: 2026-06-26 00:01:00.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "merge2026062601"
down_revision: str | tuple[str, str] = ("20260624120000", "merge2026062602")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
