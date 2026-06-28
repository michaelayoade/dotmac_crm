"""add infrastructure alerts

Revision ID: ih2026062800
Revises: ms2026062800
Create Date: 2026-06-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ih2026062800"
down_revision = "ms2026062800"
branch_labels = None
depends_on = None


category_enum = postgresql.ENUM(
    "application_services",
    "background_workers",
    "scheduled_jobs",
    "queues",
    "database",
    "replication",
    "cache",
    "external_integrations",
    name="infrastructurealertcategory",
    create_type=False,
)
severity_enum = postgresql.ENUM("info", "warning", "critical", name="infrastructurealertseverity", create_type=False)
status_enum = postgresql.ENUM("open", "resolved", name="infrastructurealertstatus", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    category_enum.create(bind, checkfirst=True)
    severity_enum.create(bind, checkfirst=True)
    status_enum.create(bind, checkfirst=True)

    op.create_table(
        "infrastructure_alerts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("fingerprint", sa.String(length=200), nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("component", sa.String(length=160), nullable=False),
        sa.Column("severity", severity_enum, nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("check_key", sa.String(length=160), nullable=False),
        sa.Column("target_url", sa.String(length=500), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_infrastructure_alerts_fingerprint"),
    )
    op.create_index(
        "ix_infrastructure_alerts_category_status",
        "infrastructure_alerts",
        ["category", "status"],
        unique=False,
    )
    op.create_index("ix_infrastructure_alerts_last_seen_at", "infrastructure_alerts", ["last_seen_at"], unique=False)
    op.create_index(
        "ix_infrastructure_alerts_status_severity",
        "infrastructure_alerts",
        ["status", "severity"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_infrastructure_alerts_status_severity", table_name="infrastructure_alerts")
    op.drop_index("ix_infrastructure_alerts_last_seen_at", table_name="infrastructure_alerts")
    op.drop_index("ix_infrastructure_alerts_category_status", table_name="infrastructure_alerts")
    op.drop_table("infrastructure_alerts")
    status_enum.drop(op.get_bind(), checkfirst=True)
    severity_enum.drop(op.get_bind(), checkfirst=True)
    category_enum.drop(op.get_bind(), checkfirst=True)
