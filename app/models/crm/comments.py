import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SocialCommentPlatform(enum.Enum):
    facebook = "facebook"
    instagram = "instagram"


class SocialComment(Base):
    __tablename__ = "crm_social_comments"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "external_id",
            name="uq_crm_social_comments_platform_external",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[SocialCommentPlatform] = mapped_column(
        Enum(SocialCommentPlatform), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    external_post_id: Mapped[str | None] = mapped_column(String(200))
    source_account_id: Mapped[str | None] = mapped_column(String(200))
    author_id: Mapped[str | None] = mapped_column(String(200))
    author_name: Mapped[str | None] = mapped_column(String(200))
    message: Mapped[str | None] = mapped_column(Text)
    created_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    permalink_url: Mapped[str | None] = mapped_column(String(500))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class SocialCommentReply(Base):
    __tablename__ = "crm_social_comment_replies"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "external_id",
            name="uq_crm_social_comment_replies_platform_external",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    platform: Mapped[SocialCommentPlatform] = mapped_column(
        Enum(SocialCommentPlatform), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
