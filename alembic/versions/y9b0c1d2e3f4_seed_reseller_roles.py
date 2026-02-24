"""Seed reseller RBAC roles.

Revision ID: y9b0c1d2e3f4
Revises: x8a9b0c1d2e3
Create Date: 2026-02-17
"""

import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "y9b0c1d2e3f4"
down_revision = "2c1a8e0f4d7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    roles = [
        ("reseller_admin", "Reseller portal administrator"),
        ("reseller_member", "Reseller portal member"),
    ]
    for name, description in roles:
        exists = conn.execute(sa.text("SELECT 1 FROM roles WHERE name = :name"), {"name": name}).first()
        if exists:
            continue
        conn.execute(
            sa.text(
                """
                INSERT INTO roles (id, name, description, is_active, created_at, updated_at)
                VALUES (:id, :name, :description, true, now(), now())
                """
            ),
            {"id": uuid.uuid4(), "name": name, "description": description},
        )


def downgrade() -> None:
    op.execute("DELETE FROM roles WHERE name IN ('reseller_admin', 'reseller_member');")
