import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class TicketStatus(enum.Enum):
    new = "new"
    open = "open"
    pending = "pending"
    waiting_on_customer = "waiting_on_customer"
    lastmile_rerun = "lastmile_rerun"
    site_under_construction = "site_under_construction"
    on_hold = "on_hold"
    resolved = "resolved"
    closed = "closed"
    canceled = "canceled"


class TicketPriority(enum.Enum):
    lower = "lower"
    low = "low"
    medium = "medium"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class TicketChannel(enum.Enum):
    web = "web"
    email = "email"
    phone = "phone"
    chat = "chat"
    api = "api"


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("subscribers.id"))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_leads.id"))
    customer_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    created_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    assigned_to_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    ticket_manager_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    assistant_manager_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    service_team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("service_teams.id"))
    region: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus), default=TicketStatus.new)
    priority: Mapped[TicketPriority] = mapped_column(Enum(TicketPriority), default=TicketPriority.medium)
    ticket_type: Mapped[str | None] = mapped_column(String(120))
    number: Mapped[str | None] = mapped_column(String(40))
    erpnext_id: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    channel: Mapped[TicketChannel] = mapped_column(Enum(TicketChannel), default=TicketChannel.web)
    tags: Mapped[list | None] = mapped_column(JSON)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    subscriber = relationship("Subscriber", back_populates="tickets")
    lead = relationship("Lead")
    customer = relationship("Person", foreign_keys=[customer_person_id])
    created_by = relationship("Person", foreign_keys=[created_by_person_id])
    assigned_to = relationship("Person", foreign_keys=[assigned_to_person_id])
    ticket_manager = relationship("Person", foreign_keys=[ticket_manager_person_id])
    assistant_manager = relationship("Person", foreign_keys=[assistant_manager_person_id])
    service_team = relationship("ServiceTeam", foreign_keys=[service_team_id])
    comments = relationship("TicketComment", back_populates="ticket")
    assignees = relationship(
        "TicketAssignee",
        back_populates="ticket",
        cascade="all, delete-orphan",
    )

    @property
    def assigned_to_person_ids(self):
        if self.assignees:
            return [assignee.person_id for assignee in self.assignees]
        if self.assigned_to_person_id:
            return [self.assigned_to_person_id]
        return []


class TicketAssignee(Base):
    __tablename__ = "ticket_assignees"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    ticket = relationship("Ticket", back_populates="assignees")
    person = relationship("Person")


class TicketComment(Base):
    __tablename__ = "ticket_comments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id"), nullable=False)
    author_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    attachments: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    ticket = relationship("Ticket", back_populates="comments")
    author = relationship("Person")


class TicketSlaEvent(Base):
    __tablename__ = "ticket_sla_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    expected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    ticket = relationship("Ticket")
