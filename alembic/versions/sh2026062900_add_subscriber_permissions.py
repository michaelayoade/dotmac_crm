"""Add subscriber RBAC permissions

Seeds subscribers:subscriber:read/write/delete and grants them to any role or
person that already holds the analogous crm:contact permission, so existing CRM
operators keep access to subscriber management once the API routes are gated.

Revision ID: sh2026062900
Revises: ms2026062800
Create Date: 2026-06-29 00:00:00.000000

"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "sh2026062900"
down_revision = "ms2026062800"
branch_labels = None
depends_on = None

_SUBSCRIBER_PERMISSIONS = (
    ("subscribers:subscriber:read", "View subscribers"),
    ("subscribers:subscriber:write", "Create and update subscribers"),
    ("subscribers:subscriber:delete", "Delete subscribers"),
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _permissions_table() -> sa.Table:
    return sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _role_permissions_table() -> sa.Table:
    return sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )


def _person_permissions_table() -> sa.Table:
    return sa.table(
        "person_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("person_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("granted_at", sa.DateTime(timezone=True)),
        sa.column("granted_by_person_id", sa.Uuid()),
    )


def _ensure_permission(conn, permissions, *, key: str, description: str):
    existing_id = conn.execute(sa.select(permissions.c.id).where(permissions.c.key == key)).scalar_one_or_none()
    if existing_id:
        conn.execute(
            permissions.update().where(permissions.c.id == existing_id).values(is_active=True, updated_at=_utcnow())
        )
        return existing_id
    now = _utcnow()
    new_id = uuid4()
    conn.execute(
        permissions.insert().values(
            id=new_id, key=key, description=description, is_active=True, created_at=now, updated_at=now
        )
    )
    return new_id


def _perm_id(conn, permissions, key: str):
    return conn.execute(sa.select(permissions.c.id).where(permissions.c.key == key)).scalar_one_or_none()


def _grant_from_source(conn, role_permissions, person_permissions, *, source_perm_id, target_perm_id) -> None:
    """Grant target_perm to every role/person that already holds source_perm."""
    if source_perm_id is None or target_perm_id is None:
        return
    role_ids = (
        conn.execute(
            sa.select(role_permissions.c.role_id).where(role_permissions.c.permission_id == source_perm_id).distinct()
        )
        .scalars()
        .all()
    )
    for role_id in role_ids:
        exists = conn.execute(
            sa.select(role_permissions.c.id)
            .where(role_permissions.c.role_id == role_id)
            .where(role_permissions.c.permission_id == target_perm_id)
        ).scalar_one_or_none()
        if not exists:
            conn.execute(role_permissions.insert().values(id=uuid4(), role_id=role_id, permission_id=target_perm_id))
    person_ids = (
        conn.execute(
            sa.select(person_permissions.c.person_id)
            .where(person_permissions.c.permission_id == source_perm_id)
            .distinct()
        )
        .scalars()
        .all()
    )
    for person_id in person_ids:
        exists = conn.execute(
            sa.select(person_permissions.c.id)
            .where(person_permissions.c.person_id == person_id)
            .where(person_permissions.c.permission_id == target_perm_id)
        ).scalar_one_or_none()
        if not exists:
            conn.execute(
                person_permissions.insert().values(
                    id=uuid4(),
                    person_id=person_id,
                    permission_id=target_perm_id,
                    granted_at=_utcnow(),
                    granted_by_person_id=None,
                )
            )


def upgrade() -> None:
    conn = op.get_bind()
    permissions = _permissions_table()
    role_permissions = _role_permissions_table()
    person_permissions = _person_permissions_table()

    for key, description in _SUBSCRIBER_PERMISSIONS:
        _ensure_permission(conn, permissions, key=key, description=description)

    contact_read = _perm_id(conn, permissions, "crm:contact:read")
    contact_write = _perm_id(conn, permissions, "crm:contact:write")
    sub_read = _perm_id(conn, permissions, "subscribers:subscriber:read")
    sub_write = _perm_id(conn, permissions, "subscribers:subscriber:write")

    # contact:read holders → subscriber read
    _grant_from_source(conn, role_permissions, person_permissions, source_perm_id=contact_read, target_perm_id=sub_read)
    # contact:write holders → subscriber read + write (NOT delete — deletion is a
    # destructive op left to admins, who bypass require_permission entirely, or to
    # explicit grants of subscribers:subscriber:delete).
    for target in (sub_read, sub_write):
        _grant_from_source(
            conn, role_permissions, person_permissions, source_perm_id=contact_write, target_perm_id=target
        )


def downgrade() -> None:
    conn = op.get_bind()
    permissions = _permissions_table()
    role_permissions = sa.table(
        "role_permissions",
        sa.column("permission_id", sa.Uuid()),
    )
    person_permissions = sa.table(
        "person_permissions",
        sa.column("permission_id", sa.Uuid()),
    )
    keys = [key for key, _ in _SUBSCRIBER_PERMISSIONS]
    perm_ids = conn.execute(sa.select(permissions.c.id).where(permissions.c.key.in_(keys))).scalars().all()
    if not perm_ids:
        return
    conn.execute(person_permissions.delete().where(person_permissions.c.permission_id.in_(perm_ids)))
    conn.execute(role_permissions.delete().where(role_permissions.c.permission_id.in_(perm_ids)))
    conn.execute(permissions.delete().where(permissions.c.id.in_(perm_ids)))
