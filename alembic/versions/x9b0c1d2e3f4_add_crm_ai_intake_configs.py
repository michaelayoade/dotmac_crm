"""add crm ai intake configs

Revision ID: x9b0c1d2e3f4
Revises: w8b9c0d1e2f3
Create Date: 2026-03-02 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "x9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "w8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    channel_type = postgresql.ENUM(
        "email",
        "whatsapp",
        "facebook_messenger",
        "instagram_dm",
        "note",
        "chat_widget",
        name="channeltype",
        create_type=False,
    )
    op.create_table(
        "crm_ai_intake_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("channel_type", channel_type, nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("confidence_threshold", sa.Float(), nullable=False, server_default=sa.text("0.75")),
        sa.Column("allow_followup_questions", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("max_clarification_turns", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("escalate_after_minutes", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("exclude_campaign_attribution", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("fallback_team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("department_mappings", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_key", name="uq_crm_ai_intake_configs_scope_key"),
    )


def downgrade() -> None:
    op.drop_table("crm_ai_intake_configs")
