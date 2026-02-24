"""add_site_role_to_olt_devices

Revision ID: 2c1a8e0f4d7b
Revises: 08c2b28e5407, x8a9b0c1d2e3
Create Date: 2026-02-17

"""

from alembic import op
import sqlalchemy as sa


revision = "2c1a8e0f4d7b"
down_revision = ("08c2b28e5407", "x8a9b0c1d2e3")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "olt_devices",
        sa.Column("site_role", sa.String(length=32), nullable=False, server_default=sa.text("'olt'")),
    )


def downgrade() -> None:
    op.drop_column("olt_devices", "site_role")

