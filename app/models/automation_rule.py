import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AutomationRuleStatus(enum.Enum):
    active = "active"
    paused = "paused"
    archived = "archived"


class AutomationLogOutcome(enum.Enum):
    success = "success"
    partial_failure = "partial_failure"
    failure = "failure"
    skipped = "skipped"


class AutomationRule(Base):
    __tablename__ = "automation_rules"
    __table_args__ = (
        Index(
            "ix_automation_rules_active_event",
            "event_type",
            "status",
            "is_active",
            "priority",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    conditions: Mapped[list | None] = mapped_column(JSONB)
    actions: Mapped[list | None] = mapped_column(JSONB)
    status: Mapped[AutomationRuleStatus] = mapped_column(
        Enum(AutomationRuleStatus), default=AutomationRuleStatus.active
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    stop_after_match: Mapped[bool] = mapped_column(Boolean, default=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=0)
    execution_count: Mapped[int] = mapped_column(Integer, default=0)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    created_by = relationship("Person", foreign_keys=[created_by_id])
    logs = relationship("AutomationRuleLog", back_populates="rule")


class AutomationRuleLog(Base):
    __tablename__ = "automation_rule_logs"
    __table_args__ = (
        Index("ix_automation_rule_logs_rule_id", "rule_id"),
        Index("ix_automation_rule_logs_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("automation_rules.id"), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[AutomationLogOutcome] = mapped_column(Enum(AutomationLogOutcome), nullable=False)
    actions_executed: Mapped[list | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    rule = relationship("AutomationRule", back_populates="logs")
