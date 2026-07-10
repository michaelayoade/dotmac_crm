"""add nin to people

Revision ID: zz3e4f5g6h7i
Revises: so2026070801
Create Date: 2026-07-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "zz3e4f5g6h7i"
down_revision = "so2026070801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("people")} if inspector.has_table("people") else set()
    unique_constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("people")}
    if "nin" not in columns:
        op.add_column("people", sa.Column("nin", sa.String(length=11), nullable=True))
    else:
        op.alter_column("people", "nin", existing_type=sa.String(length=20), type_=sa.String(length=11), existing_nullable=True)
    if "uq_people_nin" in unique_constraints:
        op.drop_constraint("uq_people_nin", "people", type_="unique")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("people")} if inspector.has_table("people") else set()
    if "nin" in columns:
        op.drop_column("people", "nin")
