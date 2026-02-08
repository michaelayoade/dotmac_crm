"""add crm agent presence

Revision ID: c1b2c3d4e5f6
Revises: c0a1b2c3d4e5
Create Date: 2026-02-05 12:00:00.000000

"""

from datetime import UTC, datetime
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "c1b2c3d4e5f6"
down_revision = "c0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    presence_status_enum = sa.Enum(
        "online",
        "away",
        "offline",
        name="agentpresencestatus",
    )
    presence_status_enum.create(op.get_bind(), checkfirst=True)
    presence_status_enum_nocreate = postgresql.ENUM(
        "online",
        "away",
        "offline",
        name="agentpresencestatus",
        create_type=False,
    )

    op.create_table(
        "crm_agent_presence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", presence_status_enum_nocreate, nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["crm_agents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", name="uq_crm_agent_presence_agent_id"),
    )
    op.create_index(
        "ix_crm_agent_presence_status", "crm_agent_presence", ["status"]
    )
    op.create_index(
        "ix_crm_agent_presence_last_seen_at",
        "crm_agent_presence",
        ["last_seen_at"],
    )

    conn = op.get_bind()
    now = datetime.now(UTC)
    agent_rows = conn.execute(sa.text("SELECT id FROM crm_agents")).fetchall()
    if agent_rows:
        for (agent_id,) in agent_rows:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO crm_agent_presence
                        (id, agent_id, status, last_seen_at, created_at, updated_at)
                    VALUES
                        (:id, :agent_id, :status, :last_seen_at, :created_at, :updated_at)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "agent_id": agent_id,
                    "status": "offline",
                    "last_seen_at": None,
                    "created_at": now,
                    "updated_at": now,
                },
            )


def downgrade() -> None:
    op.drop_index("ix_crm_agent_presence_last_seen_at", table_name="crm_agent_presence")
    op.drop_index("ix_crm_agent_presence_status", table_name="crm_agent_presence")
    op.drop_table("crm_agent_presence")
    op.execute("DROP TYPE IF EXISTS agentpresencestatus")
