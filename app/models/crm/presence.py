import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.crm.enums import AgentPresenceStatus


class AgentPresence(Base):
    __tablename__ = "crm_agent_presence"
    __table_args__ = (
        Index("ix_crm_agent_presence_agent_id", "agent_id", unique=True),
        Index("ix_crm_agent_presence_status", "status"),
        Index("ix_crm_agent_presence_last_seen_at", "last_seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_agents.id"), nullable=False
    )
    status: Mapped[AgentPresenceStatus] = mapped_column(
        Enum(AgentPresenceStatus, name="agentpresencestatus"),
        default=AgentPresenceStatus.offline,
        nullable=False,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    agent = relationship("CrmAgent")
