"""add_qa_remediation_approval_fields

Revision ID: 08c2b28e5407
Revises: bfcbe1032692
Create Date: 2026-02-17 10:34:37.638059

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '08c2b28e5407'
down_revision = 'bfcbe1032692'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fiber_qa_remediation_logs",
        sa.Column("status", sa.String(length=30), nullable=False, server_default=sa.text("'pending'")),
    )
    op.add_column(
        "fiber_qa_remediation_logs",
        sa.Column("review_notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "fiber_qa_remediation_logs",
        sa.Column("target_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "fiber_qa_remediation_logs",
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "fiber_qa_remediation_logs",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_fiber_qa_remediation_logs_status",
        "fiber_qa_remediation_logs",
        ["status"],
    )
    op.create_index(
        "ix_fiber_qa_remediation_logs_issue_type",
        "fiber_qa_remediation_logs",
        ["issue_type"],
    )
    op.create_index(
        "ix_fiber_qa_remediation_logs_target_asset_id",
        "fiber_qa_remediation_logs",
        ["target_asset_id"],
    )

    # Backfill `target_asset_id` from `new_value` when it looks like a UUID.
    op.execute(
        sa.text(
            """
            update fiber_qa_remediation_logs
               set target_asset_id = nullif(new_value, '')::uuid
             where target_asset_id is null
               and new_value is not null
               and new_value ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_fiber_qa_remediation_logs_target_asset_id", table_name="fiber_qa_remediation_logs")
    op.drop_index("ix_fiber_qa_remediation_logs_issue_type", table_name="fiber_qa_remediation_logs")
    op.drop_index("ix_fiber_qa_remediation_logs_status", table_name="fiber_qa_remediation_logs")
    op.drop_column("fiber_qa_remediation_logs", "approved_at")
    op.drop_column("fiber_qa_remediation_logs", "approved_by")
    op.drop_column("fiber_qa_remediation_logs", "target_asset_id")
    op.drop_column("fiber_qa_remediation_logs", "review_notes")
    op.drop_column("fiber_qa_remediation_logs", "status")
