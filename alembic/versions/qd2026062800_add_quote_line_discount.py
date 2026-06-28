"""add discount_percent to crm_quote_line_items

Revision ID: qd2026062800
Revises: qt2026062800
Create Date: 2026-06-28

Adds a per-line discount percent to quote line items; the line amount is stored
net of the discount.
"""

import sqlalchemy as sa
from alembic import op

revision = "qd2026062800"
down_revision = "qt2026062800"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_quote_line_items",
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("crm_quote_line_items", "discount_percent")
