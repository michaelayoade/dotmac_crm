"""work order access tokens: customer "Track My Visit" magic-link

Revision ID: b7d1e2f3a4c5
Revises: qb3c4d5e6f7a
Create Date: 2026-06-27 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7d1e2f3a4c5"
down_revision = "qb3c4d5e6f7a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "work_order_access_tokens" not in existing_tables:
        op.create_table(
            "work_order_access_tokens",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("token", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_work_order_access_tokens_token", "work_order_access_tokens", ["token"], unique=True
        )
        op.create_index(
            "ix_work_order_access_tokens_work_order_id", "work_order_access_tokens", ["work_order_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "work_order_access_tokens" in existing_tables:
        op.drop_index("ix_work_order_access_tokens_work_order_id", table_name="work_order_access_tokens")
        op.drop_index("ix_work_order_access_tokens_token", table_name="work_order_access_tokens")
        op.drop_table("work_order_access_tokens")
