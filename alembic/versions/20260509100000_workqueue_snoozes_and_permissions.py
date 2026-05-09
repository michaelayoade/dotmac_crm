"""workqueue snoozes table and permissions

Revision ID: 20260509100000
Revises: 20260508133000
Create Date: 2026-05-09 10:00:00.000000

"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "20260509100000"
down_revision = "20260508133000"
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
    existing_id = conn.execute(
        sa.select(permissions.c.id).where(permissions.c.key == key)
    ).scalar_one_or_none()
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
    # --- workqueue_snoozes table ---
    op.create_table(
        "workqueue_snoozes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "item_kind",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("snooze_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("until_next_reply", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "item_kind", "item_id", name="uq_workqueue_snooze_user_item"),
    )
    op.create_index("ix_workqueue_snooze_user_id", "workqueue_snoozes", ["user_id"])
    op.create_index("ix_workqueue_snooze_user_until", "workqueue_snoozes", ["user_id", "snooze_until"])

    # --- permissions seed ---
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

    _ensure_permission(conn, permissions, key="workqueue:view", description="View the Workqueue surface")
    _ensure_permission(conn, permissions, key="workqueue:claim", description="Claim items from the Workqueue")
    _ensure_permission(conn, permissions, key="workqueue:audience:team", description="View team-scoped Workqueue items")
    _ensure_permission(conn, permissions, key="workqueue:audience:org", description="View org-scoped Workqueue items")


def downgrade() -> None:
    # --- remove permission rows ---
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

    workqueue_keys = (
        "workqueue:view",
        "workqueue:claim",
        "workqueue:audience:team",
        "workqueue:audience:org",
    )
    perm_ids = conn.execute(
        sa.select(permissions.c.id).where(permissions.c.key.in_(workqueue_keys))
    ).scalars().all()

    if perm_ids:
        conn.execute(person_permissions.delete().where(person_permissions.c.permission_id.in_(perm_ids)))
        conn.execute(role_permissions.delete().where(role_permissions.c.permission_id.in_(perm_ids)))
        conn.execute(permissions.delete().where(permissions.c.id.in_(perm_ids)))

    # --- drop indices and table ---
    op.drop_index("ix_workqueue_snooze_user_until", table_name="workqueue_snoozes")
    op.drop_index("ix_workqueue_snooze_user_id", table_name="workqueue_snoozes")
    op.drop_table("workqueue_snoozes")
