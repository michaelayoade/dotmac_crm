"""add field attachment asset tag

Revision ID: qd5e6f7a8b9c
Revises: qc4d5e6f7a8b
Create Date: 2026-06-28 00:10:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "qd5e6f7a8b9c"
down_revision = "qc4d5e6f7a8b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("field_attachments")}
    if "asset_type" not in columns:
        op.add_column("field_attachments", sa.Column("asset_type", sa.String(length=80), nullable=True))
    if "asset_id" not in columns:
        op.add_column("field_attachments", sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=True))
    indexes = {idx["name"] for idx in inspector.get_indexes("field_attachments")}
    if "ix_field_attachments_asset" not in indexes:
        op.create_index("ix_field_attachments_asset", "field_attachments", ["asset_type", "asset_id"])


def downgrade() -> None:
    op.drop_index("ix_field_attachments_asset", table_name="field_attachments")
    op.drop_column("field_attachments", "asset_id")
    op.drop_column("field_attachments", "asset_type")
