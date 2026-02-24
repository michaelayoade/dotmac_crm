import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.crm.enums import ChannelType


class CrmTeam(Base):
    __tablename__ = "crm_teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    service_team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("service_teams.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    service_team = relationship("ServiceTeam", back_populates="crm_teams", foreign_keys=[service_team_id])
    agents = relationship("CrmAgentTeam", back_populates="team")
    channels = relationship("CrmTeamChannel", back_populates="team")
    assignments = relationship("ConversationAssignment", back_populates="team")
    routing_rules = relationship("CrmRoutingRule", back_populates="team")


class CrmAgent(Base):
    __tablename__ = "crm_agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[str | None] = mapped_column(String(120))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    teams = relationship("CrmAgentTeam", back_populates="agent")
    assignments = relationship("ConversationAssignment", back_populates="agent")
    macros = relationship("CrmConversationMacro", back_populates="created_by_agent", foreign_keys="CrmConversationMacro.created_by_agent_id")


class CrmAgentTeam(Base):
    __tablename__ = "crm_agent_teams"
    __table_args__ = (UniqueConstraint("agent_id", "team_id", name="uq_crm_agent_team"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_agents.id"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_teams.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    agent = relationship("CrmAgent", back_populates="teams")
    team = relationship("CrmTeam", back_populates="agents")


class CrmTeamChannel(Base):
    __tablename__ = "crm_team_channels"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "channel_type",
            "channel_target_id",
            name="uq_crm_team_channels_team_type_target",
        ),
        Index(
            "uq_crm_team_channels_default",
            "team_id",
            "channel_type",
            unique=True,
            sqlite_where=text("channel_target_id IS NULL"),
            postgresql_where=text("channel_target_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_teams.id"), nullable=False)
    channel_type: Mapped[ChannelType] = mapped_column(Enum(ChannelType), nullable=False)
    channel_target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integration_targets.id")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    team = relationship("CrmTeam", back_populates="channels")


class CrmRoutingRule(Base):
    __tablename__ = "crm_routing_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_teams.id"), nullable=False)
    channel_type: Mapped[ChannelType] = mapped_column(Enum(ChannelType), nullable=False)
    rule_config: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    team = relationship("CrmTeam", back_populates="routing_rules")
