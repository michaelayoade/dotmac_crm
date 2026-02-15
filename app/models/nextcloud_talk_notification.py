import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class NextcloudTalkNotificationRoom(Base):
    """Cached user-to-room mapping for app notification forwarding to Nextcloud Talk."""

    __tablename__ = "nextcloud_talk_notification_rooms"
    __table_args__ = (
        UniqueConstraint(
            "person_id",
            "base_url",
            "notifier_username",
            name="uq_nextcloud_talk_notification_rooms_person_instance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    notifier_username: Mapped[str] = mapped_column(String(150), nullable=False)
    invite_target: Mapped[str] = mapped_column(String(255), nullable=False)
    room_token: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
