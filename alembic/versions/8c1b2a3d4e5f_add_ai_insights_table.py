"""Add ai_insights table for Intelligence Engine.

Revision ID: 8c1b2a3d4e5f
Revises: 75b4f3e2c1d0
Create Date: 2026-02-14
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8c1b2a3d4e5f"
down_revision = "75b4f3e2c1d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insightdomain = sa.Enum(
        "tickets",
        "inbox",
        "projects",
        "performance",
        "vendors",
        "dispatch",
        "campaigns",
        "customer_success",
        name="insightdomain",
    )
    insightseverity = sa.Enum("info", "suggestion", "warning", "critical", name="insightseverity")
    aiinsightstatus = sa.Enum(
        "pending",
        "completed",
        "failed",
        "acknowledged",
        "actioned",
        "expired",
        name="aiinsightstatus",
    )

    op.create_table(
        "ai_insights",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("persona_key", sa.String(length=80), nullable=False),
        sa.Column("domain", insightdomain, nullable=False),
        sa.Column("severity", insightseverity, nullable=False, server_default="info"),
        sa.Column("status", aiinsightstatus, nullable=False, server_default="pending"),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=120), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("structured_output", sa.JSON(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("recommendations", sa.JSON(), nullable=True),
        sa.Column("llm_provider", sa.String(length=40), nullable=False, server_default="vllm"),
        sa.Column("llm_model", sa.String(length=100), nullable=False),
        sa.Column("llm_tokens_in", sa.Integer(), nullable=True),
        sa.Column("llm_tokens_out", sa.Integer(), nullable=True),
        sa.Column("llm_endpoint", sa.String(length=20), nullable=True),
        sa.Column("generation_time_ms", sa.Integer(), nullable=True),
        sa.Column("trigger", sa.String(length=40), nullable=False),
        sa.Column(
            "triggered_by_person_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "acknowledged_by_person_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_ai_insights_domain_status", "ai_insights", ["domain", "status"])
    op.create_index("ix_ai_insights_entity", "ai_insights", ["entity_type", "entity_id"])
    op.create_index("ix_ai_insights_persona", "ai_insights", ["persona_key"])
    op.create_index("ix_ai_insights_created", "ai_insights", ["created_at"])
    op.create_index("ix_ai_insights_severity", "ai_insights", ["severity"])


def downgrade() -> None:
    op.drop_index("ix_ai_insights_severity", table_name="ai_insights")
    op.drop_index("ix_ai_insights_created", table_name="ai_insights")
    op.drop_index("ix_ai_insights_persona", table_name="ai_insights")
    op.drop_index("ix_ai_insights_entity", table_name="ai_insights")
    op.drop_index("ix_ai_insights_domain_status", table_name="ai_insights")
    op.drop_table("ai_insights")

    op.execute("DROP TYPE IF EXISTS aiinsightstatus;")
    op.execute("DROP TYPE IF EXISTS insightseverity;")
    op.execute("DROP TYPE IF EXISTS insightdomain;")
