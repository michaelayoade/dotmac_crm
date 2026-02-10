"""add ticket type to tickets

Revision ID: 7c9d0e1f2a3b
Revises: 6f8a9b0c1d2e
Create Date: 2026-02-01 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "7c9d0e1f2a3b"
down_revision = "6f8a9b0c1d2e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch_op:
        batch_op.add_column(sa.Column("ticket_type", sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch_op:
        batch_op.drop_column("ticket_type")
