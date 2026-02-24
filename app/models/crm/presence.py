import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, String
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
        Index("ix_crm_agent_presence_manual_override_status", "manual_override_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_agents.id"), nullable=False)
    status: Mapped[AgentPresenceStatus] = mapped_column(
        Enum(AgentPresenceStatus, name="agentpresencestatus"),
        default=AgentPresenceStatus.offline,
        nullable=False,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When set, this value takes precedence over auto (heartbeat/visibility) status.
    manual_override_status: Mapped[AgentPresenceStatus | None] = mapped_column(
        Enum(AgentPresenceStatus, name="agentpresencestatus"),
        nullable=True,
    )
    manual_override_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location_sharing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_latitude: Mapped[float | None] = mapped_column(Float)
    last_longitude: Mapped[float | None] = mapped_column(Float)
    last_location_accuracy_m: Mapped[float | None] = mapped_column(Float)
    last_location_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    agent = relationship("CrmAgent")


class AgentPresenceEvent(Base):
    """Tracks presence status intervals for reporting (durations by status)."""

    __tablename__ = "crm_agent_presence_events"
    __table_args__ = (
        Index("ix_crm_agent_presence_events_agent_id", "agent_id"),
        Index("ix_crm_agent_presence_events_status", "status"),
        Index("ix_crm_agent_presence_events_started_at", "started_at"),
        Index("ix_crm_agent_presence_events_ended_at", "ended_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_agents.id"), nullable=False)
    status: Mapped[AgentPresenceStatus] = mapped_column(
        Enum(AgentPresenceStatus, name="agentpresencestatus"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 'auto' (heartbeat/visibility) or 'manual' (user override).
    source: Mapped[str] = mapped_column(String(32), default="auto", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    agent = relationship("CrmAgent")


class AgentLocationPing(Base):
    __tablename__ = "crm_agent_location_pings"
    __table_args__ = (
        Index("ix_crm_agent_location_pings_agent_received", "agent_id", "received_at"),
        Index("ix_crm_agent_location_pings_received_at", "received_at"),
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_crm_agent_location_pings_lat_range"),
        CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_crm_agent_location_pings_lng_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_agents.id"), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), default="browser", nullable=False)

    agent = relationship("CrmAgent")
