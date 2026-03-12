"""Add as-built line items table.

Revision ID: z2d3e4f5a6b7
Revises: z1c2d3e4f5a6
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "z2d3e4f5a6b7"
down_revision = "z1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "as_built_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_built_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_type", sa.String(length=80), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cable_type", sa.String(length=120), nullable=True),
        sa.Column("fiber_count", sa.Integer(), nullable=True),
        sa.Column("splice_count", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["as_built_id"], ["as_built_routes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_as_built_line_items_as_built_id"), "as_built_line_items", ["as_built_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_as_built_line_items_as_built_id"), table_name="as_built_line_items")
    op.drop_table("as_built_line_items")
