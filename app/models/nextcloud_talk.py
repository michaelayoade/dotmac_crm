import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class NextcloudTalkAccount(Base):
    """Per-user Nextcloud Talk credentials for the Talk sidebar panel."""

    __tablename__ = "nextcloud_talk_accounts"
    __table_args__ = (UniqueConstraint("person_id", name="uq_nextcloud_talk_accounts_person_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)

    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    username: Mapped[str] = mapped_column(String(150), nullable=False)
    app_password_enc: Mapped[str] = mapped_column(String(2048), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    person = relationship("Person")

