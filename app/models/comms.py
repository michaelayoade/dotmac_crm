import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class CustomerNotificationStatus(enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"


class CustomerSurveyStatus(enum.Enum):
    draft = "draft"
    active = "active"
    paused = "paused"
    closed = "closed"


class SurveyTriggerType(enum.Enum):
    manual = "manual"
    ticket_closed = "ticket_closed"
    work_order_completed = "work_order_completed"


class SurveyQuestionType(enum.Enum):
    rating = "rating"
    nps = "nps"
    multiple_choice = "multiple_choice"
    free_text = "free_text"


class SurveyInvitationStatus(enum.Enum):
    pending = "pending"
    sent = "sent"
    opened = "opened"
    completed = "completed"
    expired = "expired"


class CustomerNotificationEvent(Base):
    __tablename__ = "customer_notification_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[CustomerNotificationStatus] = mapped_column(
        Enum(CustomerNotificationStatus), default=CustomerNotificationStatus.pending
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class EtaUpdate(Base):
    __tablename__ = "eta_updates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False)
    eta_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    work_order = relationship("WorkOrder")


class Survey(Base):
    __tablename__ = "surveys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    questions: Mapped[list | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Survey lifecycle
    status: Mapped[CustomerSurveyStatus] = mapped_column(Enum(CustomerSurveyStatus), default=CustomerSurveyStatus.draft)
    trigger_type: Mapped[SurveyTriggerType] = mapped_column(Enum(SurveyTriggerType), default=SurveyTriggerType.manual)

    # Public access
    public_slug: Mapped[str | None] = mapped_column(String(120), unique=True, index=True)
    thank_you_message: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Segment filtering (reuses campaign pattern)
    segment_filter: Mapped[dict | None] = mapped_column(JSON)

    # Creator
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))

    # Denormalized counters
    total_invited: Mapped[int] = mapped_column(Integer, default=0)
    total_responses: Mapped[int] = mapped_column(Integer, default=0)
    avg_rating: Mapped[float | None] = mapped_column(Float)
    nps_score: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    # Relationships
    created_by = relationship("Person", foreign_keys=[created_by_id])
    invitations = relationship("SurveyInvitation", back_populates="survey")
    responses = relationship("SurveyResponse", back_populates="survey")


class SurveyResponse(Base):
    __tablename__ = "survey_responses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("surveys.id"), nullable=False)
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"))
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id"))
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("survey_invitations.id"))
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    responses: Mapped[dict | None] = mapped_column(JSON)
    rating: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    survey = relationship("Survey", back_populates="responses")
    work_order = relationship("WorkOrder")
    ticket = relationship("Ticket")
    invitation = relationship("SurveyInvitation", back_populates="response")
    person = relationship("Person")


class SurveyInvitation(Base):
    __tablename__ = "survey_invitations"
    __table_args__ = (
        UniqueConstraint("survey_id", "person_id", name="uq_survey_invitation_person"),
        Index("ix_survey_invitations_token", "token", unique=True),
        Index("ix_survey_invitations_survey_id", "survey_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("surveys.id"), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SurveyInvitationStatus] = mapped_column(
        Enum(SurveyInvitationStatus), default=SurveyInvitationStatus.pending
    )

    # Delivery tracking
    notification_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("notifications.id"))

    # Trigger context (which entity triggered this invitation)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id"))
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"))

    # Timestamps
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Relationships
    survey = relationship("Survey", back_populates="invitations")
    person = relationship("Person")
    notification = relationship("Notification")
    ticket = relationship("Ticket")
    work_order = relationship("WorkOrder")
    response = relationship("SurveyResponse", back_populates="invitation", uselist=False)
