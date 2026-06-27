"""add agent_greeting_enabled to chat_widget_configs

Revision ID: wa1b2c3d4e5f
Revises: merge2026062601
Create Date: 2026-06-27 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "wa1b2c3d4e5f"
down_revision = "merge2026062601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("chat_widget_configs")}
    if "agent_greeting_enabled" in columns:
        return
    op.add_column(
        "chat_widget_configs",
        sa.Column("agent_greeting_enabled", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("chat_widget_configs")}
    if "agent_greeting_enabled" not in columns:
        return
    op.drop_column("chat_widget_configs", "agent_greeting_enabled")
