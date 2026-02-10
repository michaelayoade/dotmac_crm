"""Chat widget models for website embed support."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ChatWidgetConfig(Base):
    """Widget configuration for embedding on external sites."""

    __tablename__ = "chat_widget_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    # Optional link to connector for routing
    connector_config_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connector_configs.id")
    )

    # Domain restrictions - JSON array of allowed domains
    # Supports exact matches ("example.com") and wildcards ("*.example.com")
    allowed_domains: Mapped[list | None] = mapped_column(JSON)

    # Appearance settings
    primary_color: Mapped[str] = mapped_column(String(20), default="#3B82F6")
    bubble_position: Mapped[str] = mapped_column(String(20), default="bottom-right")
    welcome_message: Mapped[str | None] = mapped_column(Text)
    placeholder_text: Mapped[str] = mapped_column(String(120), default="Type a message...")
    widget_title: Mapped[str] = mapped_column(String(80), default="Chat with us")
    offline_message: Mapped[str | None] = mapped_column(Text)

    # Pre-chat form configuration
    prechat_form_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    prechat_fields: Mapped[list | None] = mapped_column(MutableList.as_mutable(JSON()))

    # Business hours - JSON with schedule
    business_hours: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSON()))

    # Rate limiting settings
    rate_limit_messages_per_minute: Mapped[int] = mapped_column(Integer, default=10)
    rate_limit_sessions_per_ip: Mapped[int] = mapped_column(Integer, default=5)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    connector_config = relationship("ConnectorConfig")
    visitor_sessions = relationship(
        "WidgetVisitorSession", back_populates="widget_config", cascade="all, delete-orphan"
    )


class WidgetVisitorSession(Base):
    """Track widget visitor sessions for anonymous and identified users."""

    __tablename__ = "widget_visitor_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    widget_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_widget_configs.id"), nullable=False
    )

    # Authentication token for the visitor
    visitor_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    # Browser fingerprint for session persistence
    fingerprint_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    # Link to identified person (after identification)
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )

    # Link to conversation
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_conversations.id")
    )

    # Visitor metadata
    ip_address: Mapped[str | None] = mapped_column(String(45))  # IPv6 max length
    user_agent: Mapped[str | None] = mapped_column(String(512))
    page_url: Mapped[str | None] = mapped_column(String(2048))
    referrer_url: Mapped[str | None] = mapped_column(String(2048))

    # Custom fields from prechat form or identify call
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", MutableDict.as_mutable(JSON())
    )

    # Identification tracking
    is_identified: Mapped[bool] = mapped_column(Boolean, default=False)
    identified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    identified_email: Mapped[str | None] = mapped_column(String(255))
    identified_name: Mapped[str | None] = mapped_column(String(160))

    # Activity tracking
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationships
    widget_config = relationship("ChatWidgetConfig", back_populates="visitor_sessions")
    person = relationship("Person")
    conversation = relationship("Conversation")
