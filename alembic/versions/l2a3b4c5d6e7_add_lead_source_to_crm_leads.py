"""add lead_source to crm leads

Revision ID: l2a3b4c5d6e7
Revises: aa7b8c9d0e1f, h8b9c0d1e2f3, h3c4d5e6f7a8, n1a2b3c4d5f1
Create Date: 2026-02-23 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "l2a3b4c5d6e7"
down_revision = ("aa7b8c9d0e1f", "h8b9c0d1e2f3", "h3c4d5e6f7a8", "n1a2b3c4d5f1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("crm_leads") as batch_op:
        batch_op.add_column(sa.Column("lead_source", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("crm_leads") as batch_op:
        batch_op.drop_column("lead_source")
