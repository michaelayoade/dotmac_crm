"""Add context_quality_score column and skipped status to ai_insights.

Revision ID: q1a2b3c4d5f3
Revises: p1a2b3c4d5e6
Create Date: 2026-02-15
"""

import sqlalchemy as sa
from alembic import op

revision = "q1a2b3c4d5f3"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ai_insights", sa.Column("context_quality_score", sa.Numeric(3, 2)))
    op.create_index("ix_ai_insights_context_quality", "ai_insights", ["context_quality_score"])

    # Add 'skipped' to aiinsightstatus enum
    op.execute("ALTER TYPE aiinsightstatus ADD VALUE IF NOT EXISTS 'skipped' AFTER 'failed'")


def downgrade():
    op.drop_index("ix_ai_insights_context_quality", table_name="ai_insights")
    op.drop_column("ai_insights", "context_quality_score")
    # Note: PostgreSQL cannot remove enum values; 'skipped' remains but is unused
