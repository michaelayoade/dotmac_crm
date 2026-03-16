"""add project sla entity and policy

Revision ID: y3d4e5f6a7b8
Revises: y2d3e4f5a6b7
Create Date: 2026-03-12 11:05:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "y3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "y2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SLA_POLICIES_TABLE = sa.table(
    "sla_policies",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("name", sa.String()),
    sa.column("entity_type", sa.String()),
    sa.column("description", sa.Text()),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

SLA_TARGETS_TABLE = sa.table(
    "sla_targets",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("policy_id", UUID(as_uuid=True)),
)

PROJECT_POLICY_NAME = "Project Completion SLA"
LEGACY_TICKET_POLICY_NAME = "Ticket SLA"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(UTC)

    with op.get_context().autocommit_block():
        conn.execute(sa.text("ALTER TYPE workflowentitytype ADD VALUE IF NOT EXISTS 'project'"))

    existing_project_policy = conn.execute(
        sa.select(SLA_POLICIES_TABLE.c.id)
        .where(SLA_POLICIES_TABLE.c.name == PROJECT_POLICY_NAME)
        .where(sa.cast(SLA_POLICIES_TABLE.c.entity_type, sa.String()) == "project")
    ).first()
    if not existing_project_policy:
        conn.execute(
            SLA_POLICIES_TABLE.insert().values(
                id=uuid.uuid4(),
                name=PROJECT_POLICY_NAME,
                entity_type=sa.text("'project'::workflowentitytype"),
                description="SLA policy for overall project completion timelines",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )

    legacy_ticket_policy = conn.execute(
        sa.select(SLA_POLICIES_TABLE.c.id)
        .where(SLA_POLICIES_TABLE.c.name == LEGACY_TICKET_POLICY_NAME)
        .where(sa.cast(SLA_POLICIES_TABLE.c.entity_type, sa.String()) == "ticket")
    ).first()
    if legacy_ticket_policy:
        conn.execute(SLA_TARGETS_TABLE.delete().where(SLA_TARGETS_TABLE.c.policy_id == legacy_ticket_policy.id))
        conn.execute(SLA_POLICIES_TABLE.delete().where(SLA_POLICIES_TABLE.c.id == legacy_ticket_policy.id))


def downgrade() -> None:
    conn = op.get_bind()
    project_policy = conn.execute(
        sa.select(SLA_POLICIES_TABLE.c.id)
        .where(SLA_POLICIES_TABLE.c.name == PROJECT_POLICY_NAME)
        .where(sa.cast(SLA_POLICIES_TABLE.c.entity_type, sa.String()) == "project")
    ).first()
    if project_policy:
        conn.execute(SLA_POLICIES_TABLE.delete().where(SLA_POLICIES_TABLE.c.id == project_policy.id))
