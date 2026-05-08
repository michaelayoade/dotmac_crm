"""add zabbix connector type

Revision ID: zh8a9b0c1d2e
Revises: zg7a8b9c0d1e
Create Date: 2026-05-08 14:05:00.000000
"""

from alembic import op

revision = "zh8a9b0c1d2e"
down_revision = "zg7a8b9c0d1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE connectortype ADD VALUE IF NOT EXISTS 'zabbix'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely without recreating the type.
    pass
