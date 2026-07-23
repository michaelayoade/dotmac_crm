"""add authoritative CRM response obligations

Revision ID: rp2026072101
Revises: so2026070801
Create Date: 2026-07-21 15:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "rp2026072101"
down_revision = "fr2026072001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("crm_response_obligations"):
        return

    uuid_type = postgresql.UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.String(36)
    op.create_table(
        "crm_response_obligations",
        sa.Column("conversation_id", uuid_type, nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("trigger_message_id", uuid_type, nullable=True),
        sa.Column("latest_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_outbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("breached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_agent_id", uuid_type, nullable=True),
        sa.Column("owner_team_id", uuid_type, nullable=True),
        sa.Column("owner_scope", sa.String(length=80), nullable=False),
        sa.Column("escalation_level", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_escalation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["crm_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trigger_message_id"], ["crm_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_agent_id"], ["crm_agents.id"]),
        sa.ForeignKeyConstraint(["owner_team_id"], ["crm_teams.id"]),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index(
        "ix_crm_response_obligations_due",
        "crm_response_obligations",
        ["state", "next_escalation_at"],
    )
    op.create_index(
        "ix_crm_response_obligations_owner",
        "crm_response_obligations",
        ["owner_agent_id", "owner_team_id"],
    )
    op.create_index(
        "ix_crm_response_obligations_reconciled",
        "crm_response_obligations",
        ["reconciled_at"],
    )

    # Backfill production in one set-based pass. Non-PostgreSQL development
    # databases are repaired by the bounded reconciler after startup.
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                WITH latest_inbound AS (
                    SELECT DISTINCT ON (m.conversation_id)
                        m.conversation_id,
                        m.id AS message_id,
                        coalesce(m.received_at, m.sent_at, m.created_at) AS activity_at
                    FROM crm_messages m
                    WHERE m.direction = 'inbound'
                    ORDER BY m.conversation_id,
                             coalesce(m.received_at, m.sent_at, m.created_at) DESC,
                             m.created_at DESC,
                             m.id DESC
                ),
                latest_meaningful_outbound AS (
                    SELECT DISTINCT ON (m.conversation_id)
                        m.conversation_id,
                        coalesce(m.received_at, m.sent_at, m.created_at) AS activity_at
                    FROM crm_messages m
                    WHERE m.direction = 'outbound'
                      AND m.status IN ('sent', 'delivered', 'read')
                      AND coalesce(m.metadata->>'response_obligation_exempt', 'false') <> 'true'
                      AND coalesce(m.metadata->>'ai_intake_generated', 'false') <> 'true'
                    ORDER BY m.conversation_id,
                             coalesce(m.received_at, m.sent_at, m.created_at) DESC,
                             m.created_at DESC,
                             m.id DESC
                ),
                active_assignment AS (
                    SELECT DISTINCT ON (a.conversation_id)
                        a.conversation_id, a.agent_id, a.team_id
                    FROM crm_conversation_assignments a
                    WHERE a.is_active IS TRUE
                    ORDER BY a.conversation_id,
                             a.assigned_at DESC NULLS LAST,
                             a.created_at DESC
                ),
                source AS (
                    SELECT
                        c.id AS conversation_id,
                        c.is_active,
                        c.status::text AS status,
                        coalesce(c.priority::text, 'none') AS priority,
                        i.message_id,
                        i.activity_at AS inbound_at,
                        o.activity_at AS outbound_at,
                        a.agent_id,
                        a.team_id
                    FROM crm_conversations c
                    LEFT JOIN latest_inbound i ON i.conversation_id = c.id
                    LEFT JOIN latest_meaningful_outbound o ON o.conversation_id = c.id
                    LEFT JOIN active_assignment a ON a.conversation_id = c.id
                ),
                decisions AS (
                    SELECT *,
                        CASE
                            WHEN is_active IS FALSE OR status IN ('resolved', 'resolved_to_ticket') THEN 'resolved'
                            WHEN status = 'snoozed' THEN 'snoozed'
                            WHEN inbound_at IS NULL THEN 'no_customer_message'
                            WHEN outbound_at IS NULL THEN 'awaiting_first_response'
                            WHEN inbound_at >= outbound_at THEN 'awaiting_follow_up'
                            ELSE 'responded'
                        END AS response_state
                    FROM source
                )
                INSERT INTO crm_response_obligations (
                    conversation_id, state, trigger_message_id,
                    latest_inbound_at, latest_outbound_at, response_due_at,
                    responded_at, owner_agent_id, owner_team_id, owner_scope,
                    escalation_level, next_escalation_at,
                    reconciled_at, created_at, updated_at
                )
                SELECT
                    conversation_id,
                    response_state,
                    CASE WHEN response_state IN ('awaiting_first_response', 'awaiting_follow_up')
                         THEN message_id END,
                    inbound_at,
                    outbound_at,
                    CASE WHEN response_state IN ('awaiting_first_response', 'awaiting_follow_up') THEN
                        inbound_at + make_interval(mins => CASE priority
                            WHEN 'urgent' THEN 60
                            WHEN 'high' THEN 240
                            WHEN 'medium' THEN 480
                            ELSE 1440
                        END)
                    END,
                    CASE WHEN response_state = 'responded' THEN outbound_at END,
                    agent_id,
                    team_id,
                    CASE
                        WHEN agent_id IS NOT NULL THEN 'agent:' || agent_id::text
                        WHEN team_id IS NOT NULL THEN 'team:' || team_id::text
                        ELSE 'unassigned'
                    END,
                    0,
                    CASE WHEN response_state IN ('awaiting_first_response', 'awaiting_follow_up') THEN
                        inbound_at + interval '5 minutes'
                    END,
                    now(), now(), now()
                FROM decisions
                """
            )
        )


def downgrade() -> None:
    op.drop_index("ix_crm_response_obligations_reconciled", table_name="crm_response_obligations")
    op.drop_index("ix_crm_response_obligations_owner", table_name="crm_response_obligations")
    op.drop_index("ix_crm_response_obligations_due", table_name="crm_response_obligations")
    op.drop_table("crm_response_obligations")
