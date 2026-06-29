"""Remove postpaid report viewer role.

Revision ID: pcr2026062901
Revises: pcr2026062900
Create Date: 2026-06-29 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "pcr2026062901"
down_revision = "pcr2026062900"
branch_labels = None
depends_on = None

ROLE_NAME = "postpaid_report_viewer"


def upgrade() -> None:
    conn = op.get_bind()
    roles = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("name", sa.String()))
    role_permissions = sa.table("role_permissions", sa.column("role_id", sa.Uuid()))
    person_roles = sa.table("person_roles", sa.column("role_id", sa.Uuid()))

    role_id = conn.execute(sa.select(roles.c.id).where(roles.c.name == ROLE_NAME)).scalar_one_or_none()
    if not role_id:
        return
    conn.execute(person_roles.delete().where(person_roles.c.role_id == role_id))
    conn.execute(role_permissions.delete().where(role_permissions.c.role_id == role_id))
    conn.execute(roles.delete().where(roles.c.id == role_id))


def downgrade() -> None:
    pass
