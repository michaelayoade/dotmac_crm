"""Add erp_customer_id to people table.

Revision ID: g4d5e6f7a8b9
Revises: g3c4d5e6f7a8
Create Date: 2026-02-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "g4d5e6f7a8b9"
down_revision = "g3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("people", sa.Column("erp_customer_id", sa.String(100), nullable=True))
    op.create_unique_constraint("uq_people_erp_customer_id", "people", ["erp_customer_id"])
    op.create_index("ix_people_erp_customer_id", "people", ["erp_customer_id"])


def downgrade() -> None:
    op.drop_index("ix_people_erp_customer_id", table_name="people")
    op.drop_constraint("uq_people_erp_customer_id", "people", type_="unique")
    op.drop_column("people", "erp_customer_id")
