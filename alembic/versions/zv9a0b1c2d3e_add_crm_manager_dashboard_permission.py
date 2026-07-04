"""Add CRM manager dashboard permission.

Revision ID: zv9a0b1c2d3e
Revises: fe2026070202
Create Date: 2026-07-03 00:00:00.000000
"""

from __future__ import annotations

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "zv9a0b1c2d3e"
down_revision = "fe2026070202"
branch_labels = None
depends_on = None

PERMISSION_KEY = "crm:inbox:manager_dashboard:read"
PERMISSION_DESCRIPTION = "View the Customer Relations Manager inbox dashboard"
AISHA_EMAIL = "i.aisha@dotmac.ng"


def _tables() -> tuple[sa.Table, sa.Table, sa.Table]:
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    people = sa.table(
        "people",
        sa.column("id", sa.Uuid()),
        sa.column("email", sa.String()),
    )
    person_permissions = sa.table(
        "person_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("person_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("granted_at", sa.DateTime(timezone=True)),
        sa.column("granted_by_person_id", sa.Uuid()),
    )
    return permissions, people, person_permissions


def upgrade() -> None:
    conn = op.get_bind()
    permissions, people, person_permissions = _tables()

    permission_id = conn.execute(
        sa.select(permissions.c.id).where(permissions.c.key == PERMISSION_KEY)
    ).scalar_one_or_none()
    if permission_id is None:
        permission_id = uuid4()
        conn.execute(
            permissions.insert().values(
                id=permission_id,
                key=PERMISSION_KEY,
                description=PERMISSION_DESCRIPTION,
                is_active=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    aisha_id = conn.execute(
        sa.select(people.c.id).where(sa.func.lower(people.c.email) == AISHA_EMAIL)
    ).scalar_one_or_none()
    if aisha_id is None:
        return

    existing_grant_id = conn.execute(
        sa.select(person_permissions.c.id)
        .where(person_permissions.c.person_id == aisha_id)
        .where(person_permissions.c.permission_id == permission_id)
    ).scalar_one_or_none()
    if existing_grant_id is None:
        conn.execute(
            person_permissions.insert().values(
                id=uuid4(),
                person_id=aisha_id,
                permission_id=permission_id,
                granted_at=sa.func.now(),
                granted_by_person_id=None,
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    permissions, _people, person_permissions = _tables()

    permission_id = conn.execute(
        sa.select(permissions.c.id).where(permissions.c.key == PERMISSION_KEY)
    ).scalar_one_or_none()
    if permission_id is None:
        return

    conn.execute(person_permissions.delete().where(person_permissions.c.permission_id == permission_id))
    conn.execute(permissions.delete().where(permissions.c.id == permission_id))
