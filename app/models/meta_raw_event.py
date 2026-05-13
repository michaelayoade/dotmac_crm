import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MetaRawEvent(Base):
    """Source-of-truth record for raw inbound Meta messaging events."""

    __tablename__ = "meta_raw_events"
    __table_args__ = (
        Index("ix_meta_raw_events_platform_received", "platform", "received_at"),
        Index("ix_meta_raw_events_sender_page", "platform", "sender_id", "page_id"),
        UniqueConstraint("dedupe_key", name="uq_meta_raw_events_dedupe_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    sender_id: Mapped[str | None] = mapped_column(String(255))
    page_id: Mapped[str | None] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(255))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON()), nullable=False)
    attribution: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSON()))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
