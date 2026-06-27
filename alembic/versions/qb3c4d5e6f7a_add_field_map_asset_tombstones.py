"""add field map asset tombstones

Revision ID: qb3c4d5e6f7a
Revises: qa2b3c4d5e6f
Create Date: 2026-06-27 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "qb3c4d5e6f7a"
down_revision = "qa2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("field_map_asset_tombstones"):
        return

    op.create_table(
        "field_map_asset_tombstones",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_type", sa.String(length=80), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_type", "asset_id", name="uq_field_map_asset_tombstones_asset"),
    )
    op.create_index(
        "ix_field_map_asset_tombstones_deleted_at",
        "field_map_asset_tombstones",
        ["deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_field_map_asset_tombstones_deleted_at", table_name="field_map_asset_tombstones")
    op.drop_table("field_map_asset_tombstones")
