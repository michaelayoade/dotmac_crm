"""add_crm_agent_live_location_tracking

Revision ID: aa7b8c9d0e1f
Revises: z1c2d3e4f5a6
Create Date: 2026-02-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "aa7b8c9d0e1f"
down_revision = "z1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_agent_presence",
        sa.Column("location_sharing_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("crm_agent_presence", sa.Column("last_latitude", sa.Float(), nullable=True))
    op.add_column("crm_agent_presence", sa.Column("last_longitude", sa.Float(), nullable=True))
    op.add_column("crm_agent_presence", sa.Column("last_location_accuracy_m", sa.Float(), nullable=True))
    op.add_column("crm_agent_presence", sa.Column("last_location_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "crm_agent_location_pings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source", sa.String(length=32), nullable=False, server_default=sa.text("'browser'")),
        sa.ForeignKeyConstraint(["agent_id"], ["crm_agents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_crm_agent_location_pings_lat_range"),
        sa.CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_crm_agent_location_pings_lng_range"),
    )
    op.create_index(
        "ix_crm_agent_location_pings_agent_received",
        "crm_agent_location_pings",
        ["agent_id", "received_at"],
        unique=False,
    )
    op.create_index(
        "ix_crm_agent_location_pings_received_at",
        "crm_agent_location_pings",
        ["received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_crm_agent_location_pings_received_at", table_name="crm_agent_location_pings")
    op.drop_index("ix_crm_agent_location_pings_agent_received", table_name="crm_agent_location_pings")
    op.drop_table("crm_agent_location_pings")

    op.drop_column("crm_agent_presence", "last_location_at")
    op.drop_column("crm_agent_presence", "last_location_accuracy_m")
    op.drop_column("crm_agent_presence", "last_longitude")
    op.drop_column("crm_agent_presence", "last_latitude")
    op.drop_column("crm_agent_presence", "location_sharing_enabled")
