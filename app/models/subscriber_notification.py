import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.notification import NotificationChannel


class SubscriberNotificationLog(Base):
    __tablename__ = "subscriber_notification_logs"
    __table_args__ = (
        Index("ix_subscriber_notification_logs_subscriber_created", "subscriber_id", "created_at"),
        Index(
            "ix_subscriber_notification_logs_notification_id",
            "notification_id",
            unique=True,
            postgresql_where=text("notification_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("subscribers.id"), nullable=False)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id"))
    notification_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("notifications.id"))
    channel: Mapped[NotificationChannel] = mapped_column(Enum(NotificationChannel), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    message_body: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sent_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    subscriber = relationship("Subscriber")
    ticket = relationship("Ticket")
    notification = relationship("Notification")
    sent_by_person = relationship("Person")
