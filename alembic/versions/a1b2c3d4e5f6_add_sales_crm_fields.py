"""add sales CRM fields

Revision ID: a1b2c3d4e5f6
Revises: 9c7e3c4e8b12
Create Date: 2026-01-28 14:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "9c7e3c4e8b12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add sales fields to crm_leads table
    op.add_column(
        "crm_leads",
        sa.Column("probability", sa.Integer(), nullable=True),
    )
    op.add_column(
        "crm_leads",
        sa.Column("expected_close_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "crm_leads",
        sa.Column("lost_reason", sa.String(200), nullable=True),
    )

    # Add default_probability to crm_pipeline_stages table
    op.add_column(
        "crm_pipeline_stages",
        sa.Column("default_probability", sa.Integer(), nullable=True, server_default="50"),
    )

    # Update existing rows to have default value
    op.execute("UPDATE crm_pipeline_stages SET default_probability = 50 WHERE default_probability IS NULL")

    # Make the column NOT NULL after setting defaults
    op.alter_column("crm_pipeline_stages", "default_probability", nullable=False)


def downgrade() -> None:
    op.drop_column("crm_leads", "probability")
    op.drop_column("crm_leads", "expected_close_date")
    op.drop_column("crm_leads", "lost_reason")
    op.drop_column("crm_pipeline_stages", "default_probability")
