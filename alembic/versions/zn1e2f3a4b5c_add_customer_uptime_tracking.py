"""add customer uptime tracking

Revision ID: zn1e2f3a4b5c
Revises: zm0e1f2a3b4c
Create Date: 2026-05-29 11:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zn1e2f3a4b5c"
down_revision = "zm0e1f2a3b4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_uptime_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.String(length=120), nullable=False),
        sa.Column("service_id", sa.String(length=120), nullable=True),
        sa.Column("login", sa.String(length=120), nullable=True),
        sa.Column("is_online", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_session", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_change", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_on", sa.Integer(), nullable=True),
        sa.Column("in_bytes", sa.BigInteger(), nullable=True),
        sa.Column("out_bytes", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_customer_uptime_snapshots_customer_observed",
        "customer_uptime_snapshots",
        ["customer_id", "observed_at"],
    )
    op.create_index(
        "ix_customer_uptime_snapshots_service_observed",
        "customer_uptime_snapshots",
        ["service_id", "observed_at"],
    )
    op.create_index(
        "ix_customer_uptime_snapshots_observed_at",
        "customer_uptime_snapshots",
        ["observed_at"],
    )

    op.create_table(
        "customer_uptime_periods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.String(length=120), nullable=False),
        sa.Column("service_id", sa.String(length=120), nullable=True),
        sa.Column("login", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.String(length=40), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_customer_uptime_periods_open",
        "customer_uptime_periods",
        ["customer_id", "service_id"],
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_customer_uptime_periods_customer_started",
        "customer_uptime_periods",
        ["customer_id", "started_at"],
    )
    op.create_index(
        "ix_customer_uptime_periods_service_started",
        "customer_uptime_periods",
        ["service_id", "started_at"],
    )
    op.create_index(
        "ix_customer_uptime_periods_status_started",
        "customer_uptime_periods",
        ["status", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_customer_uptime_periods_status_started", table_name="customer_uptime_periods")
    op.drop_index("ix_customer_uptime_periods_service_started", table_name="customer_uptime_periods")
    op.drop_index("ix_customer_uptime_periods_customer_started", table_name="customer_uptime_periods")
    op.drop_index("ix_customer_uptime_periods_open", table_name="customer_uptime_periods")
    op.drop_table("customer_uptime_periods")

    op.drop_index("ix_customer_uptime_snapshots_observed_at", table_name="customer_uptime_snapshots")
    op.drop_index("ix_customer_uptime_snapshots_service_observed", table_name="customer_uptime_snapshots")
    op.drop_index("ix_customer_uptime_snapshots_customer_observed", table_name="customer_uptime_snapshots")
    op.drop_table("customer_uptime_snapshots")
