"""add ticket statuses waiting, lastmile, site under construction

Revision ID: c1d2e3f4a5b6
Revises: b6c7d8e9f0a1
Create Date: 2026-02-07

"""

from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE ticketstatus ADD VALUE IF NOT EXISTS 'waiting_on_customer'")
    op.execute("ALTER TYPE ticketstatus ADD VALUE IF NOT EXISTS 'lastmile_rerun'")
    op.execute("ALTER TYPE ticketstatus ADD VALUE IF NOT EXISTS 'site_under_construction'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without type recreation.
    pass
