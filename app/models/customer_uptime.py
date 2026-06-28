import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CustomerUptimeSnapshot(Base):
    __tablename__ = "customer_uptime_snapshots"
    __table_args__ = (
        Index("ix_customer_uptime_snapshots_customer_observed", "customer_id", "observed_at"),
        Index("ix_customer_uptime_snapshots_service_observed", "service_id", "observed_at"),
        Index("ix_customer_uptime_snapshots_observed_at", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[str] = mapped_column(String(120), nullable=False)
    service_id: Mapped[str | None] = mapped_column(String(120))
    login: Mapped[str | None] = mapped_column(String(120))
    is_online: Mapped[bool] = mapped_column(nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    start_session: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_change: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_on: Mapped[int | None] = mapped_column(Integer)
    in_bytes: Mapped[int | None] = mapped_column(BigInteger)
    out_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="selfcare_polling")
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class CustomerUptimePeriod(Base):
    __tablename__ = "customer_uptime_periods"
    __table_args__ = (
        Index(
            "ix_customer_uptime_periods_open",
            "customer_id",
            "service_id",
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index("ix_customer_uptime_periods_customer_started", "customer_id", "started_at"),
        Index("ix_customer_uptime_periods_service_started", "service_id", "started_at"),
        Index("ix_customer_uptime_periods_status_started", "status", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[str] = mapped_column(String(120), nullable=False)
    service_id: Mapped[str | None] = mapped_column(String(120))
    login: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="selfcare_polling")
    confidence: Mapped[str] = mapped_column(String(40), nullable=False, default="observed")
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    notes: Mapped[str | None] = mapped_column(Text)
