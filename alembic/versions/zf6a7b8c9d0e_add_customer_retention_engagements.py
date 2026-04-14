"""add customer retention engagements

Revision ID: zf6a7b8c9d0e
Revises: 20260413123000
Create Date: 2026-04-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zf6a7b8c9d0e"
down_revision = "20260413123000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_retention_engagements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_external_id", sa.String(length=120), nullable=False),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=80), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("follow_up_date", sa.Date(), nullable=True),
        sa.Column("rep_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rep_label", sa.String(length=255), nullable=True),
        sa.Column("created_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["rep_person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_customer_retention_customer_external",
        "customer_retention_engagements",
        ["customer_external_id"],
    )
    op.create_index(
        "ix_customer_retention_follow_up_date",
        "customer_retention_engagements",
        ["follow_up_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_customer_retention_follow_up_date", table_name="customer_retention_engagements")
    op.drop_index("ix_customer_retention_customer_external", table_name="customer_retention_engagements")
    op.drop_table("customer_retention_engagements")
