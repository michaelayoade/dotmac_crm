"""add chat widget support

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-01-30 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b1c2d3e4f5a6"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add chat_widget to both channel type enums
    op.execute("ALTER TYPE channeltype ADD VALUE IF NOT EXISTS 'chat_widget'")
    # PersonChannel uses same enum name in this codebase

    # Create chat_widget_configs table
    op.create_table(
        "chat_widget_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column(
            "connector_config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connector_configs.id"),
            nullable=True,
        ),
        sa.Column("allowed_domains", postgresql.JSON(), nullable=True),
        sa.Column("primary_color", sa.String(20), server_default="#3B82F6", nullable=False),
        sa.Column("bubble_position", sa.String(20), server_default="bottom-right", nullable=False),
        sa.Column("welcome_message", sa.Text(), nullable=True),
        sa.Column("placeholder_text", sa.String(120), server_default="Type a message...", nullable=False),
        sa.Column("widget_title", sa.String(80), server_default="Chat with us", nullable=False),
        sa.Column("offline_message", sa.Text(), nullable=True),
        sa.Column("prechat_form_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("prechat_fields", postgresql.JSON(), nullable=True),
        sa.Column("business_hours", postgresql.JSON(), nullable=True),
        sa.Column("rate_limit_messages_per_minute", sa.Integer(), server_default="10", nullable=False),
        sa.Column("rate_limit_sessions_per_ip", sa.Integer(), server_default="5", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Create widget_visitor_sessions table
    op.create_table(
        "widget_visitor_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "widget_config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_widget_configs.id"),
            nullable=False,
        ),
        sa.Column("visitor_token", sa.String(64), unique=True, nullable=False),
        sa.Column("fingerprint_hash", sa.String(64), nullable=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("crm_conversations.id"),
            nullable=True,
        ),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("page_url", sa.String(2048), nullable=True),
        sa.Column("referrer_url", sa.String(2048), nullable=True),
        sa.Column("metadata", postgresql.JSON(), nullable=True),
        sa.Column("is_identified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("identified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("identified_email", sa.String(255), nullable=True),
        sa.Column("identified_name", sa.String(160), nullable=True),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Create indexes
    op.create_index(
        "ix_widget_visitor_sessions_visitor_token",
        "widget_visitor_sessions",
        ["visitor_token"],
        unique=True,
    )
    op.create_index(
        "ix_widget_visitor_sessions_fingerprint_hash",
        "widget_visitor_sessions",
        ["fingerprint_hash"],
    )
    op.create_index(
        "ix_widget_visitor_sessions_widget_config_id",
        "widget_visitor_sessions",
        ["widget_config_id"],
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_widget_visitor_sessions_widget_config_id", table_name="widget_visitor_sessions")
    op.drop_index("ix_widget_visitor_sessions_fingerprint_hash", table_name="widget_visitor_sessions")
    op.drop_index("ix_widget_visitor_sessions_visitor_token", table_name="widget_visitor_sessions")

    # Drop tables
    op.drop_table("widget_visitor_sessions")
    op.drop_table("chat_widget_configs")

    # Note: Cannot remove enum values in PostgreSQL without recreation
    # The chat_widget value will remain in the enum
