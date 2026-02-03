"""add lead address and project customer address

Revision ID: fa1b2c3d4e5f
Revises: f9a0b1c2d3e4
Create Date: 2026-02-03 13:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "fa1b2c3d4e5f"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("crm_leads") as batch_op:
        batch_op.add_column(sa.Column("address", sa.Text(), nullable=True))

    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("customer_address", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("customer_address")

    with op.batch_alter_table("crm_leads") as batch_op:
        batch_op.drop_column("address")
