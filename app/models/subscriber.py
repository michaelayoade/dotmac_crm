import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AddressType(enum.Enum):
    service = "service"
    billing = "billing"
    mailing = "mailing"


class SubscriberStatus(enum.Enum):
    """Subscriber status synced from external billing system."""
    active = "active"
    suspended = "suspended"
    terminated = "terminated"
    pending = "pending"


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(80))
    domain: Mapped[str | None] = mapped_column(String(120))
    website: Mapped[str | None] = mapped_column(String(255))
    address_line1: Mapped[str | None] = mapped_column(String(120))
    address_line2: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(80))
    region: Mapped[str | None] = mapped_column(String(80))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country_code: Mapped[str | None] = mapped_column(String(2))
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    people = relationship("Person", back_populates="organization")


class Reseller(Base):
    __tablename__ = "resellers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str | None] = mapped_column(String(60), unique=True)
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    users = relationship("ResellerUser", back_populates="reseller")


class ResellerUser(Base):
    __tablename__ = "reseller_users"
    __table_args__ = (
        UniqueConstraint("reseller_id", "person_id", name="uq_reseller_users_reseller_person"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reseller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resellers.id"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    reseller = relationship("Reseller", back_populates="users")
    person = relationship("Person")


class Subscriber(Base):
    """
    Subscriber account synced from external billing/subscription system.

    This model stores subscriber data pulled from external systems like
    Splynx, UCRM, WHMCS, or custom billing platforms. It provides CRM
    context for tickets, work orders, and projects without managing
    billing locally.
    """
    __tablename__ = "subscribers"
    __table_args__ = (
        Index("ix_subscribers_external", "external_system", "external_id"),
        Index("ix_subscribers_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Link to Person (customer contact)
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )

    # External system reference
    external_id: Mapped[str | None] = mapped_column(String(120))
    external_system: Mapped[str | None] = mapped_column(String(60))  # splynx, ucrm, whmcs

    # Account identifiers
    subscriber_number: Mapped[str | None] = mapped_column(String(60), unique=True)
    account_number: Mapped[str | None] = mapped_column(String(60))

    # Status (synced from billing)
    status: Mapped[SubscriberStatus] = mapped_column(
        Enum(SubscriberStatus), default=SubscriberStatus.active
    )

    # Service info (for display)
    service_name: Mapped[str | None] = mapped_column(String(160))
    service_plan: Mapped[str | None] = mapped_column(String(120))
    service_speed: Mapped[str | None] = mapped_column(String(60))  # "100/20 Mbps"

    # Service address (may differ from person address)
    service_address_line1: Mapped[str | None] = mapped_column(String(120))
    service_address_line2: Mapped[str | None] = mapped_column(String(120))
    service_city: Mapped[str | None] = mapped_column(String(80))
    service_region: Mapped[str | None] = mapped_column(String(80))
    service_postal_code: Mapped[str | None] = mapped_column(String(20))
    service_country_code: Mapped[str | None] = mapped_column(String(2))

    # Billing info (read-only from external system)
    balance: Mapped[str | None] = mapped_column(String(40))  # Display string
    currency: Mapped[str | None] = mapped_column(String(3))
    billing_cycle: Mapped[str | None] = mapped_column(String(40))  # monthly, prepaid
    next_bill_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Activation dates
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Sync metadata
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_error: Mapped[str | None] = mapped_column(String(500))
    sync_metadata: Mapped[dict | None] = mapped_column(
        "sync_metadata", MutableDict.as_mutable(JSON())
    )

    # Notes (local, not synced)
    notes: Mapped[str | None] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    organization = relationship("Organization", foreign_keys=[organization_id])
    tickets = relationship("Ticket", back_populates="subscriber")
    work_orders = relationship("WorkOrder", back_populates="subscriber")
    projects = relationship("Project", back_populates="subscriber")

    @property
    def display_name(self) -> str:
        """Display name for UI."""
        if self.person:
            return f"{self.person.first_name} {self.person.last_name}"
        if self.organization:
            return self.organization.name
        return self.subscriber_number or str(self.id)[:8]

    @property
    def service_address(self) -> str | None:
        """Formatted service address."""
        parts = [
            self.service_address_line1,
            self.service_address_line2,
            self.service_city,
            self.service_region,
            self.service_postal_code,
        ]
        return ", ".join(p for p in parts if p)
