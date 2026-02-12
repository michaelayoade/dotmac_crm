"""Add webhook_dead_letters table.

Revision ID: h4d5e6f7a8b9
Revises: h3c4d5e6f7a8
Create Date: 2026-02-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "h4d5e6f7a8b9"
down_revision: str | None = "h3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_dead_letters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("message_id", sa.String(200), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_webhook_dead_letters_channel_created",
        "webhook_dead_letters",
        ["channel", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_dead_letters_channel_created", table_name="webhook_dead_letters")
    op.drop_table("webhook_dead_letters")
