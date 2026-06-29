"""Add postpaid customers report viewer role.

Revision ID: pcr2026062900
Revises: adad6ae61bb0
Create Date: 2026-06-29 00:00:00.000000

"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "pcr2026062900"
down_revision = "adad6ae61bb0"
branch_labels = None
depends_on = None

PERMISSION_KEY = "reports:postpaid-customers:read"
ROLE_NAME = "postpaid_report_viewer"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ensure_permission(conn: sa.engine.Connection, permissions: sa.Table) -> object:
    existing_id = conn.execute(sa.select(permissions.c.id).where(permissions.c.key == PERMISSION_KEY)).scalar_one_or_none()
    if existing_id:
        conn.execute(
            permissions.update()
            .where(permissions.c.id == existing_id)
            .values(is_active=True, updated_at=_utcnow())
        )
        return existing_id

    now = _utcnow()
    permission_id = uuid4()
    conn.execute(
        permissions.insert().values(
            id=permission_id,
            key=PERMISSION_KEY,
            description="View postpaid customers report",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    return permission_id


def _ensure_role(conn: sa.engine.Connection, roles: sa.Table) -> object:
    existing_id = conn.execute(sa.select(roles.c.id).where(roles.c.name == ROLE_NAME)).scalar_one_or_none()
    if existing_id:
        conn.execute(roles.update().where(roles.c.id == existing_id).values(is_active=True, updated_at=_utcnow()))
        return existing_id

    now = _utcnow()
    role_id = uuid4()
    conn.execute(
        roles.insert().values(
            id=role_id,
            name=ROLE_NAME,
            description="Read-only access to the postpaid customers report",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    return role_id


def upgrade() -> None:
    conn = op.get_bind()
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    roles = sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )

    permission_id = _ensure_permission(conn, permissions)
    role_id = _ensure_role(conn, roles)
    existing_link = conn.execute(
        sa.select(role_permissions.c.id)
        .where(role_permissions.c.role_id == role_id)
        .where(role_permissions.c.permission_id == permission_id)
    ).scalar_one_or_none()
    if not existing_link:
        conn.execute(role_permissions.insert().values(id=uuid4(), role_id=role_id, permission_id=permission_id))


def downgrade() -> None:
    conn = op.get_bind()
    permissions = sa.table("permissions", sa.column("id", sa.Uuid()), sa.column("key", sa.String()))
    roles = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("name", sa.String()))
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )
    person_permissions = sa.table("person_permissions", sa.column("permission_id", sa.Uuid()))

    permission_id = conn.execute(sa.select(permissions.c.id).where(permissions.c.key == PERMISSION_KEY)).scalar_one_or_none()
    role_id = conn.execute(sa.select(roles.c.id).where(roles.c.name == ROLE_NAME)).scalar_one_or_none()
    if permission_id and role_id:
        conn.execute(
            role_permissions.delete()
            .where(role_permissions.c.role_id == role_id)
            .where(role_permissions.c.permission_id == permission_id)
        )
    if permission_id:
        conn.execute(person_permissions.delete().where(person_permissions.c.permission_id == permission_id))
        conn.execute(permissions.delete().where(permissions.c.id == permission_id))
