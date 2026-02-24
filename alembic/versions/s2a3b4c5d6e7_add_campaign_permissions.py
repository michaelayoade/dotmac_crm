"""Add CRM campaign RBAC permissions

Revision ID: s2a3b4c5d6e7
Revises: 4e55c333e51e
Create Date: 2026-02-16 12:20:00.000000

"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "s2a3b4c5d6e7"
down_revision = "4e55c333e51e"
branch_labels = None
depends_on = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ensure_permission(
    conn: sa.engine.Connection,
    permissions: sa.Table,
    *,
    key: str,
    description: str,
) -> None:
    existing_id = conn.execute(sa.select(permissions.c.id).where(permissions.c.key == key)).scalar_one_or_none()
    if existing_id:
        conn.execute(
            permissions.update()
            .where(permissions.c.id == existing_id)
            .values(
                is_active=True,
                description=sa.case(
                    (permissions.c.description.is_(None), description),
                    else_=permissions.c.description,
                ),
                updated_at=_utcnow(),
            )
        )
        return

    now = _utcnow()
    conn.execute(
        permissions.insert().values(
            id=uuid4(),
            key=key,
            description=description,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )


def _grant_role_permission(
    conn: sa.engine.Connection,
    role_permissions: sa.Table,
    *,
    role_id,
    permission_id,
) -> None:
    exists = conn.execute(
        sa.select(role_permissions.c.id)
        .where(role_permissions.c.role_id == role_id)
        .where(role_permissions.c.permission_id == permission_id)
    ).scalar_one_or_none()
    if exists:
        return
    conn.execute(
        role_permissions.insert().values(
            id=uuid4(),
            role_id=role_id,
            permission_id=permission_id,
        )
    )


def _grant_person_permission(
    conn: sa.engine.Connection,
    person_permissions: sa.Table,
    *,
    person_id,
    permission_id,
) -> None:
    exists = conn.execute(
        sa.select(person_permissions.c.id)
        .where(person_permissions.c.person_id == person_id)
        .where(person_permissions.c.permission_id == permission_id)
    ).scalar_one_or_none()
    if exists:
        return
    conn.execute(
        person_permissions.insert().values(
            id=uuid4(),
            person_id=person_id,
            permission_id=permission_id,
            granted_at=_utcnow(),
            granted_by_person_id=None,
        )
    )


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
    role_permissions = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )
    person_permissions = sa.table(
        "person_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("person_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("granted_at", sa.DateTime(timezone=True)),
        sa.column("granted_by_person_id", sa.Uuid()),
    )

    _ensure_permission(conn, permissions, key="crm:campaign:read", description="View campaigns")
    _ensure_permission(conn, permissions, key="crm:campaign:write", description="Manage campaigns")

    campaign_read_id = conn.execute(
        sa.select(permissions.c.id).where(permissions.c.key == "crm:campaign:read")
    ).scalar_one()
    campaign_write_id = conn.execute(
        sa.select(permissions.c.id).where(permissions.c.key == "crm:campaign:write")
    ).scalar_one()
    conversation_read_id = conn.execute(
        sa.select(permissions.c.id).where(permissions.c.key == "crm:conversation:read")
    ).scalar_one_or_none()
    conversation_write_id = conn.execute(
        sa.select(permissions.c.id).where(permissions.c.key == "crm:conversation:write")
    ).scalar_one_or_none()

    if conversation_read_id is not None:
        role_ids = conn.execute(
            sa.select(role_permissions.c.role_id)
            .where(role_permissions.c.permission_id == conversation_read_id)
            .distinct()
        ).scalars().all()
        for role_id in role_ids:
            _grant_role_permission(conn, role_permissions, role_id=role_id, permission_id=campaign_read_id)

        person_ids = conn.execute(
            sa.select(person_permissions.c.person_id)
            .where(person_permissions.c.permission_id == conversation_read_id)
            .distinct()
        ).scalars().all()
        for person_id in person_ids:
            _grant_person_permission(conn, person_permissions, person_id=person_id, permission_id=campaign_read_id)

    if conversation_write_id is not None:
        read_role_ids = conn.execute(
            sa.select(role_permissions.c.role_id)
            .where(role_permissions.c.permission_id == conversation_write_id)
            .distinct()
        ).scalars().all()
        for role_id in read_role_ids:
            _grant_role_permission(conn, role_permissions, role_id=role_id, permission_id=campaign_read_id)
            _grant_role_permission(conn, role_permissions, role_id=role_id, permission_id=campaign_write_id)

        person_ids = conn.execute(
            sa.select(person_permissions.c.person_id)
            .where(person_permissions.c.permission_id == conversation_write_id)
            .distinct()
        ).scalars().all()
        for person_id in person_ids:
            _grant_person_permission(conn, person_permissions, person_id=person_id, permission_id=campaign_read_id)
            _grant_person_permission(conn, person_permissions, person_id=person_id, permission_id=campaign_write_id)


def downgrade() -> None:
    conn = op.get_bind()

    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("permission_id", sa.Uuid()),
    )
    person_permissions = sa.table(
        "person_permissions",
        sa.column("permission_id", sa.Uuid()),
    )

    campaign_perm_ids = conn.execute(
        sa.select(permissions.c.id).where(permissions.c.key.in_(("crm:campaign:read", "crm:campaign:write")))
    ).scalars().all()
    if not campaign_perm_ids:
        return

    conn.execute(person_permissions.delete().where(person_permissions.c.permission_id.in_(campaign_perm_ids)))
    conn.execute(role_permissions.delete().where(role_permissions.c.permission_id.in_(campaign_perm_ids)))
    conn.execute(permissions.delete().where(permissions.c.id.in_(campaign_perm_ids)))
