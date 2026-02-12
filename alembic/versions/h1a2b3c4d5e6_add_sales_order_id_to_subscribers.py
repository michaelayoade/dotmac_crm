"""Add sales_order_id to subscribers.

Revision ID: h1a2b3c4d5e6
Revises: g4d5e6f7a8b9
Create Date: 2026-02-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "h1a2b3c4d5e6"
down_revision = "g4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscribers",
        sa.Column("sales_order_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_subscribers_sales_order_id",
        "subscribers",
        "sales_orders",
        ["sales_order_id"],
        ["id"],
    )
    op.create_index("ix_subscribers_sales_order", "subscribers", ["sales_order_id"])


def downgrade() -> None:
    op.drop_index("ix_subscribers_sales_order", table_name="subscribers")
    op.drop_constraint("fk_subscribers_sales_order_id", "subscribers", type_="foreignkey")
    op.drop_column("subscribers", "sales_order_id")
