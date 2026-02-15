"""add agent performance tables

Revision ID: p1a2b3c4d5e6
Revises: o1a2b3c4d5f2
Create Date: 2026-02-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "p1a2b3c4d5e6"
down_revision = "o1a2b3c4d5f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    performance_domain = postgresql.ENUM(
        "support",
        "operations",
        "field_service",
        "communication",
        "sales",
        "data_quality",
        name="performancedomain",
        create_type=False,
    )
    performance_domain.create(op.get_bind(), checkfirst=True)

    goal_status = postgresql.ENUM(
        "active",
        "achieved",
        "missed",
        "canceled",
        name="goalstatus",
        create_type=False,
    )
    goal_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "agent_performance_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("domain", performance_domain, nullable=False),
        sa.Column("raw_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("weighted_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.UniqueConstraint("person_id", "score_period_start", "domain", name="uq_perf_score_person_period_domain"),
    )
    op.create_index("ix_perf_score_person_period", "agent_performance_scores", ["person_id", "score_period_start"])
    op.create_index("ix_perf_score_domain", "agent_performance_scores", ["domain"])
    op.create_index("ix_perf_score_period", "agent_performance_scores", ["score_period_start"])

    op.create_table(
        "agent_performance_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("score_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("composite_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("domain_scores_json", sa.JSON(), nullable=False),
        sa.Column("weights_json", sa.JSON(), nullable=False),
        sa.Column("team_type", sa.String(40), nullable=True),
        sa.Column("sales_activity_ratio", sa.Numeric(8, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["service_teams.id"]),
        sa.UniqueConstraint("person_id", "score_period_start", "score_period_end", name="uq_perf_snapshot_person_period"),
    )
    op.create_index("ix_perf_snapshot_period", "agent_performance_snapshots", ["score_period_start"])
    op.create_index("ix_perf_snapshot_composite", "agent_performance_snapshots", ["composite_score"])
    op.create_index("ix_perf_snapshot_team_period", "agent_performance_snapshots", ["team_id", "score_period_start"])

    op.create_table(
        "agent_performance_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("composite_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("domain_scores_json", sa.JSON(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("strengths_json", sa.JSON(), nullable=False),
        sa.Column("improvements_json", sa.JSON(), nullable=False),
        sa.Column("recommendations_json", sa.JSON(), nullable=False),
        sa.Column("callouts_json", sa.JSON(), nullable=False),
        sa.Column("llm_model", sa.String(100), nullable=False),
        sa.Column("llm_provider", sa.String(40), nullable=False),
        sa.Column("llm_tokens_in", sa.Integer(), nullable=True),
        sa.Column("llm_tokens_out", sa.Integer(), nullable=True),
        sa.Column("is_acknowledged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.UniqueConstraint("person_id", "review_period_start", "review_period_end", name="uq_perf_review_person_period"),
    )
    op.create_index("ix_perf_review_person_period", "agent_performance_reviews", ["person_id", "review_period_start"])
    op.create_index("ix_perf_review_ack", "agent_performance_reviews", ["is_acknowledged"])

    op.create_table(
        "agent_performance_goals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", performance_domain, nullable=False),
        sa.Column("metric_key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("target_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("current_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("comparison", sa.String(10), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=False),
        sa.Column("status", goal_status, nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["created_by_person_id"], ["people.id"]),
    )
    op.create_index("ix_perf_goal_person_status", "agent_performance_goals", ["person_id", "status"])
    op.create_index("ix_perf_goal_deadline", "agent_performance_goals", ["deadline"])


def downgrade() -> None:
    op.drop_index("ix_perf_goal_deadline", table_name="agent_performance_goals")
    op.drop_index("ix_perf_goal_person_status", table_name="agent_performance_goals")
    op.drop_table("agent_performance_goals")

    op.drop_index("ix_perf_review_ack", table_name="agent_performance_reviews")
    op.drop_index("ix_perf_review_person_period", table_name="agent_performance_reviews")
    op.drop_table("agent_performance_reviews")

    op.drop_index("ix_perf_snapshot_team_period", table_name="agent_performance_snapshots")
    op.drop_index("ix_perf_snapshot_composite", table_name="agent_performance_snapshots")
    op.drop_index("ix_perf_snapshot_period", table_name="agent_performance_snapshots")
    op.drop_table("agent_performance_snapshots")

    op.drop_index("ix_perf_score_period", table_name="agent_performance_scores")
    op.drop_index("ix_perf_score_domain", table_name="agent_performance_scores")
    op.drop_index("ix_perf_score_person_period", table_name="agent_performance_scores")
    op.drop_table("agent_performance_scores")

    op.execute("DROP TYPE IF EXISTS goalstatus")
    op.execute("DROP TYPE IF EXISTS performancedomain")
