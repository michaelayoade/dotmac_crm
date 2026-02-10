"""add lower and medium priorities

Revision ID: b6c7d8e9f0a1
Revises: fa1b2c3d4e5f
Create Date: 2026-02-07

"""

from alembic import op

revision = "b6c7d8e9f0a1"
down_revision = "fa1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE ticketpriority ADD VALUE IF NOT EXISTS 'lower'")
    op.execute("ALTER TYPE ticketpriority ADD VALUE IF NOT EXISTS 'medium'")
    op.execute("ALTER TYPE workorderpriority ADD VALUE IF NOT EXISTS 'lower'")
    op.execute("ALTER TYPE workorderpriority ADD VALUE IF NOT EXISTS 'medium'")
    op.execute("ALTER TYPE projectpriority ADD VALUE IF NOT EXISTS 'lower'")
    op.execute("ALTER TYPE projectpriority ADD VALUE IF NOT EXISTS 'medium'")
    op.execute("ALTER TYPE taskpriority ADD VALUE IF NOT EXISTS 'lower'")
    op.execute("ALTER TYPE taskpriority ADD VALUE IF NOT EXISTS 'medium'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without type recreation.
    pass
