import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SubscriberStationMapping(Base):
    __tablename__ = "subscriber_station_mappings"
    __table_args__ = (
        Index(
            "ix_subscriber_station_mappings_normalized_key",
            "normalized_station_key",
            postgresql_where=text("is_active IS TRUE"),
        ),
        Index(
            "ix_subscriber_station_mappings_monitoring_title",
            "monitoring_title",
            postgresql_where=text("is_active IS TRUE"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_customer_base_station: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    normalized_station_key: Mapped[str] = mapped_column(String(255), nullable=False)
    monitoring_device_id: Mapped[str | None] = mapped_column(String(120))
    monitoring_title: Mapped[str | None] = mapped_column(String(255))
    match_method: Mapped[str | None] = mapped_column(String(80))
    match_confidence: Mapped[str | None] = mapped_column(String(40))
    is_manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class SubscriberOfflineOutreachLog(Base):
    __tablename__ = "subscriber_offline_outreach_logs"
    __table_args__ = (
        Index("ix_subscriber_offline_outreach_logs_run_local_date", "run_local_date"),
        Index(
            "ix_subscriber_offline_outreach_logs_subscriber_run_date",
            "subscriber_id",
            "run_local_date",
        ),
        Index(
            "ix_subscriber_offline_outreach_logs_external_customer_created",
            "external_customer_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("subscribers.id"))
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_conversations.id"))
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_messages.id"))
    channel_target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integration_targets.id")
    )
    run_local_date: Mapped[date] = mapped_column(Date, nullable=False)
    external_customer_id: Mapped[str] = mapped_column(String(120), nullable=False)
    subscriber_number: Mapped[str | None] = mapped_column(String(120))
    customer_name: Mapped[str | None] = mapped_column(String(255))
    base_station_label: Mapped[str | None] = mapped_column(String(255))
    normalized_station_key: Mapped[str | None] = mapped_column(String(255))
    monitoring_device_id: Mapped[str | None] = mapped_column(String(120))
    monitoring_title: Mapped[str | None] = mapped_column(String(255))
    monitoring_ping_state: Mapped[str | None] = mapped_column(String(40))
    monitoring_snmp_state: Mapped[str | None] = mapped_column(String(40))
    station_status: Mapped[str | None] = mapped_column(String(40))
    decision_status: Mapped[str] = mapped_column(String(40), nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(String(120))
    message_template: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    subscriber = relationship("Subscriber")
    person = relationship("Person")
    conversation = relationship("Conversation")
    message = relationship("Message")
    channel_target = relationship("IntegrationTarget")
