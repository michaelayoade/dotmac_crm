"""add fiber segment geometry_stale flag

Revision ID: qf7a8b9c0d1e
Revises: qe6f7a8b9c0d
Create Date: 2026-06-28 00:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "qf7a8b9c0d1e"
down_revision = "qe6f7a8b9c0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("fiber_segments")}
    if "geometry_stale" not in columns:
        op.add_column(
            "fiber_segments",
            sa.Column("geometry_stale", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade() -> None:
    op.drop_column("fiber_segments", "geometry_stale")
