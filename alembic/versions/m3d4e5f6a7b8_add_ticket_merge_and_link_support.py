"""compat shim for removed ticket merge/link migration

Revision ID: m3d4e5f6a7b8
Revises: x9b0c1d2e3f4
Create Date: 2026-03-12 08:20:00.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "m3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "x9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preserve compatibility for databases stamped with the removed revision.

    The original ticket merge/link migration was removed when that feature set
    was stripped from the branch. Some databases were already advanced to the
    deleted revision, which caused Alembic startup to fail because the revision
    could no longer be resolved. This shim intentionally performs no schema
    changes and only restores the revision node in the migration graph.
    """


def downgrade() -> None:
    """No-op downgrade for the compatibility shim."""

