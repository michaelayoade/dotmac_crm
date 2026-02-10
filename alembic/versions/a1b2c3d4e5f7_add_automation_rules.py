"""add automation rules

Revision ID: a1b2c3d4e5f7
Revises: ab12cd34ef56
Create Date: 2026-02-03 21:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f7"
down_revision = "ab12cd34ef56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types
    automation_rule_status = postgresql.ENUM(
        "active", "paused", "archived",
        name="automationrulestatus",
        create_type=False,
    )
    automation_log_outcome = postgresql.ENUM(
        "success", "partial_failure", "failure", "skipped",
        name="automationlogoutcome",
        create_type=False,
    )

    bind = op.get_bind()
    automation_rule_status.create(bind, checkfirst=True)
    automation_log_outcome.create(bind, checkfirst=True)

    op.create_table(
        "automation_rules",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("conditions", postgresql.JSONB(), nullable=True),
        sa.Column("actions", postgresql.JSONB(), nullable=True),
        sa.Column(
            "status",
            automation_rule_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stop_after_match", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
    )

    op.create_index(
        "ix_automation_rules_active_event",
        "automation_rules",
        ["event_type", "status", "is_active", "priority"],
    )

    op.create_table(
        "automation_rule_logs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rule_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("event_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("outcome", automation_log_outcome, nullable=False),
        sa.Column("actions_executed", postgresql.JSONB(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["automation_rules.id"]),
    )

    op.create_index(
        "ix_automation_rule_logs_rule_id",
        "automation_rule_logs",
        ["rule_id"],
    )
    op.create_index(
        "ix_automation_rule_logs_created_at",
        "automation_rule_logs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("automation_rule_logs")
    op.drop_table("automation_rules")

    bind = op.get_bind()
    postgresql.ENUM(name="automationlogoutcome", create_type=False).drop(bind, checkfirst=True)
    postgresql.ENUM(name="automationrulestatus", create_type=False).drop(bind, checkfirst=True)
