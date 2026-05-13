"""merge conversation summaries and subscriber notifications

Revision ID: 20260508133000
Revises: 20260414102000, 20260427093000
Create Date: 2026-05-08 13:30:00.000000
"""

from collections.abc import Sequence

revision: str = "20260508133000"
down_revision: str | Sequence[str] | None = ("20260414102000", "20260427093000")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
