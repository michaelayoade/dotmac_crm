"""add crm outbox

Revision ID: d4f7a1b2c3d4
Revises: e467ee5c4c52
Create Date: 2026-02-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "d4f7a1b2c3d4"
down_revision = "e467ee5c4c52"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("crm_conversations.id"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("crm_messages.id"),
            nullable=True,
        ),
        sa.Column("channel_type", sa.Enum("email", "whatsapp", "facebook_messenger", "instagram_dm", "sms", "telegram", "webchat", "phone", "chat_widget", "note", name="channeltype"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSON(), nullable=True),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True, unique=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_crm_outbox_status_next_attempt",
        "crm_outbox",
        ["status", "next_attempt_at"],
    )


def downgrade():
    op.drop_index("ix_crm_outbox_status_next_attempt", table_name="crm_outbox")
    op.drop_table("crm_outbox")
