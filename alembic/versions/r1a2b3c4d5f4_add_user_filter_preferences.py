"""Add user filter preferences table.

Revision ID: r1a2b3c4d5f4
Revises: q1a2b3c4d5f3
Create Date: 2026-02-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r1a2b3c4d5f4"
down_revision = "q1a2b3c4d5f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_filter_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("page_key", sa.String(length=120), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("person_id", "page_key", name="uq_user_filter_preferences_person_page"),
    )
    op.create_index(
        "ix_user_filter_preferences_person_id",
        "user_filter_preferences",
        ["person_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_filter_preferences_person_id", table_name="user_filter_preferences")
    op.drop_table("user_filter_preferences")
