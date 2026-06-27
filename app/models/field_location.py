"""Field-technician live location — a dedicated, person-keyed presence store.

Mirrors the proven CRM agent-presence design (a 1:1 current-snapshot row plus an
immutable ping audit with a retention prune) but is keyed by ``person_id`` because
field jobs and the transition engine identify a tech by Person, not by CRM agent.
See docs/field-app-scope.md §9.1 for the decision.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FieldPresenceStatus(enum.Enum):
    on_shift = "on_shift"
    on_break = "on_break"
    off_shift = "off_shift"


class FieldTechPresence(Base):
    """Current location snapshot for a field technician (one row per person)."""

    __tablename__ = "field_tech_presence"
    __table_args__ = (
        Index("ix_field_tech_presence_person_id", "person_id", unique=True),
        Index("ix_field_tech_presence_status", "status"),
        Index("ix_field_tech_presence_last_location_at", "last_location_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    status: Mapped[FieldPresenceStatus] = mapped_column(
        Enum(FieldPresenceStatus, name="fieldpresencestatus"),
        default=FieldPresenceStatus.off_shift,
        nullable=False,
    )
    # Opt-in: a tech only appears on the live-map while this is true.
    location_sharing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_latitude: Mapped[float | None] = mapped_column(Float)
    last_longitude: Mapped[float | None] = mapped_column(Float)
    last_location_accuracy_m: Mapped[float | None] = mapped_column(Float)
    last_location_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    person = relationship("Person")


class FieldTechLocationPing(Base):
    """Immutable audit of every accepted location ping; pruned on a retention window."""

    __tablename__ = "field_tech_location_pings"
    __table_args__ = (
        Index("ix_field_tech_location_pings_person_received", "person_id", "received_at"),
        Index("ix_field_tech_location_pings_received_at", "received_at"),
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_field_tech_location_pings_lat_range"),
        CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_field_tech_location_pings_lng_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    # Optional work-order context (set when the ping was captured against a job).
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), default="mobile", nullable=False)

    person = relationship("Person")


class WorkOrderAccessToken(Base):
    """Magic-link token granting a customer the "Track My Visit" page for one job.

    There is no customer login: the unguessable ``token`` *is* the capability,
    exactly like the survey-invitation pattern. One active token per work order
    (the service get-or-creates it). Read access plus the limited confirm /
    request-reschedule actions are all authorized by holding the link.
    """

    __tablename__ = "work_order_access_tokens"
    __table_args__ = (
        Index("ix_work_order_access_tokens_token", "token", unique=True),
        Index("ix_work_order_access_tokens_work_order_id", "work_order_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    work_order = relationship("WorkOrder")
