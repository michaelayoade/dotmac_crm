"""Add erp_person_id to people for ERP identity mapping.

Revision ID: o1a2b3c4d5f2
Revises: n1a2b3c4d5f1
Create Date: 2026-02-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "o1a2b3c4d5f2"
down_revision: str | None = "n1a2b3c4d5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("people", sa.Column("erp_person_id", sa.String(length=100), nullable=True))
    op.create_index("ix_people_erp_person_id", "people", ["erp_person_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_people_erp_person_id", table_name="people")
    op.drop_column("people", "erp_person_id")

