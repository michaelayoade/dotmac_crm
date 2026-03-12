"""Merge variation workflow and as-built line items heads.

Revision ID: v2b3c4d5e6f7
Revises: v1a2b3c4d5e6, z2d3e4f5a6b7
Create Date: 2026-03-11
"""

from collections.abc import Sequence

revision: str = "v2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = ("v1a2b3c4d5e6", "z2d3e4f5a6b7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
