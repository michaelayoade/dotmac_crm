"""Add cross_connect value to projecttype enum.

Revision ID: z1c2d3e4f5a6
Revises: y9b0c1d2e3f4
Create Date: 2026-02-18
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "z1c2d3e4f5a6"
down_revision = "y9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'projecttype'
          AND e.enumlabel = 'cross_connect'
    ) THEN
        ALTER TYPE projecttype ADD VALUE 'cross_connect';
    END IF;
END $$;
"""
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values in place.
    pass
