"""create document_sequences and merge heads

Revision ID: d1e2f3a4b5c6
Revises: b1c2d3e4f5a6, c4d5e6f7a8b9
Create Date: 2026-01-30 12:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = ("b1c2d3e4f5a6", "c4d5e6f7a8b9")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_sequences",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.UniqueConstraint("key", name="uq_document_sequences_key"),
    )


def downgrade() -> None:
    op.drop_table("document_sequences")
