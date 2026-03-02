"""add dialog flow to chat widgets

Revision ID: v7a8b9c0d1e2
Revises: u6a7b8c9d0e1
Create Date: 2026-03-02 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "u6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_widget_configs",
        sa.Column("dialog_flow_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "chat_widget_configs",
        sa.Column("dialog_flow_steps", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_widget_configs", "dialog_flow_steps")
    op.drop_column("chat_widget_configs", "dialog_flow_enabled")
