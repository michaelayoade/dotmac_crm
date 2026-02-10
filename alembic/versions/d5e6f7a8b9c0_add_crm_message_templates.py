"""add crm message templates

Revision ID: d5e6f7a8b9c0
Revises: d4f7a1b2c3d4
Create Date: 2026-02-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "d5e6f7a8b9c0"
down_revision = "d4f7a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_message_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "channel_type",
            postgresql.ENUM(
                "email",
                "whatsapp",
                "facebook_messenger",
                "instagram_dm",
                "sms",
                "telegram",
                "webchat",
                "phone",
                "chat_widget",
                "note",
                name="channeltype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_crm_message_templates_channel_active",
        "crm_message_templates",
        ["channel_type", "is_active"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_crm_message_templates_channel_active", table_name="crm_message_templates")
    op.drop_table("crm_message_templates")
