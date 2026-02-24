"""add_fiber_qa_remediation_logs

Revision ID: bfcbe1032692
Revises: w7a8b9c0d1e2
Create Date: 2026-02-17 10:26:38.566394

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'bfcbe1032692'
down_revision = 'w7a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fiber_qa_remediation_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("asset_type", sa.String(length=50), nullable=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("issue_type", sa.String(length=100), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("action_taken", sa.String(length=50), nullable=True),
        sa.Column("performed_by", sa.String(length=100), nullable=False, server_default=sa.text("'codex_strategy'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index(
        "idx_qa_logs_asset",
        "fiber_qa_remediation_logs",
        ["asset_type", "asset_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_qa_logs_asset", table_name="fiber_qa_remediation_logs")
    op.drop_table("fiber_qa_remediation_logs")
