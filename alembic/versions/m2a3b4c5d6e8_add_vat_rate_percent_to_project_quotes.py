"""add vat rate percent to project quotes

Revision ID: m2a3b4c5d6e8
Revises: j1a2b3c4d5e7
Create Date: 2026-02-24 10:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "m2a3b4c5d6e8"
down_revision = "j1a2b3c4d5e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_quotes", sa.Column("vat_rate_percent", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("project_quotes", "vat_rate_percent")
