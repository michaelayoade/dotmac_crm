"""Add immutable assignment stints and per-assignment first response metrics.

Revision ID: fr2026072001
Revises: ep2026071901
Create Date: 2026-07-20 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "fr2026072001"
down_revision = "ep2026071901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    conversation_columns = {column["name"] for column in inspector.get_columns("crm_conversations")}
    if "human_handoff_at" not in conversation_columns:
        op.add_column(
            "crm_conversations",
            sa.Column("human_handoff_at", sa.DateTime(timezone=True), nullable=True),
        )

    assignment_columns = {column["name"] for column in inspector.get_columns("crm_conversation_assignments")}
    if "ended_at" not in assignment_columns:
        op.add_column(
            "crm_conversation_assignments",
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "first_response_at" not in assignment_columns:
        op.add_column(
            "crm_conversation_assignments",
            sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "response_time_seconds" not in assignment_columns:
        op.add_column(
            "crm_conversation_assignments",
            sa.Column("response_time_seconds", sa.Integer(), nullable=True),
        )
    if "first_response_message_id" not in assignment_columns:
        op.add_column(
            "crm_conversation_assignments",
            sa.Column("first_response_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_crm_assignment_first_response_message",
            "crm_conversation_assignments",
            "crm_messages",
            ["first_response_message_id"],
            ["id"],
        )

    unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("crm_conversation_assignments")
    }
    if "uq_crm_conversation_assignments" in unique_constraints:
        op.drop_constraint(
            "uq_crm_conversation_assignments",
            "crm_conversation_assignments",
            type_="unique",
        )

    indexes = {index["name"] for index in inspector.get_indexes("crm_conversation_assignments")}
    if "ix_crm_assignments_agent_assigned_at" not in indexes:
        op.create_index(
            "ix_crm_assignments_agent_assigned_at",
            "crm_conversation_assignments",
            ["agent_id", "assigned_at"],
        )
    if "ix_crm_assignments_conversation_assigned_at" not in indexes:
        op.create_index(
            "ix_crm_assignments_conversation_assigned_at",
            "crm_conversation_assignments",
            ["conversation_id", "assigned_at"],
        )

    # Existing inactive rows predate explicit stint closure. Their updated_at is
    # the best available non-fabricated end marker.
    op.execute(
        sa.text(
            """
            UPDATE crm_conversation_assignments
            SET ended_at = updated_at
            WHERE is_active IS FALSE AND ended_at IS NULL
            """
        )
    )

    if bind.dialect.name == "postgresql":
        # Repair the overlap created by the old automation path. Pending AI
        # conversations must have no active assignment; terminal AI rows can
        # safely move their still-active stint start forward to the persisted
        # relinquishment timestamp.
        op.execute(
            sa.text(
                """
                UPDATE crm_conversation_assignments AS assignment
                SET is_active = FALSE,
                    ended_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                FROM crm_conversations AS conversation
                WHERE assignment.conversation_id = conversation.id
                  AND assignment.is_active IS TRUE
                  AND conversation.metadata -> 'ai_intake' ->> 'status'
                      IN ('pending', 'awaiting_customer', 'awaiting_timeout', 'awaiting_profile')
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE crm_conversations
                SET human_handoff_at = CASE metadata -> 'ai_intake' ->> 'status'
                        WHEN 'resolved' THEN (metadata -> 'ai_intake' ->> 'resolved_at')::timestamptz
                        WHEN 'escalated' THEN (metadata -> 'ai_intake' ->> 'escalated_at')::timestamptz
                        WHEN 'human_assigned' THEN
                            (metadata -> 'ai_intake' ->> 'human_assigned_at')::timestamptz
                    END
                WHERE human_handoff_at IS NULL
                  AND metadata -> 'ai_intake' ->> 'status'
                      IN ('resolved', 'escalated', 'human_assigned')
                  AND CASE metadata -> 'ai_intake' ->> 'status'
                        WHEN 'resolved' THEN metadata -> 'ai_intake' ->> 'resolved_at'
                        WHEN 'escalated' THEN metadata -> 'ai_intake' ->> 'escalated_at'
                        WHEN 'human_assigned' THEN metadata -> 'ai_intake' ->> 'human_assigned_at'
                      END IS NOT NULL
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE crm_conversation_assignments AS assignment
                SET assigned_at = GREATEST(
                        assignment.assigned_at,
                        conversation.human_handoff_at
                    ),
                    updated_at = CURRENT_TIMESTAMP
                FROM crm_conversations AS conversation
                WHERE assignment.conversation_id = conversation.id
                  AND assignment.is_active IS TRUE
                  AND assignment.first_response_at IS NULL
                  AND conversation.metadata -> 'ai_intake' ->> 'status'
                      IN ('resolved', 'escalated', 'human_assigned')
                  AND conversation.human_handoff_at IS NOT NULL
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("crm_conversation_assignments")}
    if "ix_crm_assignments_conversation_assigned_at" in indexes:
        op.drop_index(
            "ix_crm_assignments_conversation_assigned_at",
            table_name="crm_conversation_assignments",
        )
    if "ix_crm_assignments_agent_assigned_at" in indexes:
        op.drop_index(
            "ix_crm_assignments_agent_assigned_at",
            table_name="crm_conversation_assignments",
        )

    unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("crm_conversation_assignments")
    }
    if "uq_crm_conversation_assignments" not in unique_constraints:
        op.create_unique_constraint(
            "uq_crm_conversation_assignments",
            "crm_conversation_assignments",
            ["conversation_id", "team_id", "agent_id"],
        )

    assignment_columns = {column["name"] for column in inspector.get_columns("crm_conversation_assignments")}
    if "first_response_message_id" in assignment_columns:
        op.drop_constraint(
            "fk_crm_assignment_first_response_message",
            "crm_conversation_assignments",
            type_="foreignkey",
        )
        op.drop_column("crm_conversation_assignments", "first_response_message_id")
    if "response_time_seconds" in assignment_columns:
        op.drop_column("crm_conversation_assignments", "response_time_seconds")
    if "first_response_at" in assignment_columns:
        op.drop_column("crm_conversation_assignments", "first_response_at")
    if "ended_at" in assignment_columns:
        op.drop_column("crm_conversation_assignments", "ended_at")

    conversation_columns = {column["name"] for column in inspector.get_columns("crm_conversations")}
    if "human_handoff_at" in conversation_columns:
        op.drop_column("crm_conversations", "human_handoff_at")
