"""add crm conversation summaries

Revision ID: 20260414102000
Revises: 20260414100000
Create Date: 2026-04-14 10:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260414102000"
down_revision: str | None = "20260414100000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "crm_conversation_summaries" in inspector.get_table_names():
        return

    uuid_type = postgresql.UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.String(length=36)
    enum_factory = postgresql.ENUM if bind.dialect.name == "postgresql" else sa.Enum
    channel_type = enum_factory(
        "email",
        "whatsapp",
        "facebook_messenger",
        "instagram_dm",
        "note",
        "chat_widget",
        name="channeltype",
        create_type=False,
    )
    status_type = enum_factory(
        "open", "pending", "resolved", "snoozed", name="conversationstatus", create_type=False
    )
    priority_type = enum_factory(
        "low", "medium", "high", "urgent", "none", name="conversationpriority", create_type=False
    )

    op.create_table(
        "crm_conversation_summaries",
        sa.Column("conversation_id", uuid_type, sa.ForeignKey("crm_conversations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("person_id", uuid_type, sa.ForeignKey("people.id"), nullable=False),
        sa.Column("latest_message_id", uuid_type, sa.ForeignKey("crm_messages.id"), nullable=True),
        sa.Column("latest_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_outbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_failed_outbox", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("primary_channel_type", channel_type, nullable=True),
        sa.Column("active_assignment_agent_id", uuid_type, sa.ForeignKey("crm_agents.id"), nullable=True),
        sa.Column("active_assignment_team_id", uuid_type, sa.ForeignKey("crm_teams.id"), nullable=True),
        sa.Column("needs_attention", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unreplied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", status_type, nullable=False),
        sa.Column("priority", priority_type, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "idx_crm_conv_summaries_active_status",
        "crm_conversation_summaries",
        ["is_active", "status", "latest_message_at"],
    )
    op.create_index(
        "idx_crm_conv_summaries_assignment",
        "crm_conversation_summaries",
        ["active_assignment_agent_id", "active_assignment_team_id"],
    )
    op.create_index(
        "idx_crm_conv_summaries_needs_attention",
        "crm_conversation_summaries",
        ["needs_attention", "latest_message_at"],
    )
    op.create_index(
        "idx_crm_conv_summaries_unreplied",
        "crm_conversation_summaries",
        ["unreplied", "latest_message_at"],
    )
    op.create_index("idx_crm_conv_summaries_unread", "crm_conversation_summaries", ["unread_count"])
    op.create_index(
        "idx_crm_conv_summaries_channel",
        "crm_conversation_summaries",
        ["primary_channel_type", "latest_message_at"],
    )

    if bind.dialect.name == "postgresql":
        op.execute(
            """
            insert into crm_conversation_summaries (
                conversation_id,
                person_id,
                latest_message_id,
                latest_message_at,
                latest_inbound_at,
                latest_outbound_at,
                unread_count,
                has_failed_outbox,
                primary_channel_type,
                active_assignment_agent_id,
                active_assignment_team_id,
                needs_attention,
                unreplied,
                status,
                priority,
                is_active,
                updated_at
            )
            select
                c.id,
                c.person_id,
                lm.id,
                coalesce(lm.received_at, lm.sent_at, lm.created_at, c.last_message_at),
                inbound.latest_inbound_at,
                outbound.latest_outbound_at,
                coalesce(unread.unread_count, 0),
                coalesce(failed.has_failed_outbox, false),
                lm.channel_type,
                assign.agent_id,
                assign.team_id,
                (
                    c.status <> 'resolved'::conversationstatus
                    and inbound.latest_inbound_at is not null
                    and outbound.latest_outbound_at is not null
                    and inbound.latest_inbound_at > outbound.latest_outbound_at
                ),
                (
                    c.status <> 'resolved'::conversationstatus
                    and inbound.latest_inbound_at is not null
                    and outbound.latest_outbound_at is null
                ),
                c.status,
                c.priority,
                c.is_active,
                now()
            from crm_conversations c
            left join lateral (
                select m.id, m.channel_type, m.received_at, m.sent_at, m.created_at
                from crm_messages m
                where m.conversation_id = c.id
                order by coalesce(m.received_at, m.sent_at, m.created_at) desc
                limit 1
            ) lm on true
            left join lateral (
                select max(coalesce(m.received_at, m.sent_at, m.created_at)) as latest_inbound_at
                from crm_messages m
                where m.conversation_id = c.id and m.direction = 'inbound'::messagedirection
            ) inbound on true
            left join lateral (
                select max(coalesce(m.received_at, m.sent_at, m.created_at)) as latest_outbound_at
                from crm_messages m
                where m.conversation_id = c.id and m.direction = 'outbound'::messagedirection
            ) outbound on true
            left join lateral (
                select count(*)::integer as unread_count
                from crm_messages m
                where m.conversation_id = c.id
                  and m.direction = 'inbound'::messagedirection
                  and m.status = 'received'::messagestatus
                  and m.read_at is null
            ) unread on true
            left join lateral (
                select true as has_failed_outbox
                from crm_outbox o
                where o.conversation_id = c.id and o.status = 'failed'
                limit 1
            ) failed on true
            left join lateral (
                select ca.agent_id, ca.team_id
                from crm_conversation_assignments ca
                where ca.conversation_id = c.id and ca.is_active is true
                order by ca.assigned_at desc nulls last, ca.created_at desc
                limit 1
            ) assign on true
            on conflict (conversation_id) do nothing
            """
        )


def downgrade() -> None:
    op.drop_table("crm_conversation_summaries")
