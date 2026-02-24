"""Add fiber_asset_merge_logs table

Revision ID: u5a6b7c8d9e0
Revises: t4a5b6c7d8e9
Create Date: 2026-02-16 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "u5a6b7c8d9e0"
down_revision = "t4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fiber_asset_merge_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_type", sa.String(80), nullable=False),
        sa.Column("source_asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("merged_by_id", UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=True),
        sa.Column("field_choices", sa.JSON(), nullable=True),
        sa.Column("children_migrated", sa.JSON(), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_fiber_asset_merge_logs_asset_type", "fiber_asset_merge_logs", ["asset_type"])
    op.create_index("ix_fiber_asset_merge_logs_source", "fiber_asset_merge_logs", ["source_asset_id"])
    op.create_index("ix_fiber_asset_merge_logs_target", "fiber_asset_merge_logs", ["target_asset_id"])


def downgrade() -> None:
    op.drop_index("ix_fiber_asset_merge_logs_target", table_name="fiber_asset_merge_logs")
    op.drop_index("ix_fiber_asset_merge_logs_source", table_name="fiber_asset_merge_logs")
    op.drop_index("ix_fiber_asset_merge_logs_asset_type", table_name="fiber_asset_merge_logs")
    op.drop_table("fiber_asset_merge_logs")
