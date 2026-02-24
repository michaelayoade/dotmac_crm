import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.crm.enums import ChannelType, ConversationPriority, ConversationStatus, MessageDirection, MessageStatus


class Conversation(Base):
    """CRM Conversation linked to a Person in the unified party model.

    Organization context is available via conversation.person.organization.
    """

    __tablename__ = "crm_conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id"))
    status: Mapped[ConversationStatus] = mapped_column(Enum(ConversationStatus), default=ConversationStatus.open)
    priority: Mapped[ConversationPriority | None] = mapped_column(
        Enum(ConversationPriority), default=ConversationPriority.none
    )
    subject: Mapped[str | None] = mapped_column(String(200))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    person = relationship("Person", back_populates="conversations")
    contact = relationship("Person", viewonly=True, primaryjoin="Conversation.person_id == Person.id")
    messages = relationship("Message", back_populates="conversation")
    assignments = relationship("ConversationAssignment", back_populates="conversation")
    tags = relationship("ConversationTag", back_populates="conversation")

    @hybrid_property
    def contact_id(self):
        return self.person_id

    @contact_id.expression  # type: ignore[no-redef]
    def contact_id(cls):
        return cls.person_id


class ConversationAssignment(Base):
    __tablename__ = "crm_conversation_assignments"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "team_id",
            "agent_id",
            name="uq_crm_conversation_assignments",
        ),
        CheckConstraint(
            "team_id IS NOT NULL OR agent_id IS NOT NULL",
            name="ck_crm_conversation_assignments_team_or_agent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_conversations.id"), nullable=False
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_teams.id"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_agents.id"))
    assigned_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    conversation = relationship("Conversation", back_populates="assignments")
    team = relationship("CrmTeam", back_populates="assignments")
    agent = relationship("CrmAgent", back_populates="assignments")


class ConversationTag(Base):
    __tablename__ = "crm_conversation_tags"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "tag",
            name="uq_crm_conversation_tags_conversation_tag",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_conversations.id"), nullable=False
    )
    tag: Mapped[str] = mapped_column(String(80), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    conversation = relationship("Conversation", back_populates="tags")


class Message(Base):
    __tablename__ = "crm_messages"
    __table_args__ = (
        Index(
            "uq_crm_messages_external",
            "channel_type",
            text("coalesce(channel_target_id, '00000000-0000-0000-0000-000000000000')"),
            "external_id",
            unique=True,
            sqlite_where=text("external_id IS NOT NULL"),
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index(
            "uq_crm_messages_inbound_external",
            "channel_type",
            "external_id",
            unique=True,
            sqlite_where=text(
                "external_id IS NOT NULL "
                "AND direction = 'inbound' "
                "AND channel_type IN ('email', 'facebook_messenger', 'instagram_dm')"
            ),
            postgresql_where=text(
                "external_id IS NOT NULL "
                "AND direction = 'inbound' "
                "AND channel_type IN ('email', 'facebook_messenger', 'instagram_dm')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_conversations.id"), nullable=False
    )
    person_channel_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("person_channels.id"))
    channel_target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integration_targets.id")
    )
    reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_messages.id"))
    channel_type: Mapped[ChannelType] = mapped_column(Enum(ChannelType), nullable=False)
    direction: Mapped[MessageDirection] = mapped_column(Enum(MessageDirection), nullable=False)
    status: Mapped[MessageStatus] = mapped_column(Enum(MessageStatus), default=MessageStatus.received)
    subject: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(120))
    external_ref: Mapped[str | None] = mapped_column(String(255))
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    conversation = relationship("Conversation", back_populates="messages")
    person_channel = relationship("PersonChannel")
    author = relationship("Person", foreign_keys=[author_id])
    attachments = relationship("MessageAttachment", back_populates="message")


class MessageAttachment(Base):
    __tablename__ = "crm_message_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_messages.id"), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(120))
    file_size: Mapped[int | None] = mapped_column(Integer)
    external_url: Mapped[str | None] = mapped_column(String(500))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    message = relationship("Message", back_populates="attachments")
