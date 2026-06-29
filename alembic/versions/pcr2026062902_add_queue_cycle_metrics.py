"""add queue cycle metrics

Revision ID: pcr2026062902
Revises: pcr2026062901
Create Date: 2026-06-29 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "pcr2026062902"
down_revision = "pcr2026062901"
branch_labels = None
depends_on = None


def _columns(inspector, table):
    return {col["name"] for col in inspector.get_columns(table)}


def _indexes(inspector, table):
    return {ix["name"] for ix in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    conv_cols = _columns(inspector, "crm_conversations")

    if "last_queued_at" not in conv_cols:
        op.add_column("crm_conversations", sa.Column("last_queued_at", sa.DateTime(timezone=True), nullable=True))
    if "last_queue_assigned_at" not in conv_cols:
        op.add_column(
            "crm_conversations", sa.Column("last_queue_assigned_at", sa.DateTime(timezone=True), nullable=True)
        )
    if "last_queue_wait_seconds" not in conv_cols:
        op.add_column("crm_conversations", sa.Column("last_queue_wait_seconds", sa.Integer(), nullable=True))

    conv_idx = _indexes(inspector, "crm_conversations")
    if "ix_crm_conversations_last_queue_assigned_at" not in conv_idx:
        op.create_index(
            "ix_crm_conversations_last_queue_assigned_at",
            "crm_conversations",
            ["last_queue_assigned_at"],
        )

    # Backfill completed queue cycles where the legacy timestamps are coherent.
    op.execute(
        """
        UPDATE crm_conversations
        SET
            last_queued_at = queued_at,
            last_queue_assigned_at = first_assigned_at,
            last_queue_wait_seconds = EXTRACT(EPOCH FROM (first_assigned_at - queued_at))::integer
        WHERE queued_at IS NOT NULL
          AND first_assigned_at IS NOT NULL
          AND first_assigned_at >= queued_at
          AND last_queue_assigned_at IS NULL
        """
    )

    # Rows that are already assigned to an agent are not currently waiting.
    # Preserve a completed wait cycle when the active assignment timestamp can
    # be paired with the queued timestamp, then clear queued_at.
    op.execute(
        """
        UPDATE crm_conversations c
        SET
            last_queued_at = c.queued_at,
            last_queue_assigned_at = ca.assigned_at,
            last_queue_wait_seconds = EXTRACT(EPOCH FROM (ca.assigned_at - c.queued_at))::integer,
            queued_at = NULL
        FROM crm_conversation_assignments ca
        WHERE ca.conversation_id = c.id
          AND ca.is_active IS TRUE
          AND ca.agent_id IS NOT NULL
          AND c.queued_at IS NOT NULL
          AND ca.assigned_at IS NOT NULL
          AND ca.assigned_at >= c.queued_at
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    conv_idx = _indexes(inspector, "crm_conversations")
    if "ix_crm_conversations_last_queue_assigned_at" in conv_idx:
        op.drop_index("ix_crm_conversations_last_queue_assigned_at", table_name="crm_conversations")

    conv_cols = _columns(inspector, "crm_conversations")
    if "last_queue_wait_seconds" in conv_cols:
        op.drop_column("crm_conversations", "last_queue_wait_seconds")
    if "last_queue_assigned_at" in conv_cols:
        op.drop_column("crm_conversations", "last_queue_assigned_at")
    if "last_queued_at" in conv_cols:
        op.drop_column("crm_conversations", "last_queued_at")
