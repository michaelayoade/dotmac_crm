"""quote line item client_ref for offline idempotency

Revision ID: fe2026070202
Revises: fe2026070201
Create Date: 2026-07-02

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fe2026070202"
down_revision = "fe2026070201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("quote_line_items")}
    if "client_ref" not in cols:
        op.add_column("quote_line_items", sa.Column("client_ref", UUID(as_uuid=True), nullable=True))
        op.create_index("ix_quote_line_items_client_ref", "quote_line_items", ["client_ref"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_quote_line_items_client_ref", table_name="quote_line_items")
    op.drop_column("quote_line_items", "client_ref")
