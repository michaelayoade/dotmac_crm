"""Authoritative current response obligation for a CRM conversation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.crm.enums import ResponseObligationState


class ResponseObligation(Base):
    """One materialized customer-response decision per conversation.

    Messages, conversation lifecycle, priority, and assignment are inputs. This
    row is the canonical decision consumed by inbox projections, work queues,
    reminders, escalations, and response-SLA reporting.
    """

    __tablename__ = "crm_response_obligations"
    __table_args__ = (
        Index("ix_crm_response_obligations_due", "state", "next_escalation_at"),
        Index("ix_crm_response_obligations_owner", "owner_agent_id", "owner_team_id"),
        Index("ix_crm_response_obligations_reconciled", "reconciled_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("crm_conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    state: Mapped[ResponseObligationState] = mapped_column(
        Enum(ResponseObligationState, native_enum=False, length=32),
        nullable=False,
    )
    trigger_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_messages.id", ondelete="SET NULL")
    )
    latest_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_agents.id"))
    owner_team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_teams.id"))
    owner_scope: Mapped[str] = mapped_column(String(80), nullable=False)

    escalation_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_escalation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    conversation = relationship("Conversation", back_populates="response_obligation")
    trigger_message = relationship("Message", foreign_keys=[trigger_message_id])
    owner_agent = relationship("CrmAgent", foreign_keys=[owner_agent_id])
    owner_team = relationship("CrmTeam", foreign_keys=[owner_team_id])
