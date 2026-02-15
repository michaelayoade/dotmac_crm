"""add performance to settingdomain enum

Revision ID: 75b4f3e2c1d0
Revises: 64d2bd11faca
Create Date: 2026-02-14 16:38:30.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "75b4f3e2c1d0"
down_revision = "64d2bd11faca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres enum types can't be altered transactionally in older versions; Alembic will
    # still run this in a transaction by default, but Postgres permits ADD VALUE.
    op.execute(
        """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'settingdomain'
          AND e.enumlabel = 'performance'
    ) THEN
        ALTER TYPE settingdomain ADD VALUE 'performance';
    END IF;
END $$;
"""
    )


def downgrade() -> None:
    # Enum values cannot be removed safely; no-op.
    pass

