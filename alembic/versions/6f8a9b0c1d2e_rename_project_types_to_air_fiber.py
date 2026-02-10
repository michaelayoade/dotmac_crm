"""rename project types to air fiber

Revision ID: 6f8a9b0c1d2e
Revises: 317cf417ff24
Create Date: 2026-02-01 00:00:00.000000

"""

from alembic import op

revision = "6f8a9b0c1d2e"
down_revision = "317cf417ff24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE projecttype RENAME VALUE 'radio_installation' TO 'air_fiber_installation'")
    op.execute("ALTER TYPE projecttype RENAME VALUE 'radio_fiber_relocation' TO 'air_fiber_relocation'")

    op.execute(
        """
        UPDATE crm_quotes
        SET metadata = jsonb_set(metadata::jsonb, '{project_type}', '"air_fiber_installation"', true)::json
        WHERE metadata->>'project_type' = 'radio_installation'
        """
    )
    op.execute(
        """
        UPDATE crm_quotes
        SET metadata = jsonb_set(metadata::jsonb, '{project_type}', '"air_fiber_relocation"', true)::json
        WHERE metadata->>'project_type' = 'radio_fiber_relocation'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TYPE projecttype RENAME VALUE 'air_fiber_installation' TO 'radio_installation'")
    op.execute("ALTER TYPE projecttype RENAME VALUE 'air_fiber_relocation' TO 'radio_fiber_relocation'")

    op.execute(
        """
        UPDATE crm_quotes
        SET metadata = jsonb_set(metadata::jsonb, '{project_type}', '"radio_installation"', true)::json
        WHERE metadata->>'project_type' = 'air_fiber_installation'
        """
    )
    op.execute(
        """
        UPDATE crm_quotes
        SET metadata = jsonb_set(metadata::jsonb, '{project_type}', '"radio_fiber_relocation"', true)::json
        WHERE metadata->>'project_type' = 'air_fiber_relocation'
        """
    )
