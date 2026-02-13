"""add vendor erp_id

Revision ID: l1a2b3c4d5e9
Revises: k1a2b3c4d5e8
Create Date: 2026-02-13 18:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "l1a2b3c4d5e9"
down_revision = "k1a2b3c4d5e8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("vendors", sa.Column("erp_id", sa.String(100), nullable=True))
    op.create_index("ix_vendors_erp_id", "vendors", ["erp_id"], unique=True)


def downgrade():
    op.drop_index("ix_vendors_erp_id", table_name="vendors")
    op.drop_column("vendors", "erp_id")
