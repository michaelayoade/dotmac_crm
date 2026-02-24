"""add presence manual override and events

Revision ID: w7a8b9c0d1e2
Revises: v6a7b8c9d0e1
Create Date: 2026-02-17

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "w7a8b9c0d1e2"
down_revision = "v6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add enum value safely (works even if already applied).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                WHERE t.typname = 'agentpresencestatus'
                  AND e.enumlabel = 'on_break'
            ) THEN
                ALTER TYPE agentpresencestatus ADD VALUE 'on_break';
            END IF;
        END $$;
        """
    )

    # Columns on crm_agent_presence
    presence_status_enum = postgresql.ENUM(
        "online",
        "away",
        "offline",
        "on_break",
        name="agentpresencestatus",
        create_type=False,
    )

    op.add_column(
        "crm_agent_presence",
        sa.Column("manual_override_status", presence_status_enum, nullable=True),
    )
    op.add_column(
        "crm_agent_presence",
        sa.Column("manual_override_set_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_crm_agent_presence_manual_override_status",
        "crm_agent_presence",
        ["manual_override_status"],
    )

    # Event table for reporting durations.
    op.create_table(
        "crm_agent_presence_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", presence_status_enum, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["agent_id"], ["crm_agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_crm_agent_presence_events_agent_id",
        "crm_agent_presence_events",
        ["agent_id"],
    )
    op.create_index(
        "ix_crm_agent_presence_events_status",
        "crm_agent_presence_events",
        ["status"],
    )
    op.create_index(
        "ix_crm_agent_presence_events_started_at",
        "crm_agent_presence_events",
        ["started_at"],
    )
    op.create_index(
        "ix_crm_agent_presence_events_ended_at",
        "crm_agent_presence_events",
        ["ended_at"],
    )


def downgrade() -> None:
    # NOTE: We do not remove enum values on downgrade; PostgreSQL doesn't support it safely.
    op.drop_index("ix_crm_agent_presence_events_ended_at", table_name="crm_agent_presence_events")
    op.drop_index("ix_crm_agent_presence_events_started_at", table_name="crm_agent_presence_events")
    op.drop_index("ix_crm_agent_presence_events_status", table_name="crm_agent_presence_events")
    op.drop_index("ix_crm_agent_presence_events_agent_id", table_name="crm_agent_presence_events")
    op.drop_table("crm_agent_presence_events")

    op.drop_index("ix_crm_agent_presence_manual_override_status", table_name="crm_agent_presence")
    op.drop_column("crm_agent_presence", "manual_override_set_at")
    op.drop_column("crm_agent_presence", "manual_override_status")
