"""add crm conversation label catalog

Revision ID: u6a7b8c9d0e1
Revises: t5b6c7d8e9f0
Create Date: 2026-02-25 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "u6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "t5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crm_conversation_labels",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("color", sa.String(length=32), nullable=False, server_default="slate"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_crm_conversation_labels_name"),
        sa.UniqueConstraint("slug", name="uq_crm_conversation_labels_slug"),
    )


def downgrade() -> None:
    op.drop_table("crm_conversation_labels")
