import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class WebhookDeadLetter(Base):
    """Stores inbound webhook payloads that could not be processed.

    Populated when:
    - A Celery inbound-webhook task exhausts all retries.
    - A message within a webhook payload fails to parse.
    """

    __tablename__ = "webhook_dead_letters"
    __table_args__ = (Index("ix_webhook_dead_letters_channel_created", "channel", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    message_id: Mapped[str | None] = mapped_column(String(200))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
