"""add access_notes to work_orders

Revision ID: fe2026070200
Revises: qp2026063001
Create Date: 2026-07-02

Structured site-access instructions for the field technician (gate code,
call-on-arrival, parking, dog, …).
"""

import sqlalchemy as sa
from alembic import op

revision = "fe2026070200"
down_revision = "qp2026063001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_orders", sa.Column("access_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("work_orders", "access_notes")
