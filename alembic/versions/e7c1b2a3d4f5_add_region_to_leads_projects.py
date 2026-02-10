"""add region to leads and projects

Revision ID: e7c1b2a3d4f5
Revises: d1e2f3a4b5c6
Create Date: 2026-02-01 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "e7c1b2a3d4f5"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("crm_leads") as batch_op:
        batch_op.add_column(sa.Column("region", sa.String(length=80), nullable=True))

    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("region", sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("region")

    with op.batch_alter_table("crm_leads") as batch_op:
        batch_op.drop_column("region")
