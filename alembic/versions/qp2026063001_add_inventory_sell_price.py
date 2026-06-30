"""add sell price to inventory items (price-book for self-serve quote estimates)

Revision ID: qp2026063001
Revises: pcr2026062903
Create Date: 2026-06-30 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "qp2026063001"
down_revision = "pcr2026062903"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inventory_items", sa.Column("unit_price", sa.Numeric(12, 2), nullable=True))
    op.add_column("inventory_items", sa.Column("currency", sa.String(length=3), nullable=True))


def downgrade() -> None:
    op.drop_column("inventory_items", "currency")
    op.drop_column("inventory_items", "unit_price")
