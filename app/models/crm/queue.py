"""Durable logical queue state for CRM conversations.

``ConversationAssignment`` remains the history of human responsibility.  This
model owns the customer-facing Support/Sales queue cycle, including cycles that
are requeued or transferred before a human replies.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.crm.enums import ConversationQueueState, ConversationQueueType


class ConversationQueueEntry(Base):
    __tablename__ = "crm_conversation_queue_entries"
    __table_args__ = (
        # A resolved conversation may receive a later inbound message and start
        # another cycle, but never has two live queue cycles at once.
        Index(
            "uq_crm_conversation_one_live_queue_entry",
            "conversation_id",
            unique=True,
            sqlite_where=text("state IN ('classifying', 'waiting', 'assigned')"),
            postgresql_where=text("state IN ('classifying', 'waiting', 'assigned')"),
        ),
        Index("ix_crm_queue_waiting_fifo", "queue_type", "state", "original_arrival_at", "id"),
        Index("ix_crm_queue_assigned_agent", "current_agent_id", "state", "assigned_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_conversations.id"), nullable=False
    )
    queue_type: Mapped[ConversationQueueType] = mapped_column(Enum(ConversationQueueType), nullable=False)
    state: Mapped[ConversationQueueState] = mapped_column(
        Enum(ConversationQueueState), nullable=False, default=ConversationQueueState.classifying
    )
    original_arrival_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_agents.id"))
    previous_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_agents.id"))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    classification_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    notification_ledger: Mapped[dict | None] = mapped_column(JSON, default=dict)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class ConversationQueueEvent(Base):
    __tablename__ = "crm_conversation_queue_events"
    __table_args__ = (Index("ix_crm_queue_events_entry_created", "queue_entry_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_conversation_queue_entries.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    payload: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
