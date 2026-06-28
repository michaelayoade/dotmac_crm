import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class InfrastructureAlertCategory(enum.Enum):
    application_services = "application_services"
    background_workers = "background_workers"
    scheduled_jobs = "scheduled_jobs"
    queues = "queues"
    database = "database"
    replication = "replication"
    cache = "cache"
    external_integrations = "external_integrations"


class InfrastructureAlertSeverity(enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class InfrastructureAlertStatus(enum.Enum):
    open = "open"
    resolved = "resolved"


class InfrastructureAlert(Base):
    __tablename__ = "infrastructure_alerts"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_infrastructure_alerts_fingerprint"),
        Index("ix_infrastructure_alerts_status_severity", "status", "severity"),
        Index("ix_infrastructure_alerts_category_status", "category", "status"),
        Index("ix_infrastructure_alerts_last_seen_at", "last_seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fingerprint: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[InfrastructureAlertCategory] = mapped_column(Enum(InfrastructureAlertCategory), nullable=False)
    component: Mapped[str] = mapped_column(String(160), nullable=False)
    severity: Mapped[InfrastructureAlertSeverity] = mapped_column(Enum(InfrastructureAlertSeverity), nullable=False)
    status: Mapped[InfrastructureAlertStatus] = mapped_column(
        Enum(InfrastructureAlertStatus),
        default=InfrastructureAlertStatus.open,
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    details: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(80), default="application", nullable=False)
    check_key: Mapped[str] = mapped_column(String(160), nullable=False)
    target_url: Mapped[str | None] = mapped_column(String(500))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    occurrence_count: Mapped[int] = mapped_column(default=1, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
