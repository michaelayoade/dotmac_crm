"""backfill existing sla clocks

Revision ID: y4e5f6a7b8c9
Revises: y3d4e5f6a7b8
Create Date: 2026-03-12 13:10:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "y4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "y3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SLA_POLICIES_TABLE = sa.table(
    "sla_policies",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("name", sa.String()),
    sa.column("entity_type", sa.String()),
    sa.column("is_active", sa.Boolean()),
)

SLA_TARGETS_TABLE = sa.table(
    "sla_targets",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("policy_id", UUID(as_uuid=True)),
    sa.column("priority", sa.String()),
    sa.column("target_minutes", sa.Integer()),
    sa.column("is_active", sa.Boolean()),
)

SLA_CLOCKS_TABLE = sa.table(
    "sla_clocks",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("policy_id", UUID(as_uuid=True)),
    sa.column("entity_type", sa.String()),
    sa.column("entity_id", UUID(as_uuid=True)),
    sa.column("priority", sa.String()),
    sa.column("status", sa.String()),
    sa.column("started_at", sa.DateTime(timezone=True)),
    sa.column("paused_at", sa.DateTime(timezone=True)),
    sa.column("total_paused_seconds", sa.Integer()),
    sa.column("due_at", sa.DateTime(timezone=True)),
    sa.column("completed_at", sa.DateTime(timezone=True)),
    sa.column("breached_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

SLA_BREACHES_TABLE = sa.table(
    "sla_breaches",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("clock_id", UUID(as_uuid=True)),
    sa.column("status", sa.String()),
    sa.column("breached_at", sa.DateTime(timezone=True)),
    sa.column("notes", sa.Text()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

TICKETS_TABLE = sa.table(
    "tickets",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("status", sa.String()),
    sa.column("priority", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("is_active", sa.Boolean()),
)

PROJECTS_TABLE = sa.table(
    "projects",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("status", sa.String()),
    sa.column("project_type", sa.String()),
    sa.column("start_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("due_at", sa.DateTime(timezone=True)),
    sa.column("is_active", sa.Boolean()),
)

PROJECT_TASKS_TABLE = sa.table(
    "project_tasks",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("status", sa.String()),
    sa.column("priority", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("due_at", sa.DateTime(timezone=True)),
    sa.column("is_active", sa.Boolean()),
)

TICKET_SLA_POLICY_NAME = "Ticket Resolution SLA"
PROJECT_SLA_POLICY_NAME = "Project Completion SLA"
PROJECT_TASK_SLA_POLICY_NAME = "Fiber Project Task SLA"

TICKET_TERMINAL_STATUSES = {"resolved", "closed", "canceled", "merged"}
PROJECT_TERMINAL_STATUSES = {"completed", "canceled"}
PROJECT_TASK_TERMINAL_STATUSES = {"done", "canceled"}


def _policy_id(conn: sa.Connection, *, name: str, entity_type: str) -> uuid.UUID | None:
    row = conn.execute(
        sa.select(SLA_POLICIES_TABLE.c.id)
        .where(SLA_POLICIES_TABLE.c.name == name)
        .where(sa.cast(SLA_POLICIES_TABLE.c.entity_type, sa.String()) == entity_type)
        .where(SLA_POLICIES_TABLE.c.is_active.is_(True))
    ).first()
    return row.id if row else None


def _ticket_target_minutes(conn: sa.Connection, policy_id: uuid.UUID) -> dict[str | None, int]:
    rows = conn.execute(
        sa.select(SLA_TARGETS_TABLE.c.priority, SLA_TARGETS_TABLE.c.target_minutes)
        .where(SLA_TARGETS_TABLE.c.policy_id == policy_id)
        .where(SLA_TARGETS_TABLE.c.is_active.is_(True))
    ).all()
    return {row.priority: row.target_minutes for row in rows}


def _has_clock(conn: sa.Connection, *, entity_type: str, entity_id: uuid.UUID) -> bool:
    return (
        conn.execute(
            sa.select(SLA_CLOCKS_TABLE.c.id)
            .where(sa.cast(SLA_CLOCKS_TABLE.c.entity_type, sa.String()) == entity_type)
            .where(SLA_CLOCKS_TABLE.c.entity_id == entity_id)
            .limit(1)
        ).first()
        is not None
    )


def _insert_clock(
    conn: sa.Connection,
    *,
    policy_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    priority: str | None,
    started_at: datetime,
    due_at: datetime,
    now: datetime,
) -> uuid.UUID:
    clock_id = uuid.uuid4()
    is_breached = due_at <= now
    conn.execute(
        SLA_CLOCKS_TABLE.insert().values(
            id=clock_id,
            policy_id=policy_id,
            entity_type=sa.text(f"'{entity_type}'::workflowentitytype"),
            entity_id=entity_id,
            priority=priority,
            status=sa.text(f"'{'breached' if is_breached else 'running'}'::slaclockstatus"),
            started_at=started_at,
            paused_at=None,
            total_paused_seconds=0,
            due_at=due_at,
            completed_at=None,
            breached_at=due_at if is_breached else None,
            created_at=now,
            updated_at=now,
        )
    )
    if is_breached:
        conn.execute(
            SLA_BREACHES_TABLE.insert().values(
                id=uuid.uuid4(),
                clock_id=clock_id,
                status=sa.text("'open'::slabreachstatus"),
                breached_at=due_at,
                notes="Backfilled from existing overdue record during SLA rollout.",
                created_at=now,
                updated_at=now,
            )
        )
    return clock_id


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(UTC)

    ticket_policy_id = _policy_id(conn, name=TICKET_SLA_POLICY_NAME, entity_type="ticket")
    if ticket_policy_id:
        target_minutes_by_priority = _ticket_target_minutes(conn, ticket_policy_id)
        ticket_rows = conn.execute(
            sa.select(
                TICKETS_TABLE.c.id,
                TICKETS_TABLE.c.priority,
                TICKETS_TABLE.c.created_at,
            )
            .where(TICKETS_TABLE.c.is_active.is_(True))
            .where(sa.cast(TICKETS_TABLE.c.status, sa.String()).not_in(TICKET_TERMINAL_STATUSES))
        ).all()
        for row in ticket_rows:
            if _has_clock(conn, entity_type="ticket", entity_id=row.id):
                continue
            target_minutes = target_minutes_by_priority.get(row.priority)
            if target_minutes is None:
                target_minutes = target_minutes_by_priority.get(None)
            if target_minutes is None:
                continue
            started_at = row.created_at or now
            due_at = started_at + timedelta(minutes=target_minutes)
            _insert_clock(
                conn,
                policy_id=ticket_policy_id,
                entity_type="ticket",
                entity_id=row.id,
                priority=row.priority,
                started_at=started_at,
                due_at=due_at,
                now=now,
            )

    project_policy_id = _policy_id(conn, name=PROJECT_SLA_POLICY_NAME, entity_type="project")
    if project_policy_id:
        project_rows = conn.execute(
            sa.select(
                PROJECTS_TABLE.c.id,
                PROJECTS_TABLE.c.project_type,
                PROJECTS_TABLE.c.start_at,
                PROJECTS_TABLE.c.created_at,
                PROJECTS_TABLE.c.due_at,
            )
            .where(PROJECTS_TABLE.c.is_active.is_(True))
            .where(PROJECTS_TABLE.c.due_at.is_not(None))
            .where(sa.cast(PROJECTS_TABLE.c.status, sa.String()).not_in(PROJECT_TERMINAL_STATUSES))
        ).all()
        for row in project_rows:
            if _has_clock(conn, entity_type="project", entity_id=row.id):
                continue
            started_at = row.start_at or row.created_at or now
            _insert_clock(
                conn,
                policy_id=project_policy_id,
                entity_type="project",
                entity_id=row.id,
                priority=row.project_type,
                started_at=started_at,
                due_at=row.due_at,
                now=now,
            )

    project_task_policy_id = _policy_id(conn, name=PROJECT_TASK_SLA_POLICY_NAME, entity_type="project_task")
    if project_task_policy_id:
        task_rows = conn.execute(
            sa.select(
                PROJECT_TASKS_TABLE.c.id,
                PROJECT_TASKS_TABLE.c.priority,
                PROJECT_TASKS_TABLE.c.created_at,
                PROJECT_TASKS_TABLE.c.due_at,
            )
            .where(PROJECT_TASKS_TABLE.c.is_active.is_(True))
            .where(PROJECT_TASKS_TABLE.c.due_at.is_not(None))
            .where(sa.cast(PROJECT_TASKS_TABLE.c.status, sa.String()).not_in(PROJECT_TASK_TERMINAL_STATUSES))
        ).all()
        for row in task_rows:
            if _has_clock(conn, entity_type="project_task", entity_id=row.id):
                continue
            started_at = row.created_at or now
            _insert_clock(
                conn,
                policy_id=project_task_policy_id,
                entity_type="project_task",
                entity_id=row.id,
                priority=row.priority,
                started_at=started_at,
                due_at=row.due_at,
                now=now,
            )


def downgrade() -> None:
    # This migration backfills production data and is intentionally irreversible.
    return None
