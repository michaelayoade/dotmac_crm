"""add field map asset location provenance

Revision ID: qc4d5e6f7a8b
Revises: qb3c4d5e6f7a
Create Date: 2026-06-28 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "qc4d5e6f7a8b"
down_revision = "qb3c4d5e6f7a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("field_map_asset_location_provenance"):
        return

    op.create_table(
        "field_map_asset_location_provenance",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_type", sa.String(length=80), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column("updated_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_type", "asset_id", name="uq_field_map_asset_location_provenance_asset"),
    )


def downgrade() -> None:
    op.drop_table("field_map_asset_location_provenance")
