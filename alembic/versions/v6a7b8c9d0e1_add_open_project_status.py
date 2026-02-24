"""Add open value to project status enum and set project default status to open.

Revision ID: v6a7b8c9d0e1
Revises: u5a6b7c8d9e0
Create Date: 2026-02-17 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "v6a7b8c9d0e1"
down_revision = "u5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'open' BEFORE 'planned'")
    op.execute(
        """
        UPDATE domain_settings
        SET value_text = 'open'
        WHERE domain = 'projects'::settingdomain
          AND key = 'default_project_status'
          AND value_type = 'string'::settingvaluetype
          AND (value_text IS NULL OR value_text = 'planned')
        """
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values in-place.
    pass
