"""Add granular billing risk and online last 24h report permissions.

Revision ID: 20260515120000
Revises: 20260513103000, zi9b0c1d2e3f, zj0c1d2e3f4a
Create Date: 2026-05-15 12:00:00.000000

"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "20260515120000"
down_revision = ("20260513103000", "zi9b0c1d2e3f", "zj0c1d2e3f4a")
branch_labels = None
depends_on = None


REPORT_PERMISSIONS = (
    ("reports:billing-risk:read", "View billing risk report"),
    ("reports:billing-risk:write", "Run billing risk report actions"),
    ("reports:online-last-24h:read", "View active/online last 24h subscriber report"),
    ("reports:online-last-24h:write", "Run active/online last 24h subscriber report actions"),
)


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

    for key, description in REPORT_PERMISSIONS:
        _ensure_permission(conn, permissions, key=key, description=description)


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

    permission_keys = tuple(key for key, _description in REPORT_PERMISSIONS)
    permission_ids = conn.execute(sa.select(permissions.c.id).where(permissions.c.key.in_(permission_keys))).scalars().all()

    if permission_ids:
        conn.execute(person_permissions.delete().where(person_permissions.c.permission_id.in_(permission_ids)))
        conn.execute(role_permissions.delete().where(role_permissions.c.permission_id.in_(permission_ids)))
        conn.execute(permissions.delete().where(permissions.c.id.in_(permission_ids)))
