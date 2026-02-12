import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class VendorAssignmentType(enum.Enum):
    bidding = "bidding"
    direct = "direct"


class InstallationProjectStatus(enum.Enum):
    draft = "draft"
    open_for_bidding = "open_for_bidding"
    quoted = "quoted"
    approved = "approved"
    in_progress = "in_progress"
    completed = "completed"
    verified = "verified"
    assigned = "assigned"


class ProjectQuoteStatus(enum.Enum):
    draft = "draft"
    submitted = "submitted"
    under_review = "under_review"
    approved = "approved"
    rejected = "rejected"
    revision_requested = "revision_requested"


class ProposedRouteRevisionStatus(enum.Enum):
    draft = "draft"
    submitted = "submitted"
    accepted = "accepted"
    rejected = "rejected"


class AsBuiltRouteStatus(enum.Enum):
    submitted = "submitted"
    under_review = "under_review"
    accepted = "accepted"
    rejected = "rejected"


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str | None] = mapped_column(String(60), unique=True)
    contact_name: Mapped[str | None] = mapped_column(String(160))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    license_number: Mapped[str | None] = mapped_column(String(120))
    service_area: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    users = relationship("VendorUser", back_populates="vendor")
    quotes = relationship("ProjectQuote", back_populates="vendor")


class VendorUser(Base):
    __tablename__ = "vendor_users"
    __table_args__ = (UniqueConstraint("vendor_id", "person_id", name="uq_vendor_users_vendor_person"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vendors.id"), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    role: Mapped[str | None] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    vendor = relationship("Vendor", back_populates="users")
    person = relationship("Person")


class InstallationProject(Base):
    __tablename__ = "installation_projects"
    __table_args__ = (UniqueConstraint("project_id", name="uq_installation_projects_project"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    buildout_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buildout_projects.id")
    )
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("subscribers.id"))
    address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)  # FK to addresses removed
    )
    assigned_vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("vendors.id"))
    assignment_type: Mapped[VendorAssignmentType | None] = mapped_column(Enum(VendorAssignmentType))
    status: Mapped[InstallationProjectStatus] = mapped_column(
        Enum(InstallationProjectStatus), default=InstallationProjectStatus.draft
    )
    bidding_open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bidding_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_quote_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("project_quotes.id"))
    created_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    project = relationship("Project")
    buildout_project = relationship("BuildoutProject")
    subscriber = relationship("Subscriber")
    # address = relationship("Address")  # Model removed
    assigned_vendor = relationship("Vendor")
    approved_quote = relationship("ProjectQuote", foreign_keys=[approved_quote_id])
    created_by = relationship("Person", foreign_keys=[created_by_person_id])
    quotes = relationship(
        "ProjectQuote", back_populates="project", primaryjoin="InstallationProject.id == ProjectQuote.project_id"
    )
    project_notes = relationship("InstallationProjectNote", back_populates="project")
    as_built_routes = relationship("AsBuiltRoute", back_populates="project")


class ProjectQuote(Base):
    __tablename__ = "project_quotes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("installation_projects.id"), nullable=False
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vendors.id"), nullable=False)
    status: Mapped[ProjectQuoteStatus] = mapped_column(Enum(ProjectQuoteStatus), default=ProjectQuoteStatus.draft)
    currency: Mapped[str] = mapped_column(String(3), default="NGN")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    tax_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    review_notes: Mapped[str | None] = mapped_column(Text)
    created_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    project = relationship("InstallationProject", back_populates="quotes", foreign_keys=[project_id])
    vendor = relationship("Vendor", back_populates="quotes")
    reviewed_by = relationship("Person", foreign_keys=[reviewed_by_person_id])
    created_by = relationship("Person", foreign_keys=[created_by_person_id])
    line_items = relationship("QuoteLineItem", back_populates="quote")
    route_revisions = relationship("ProposedRouteRevision", back_populates="quote")


class QuoteLineItem(Base):
    __tablename__ = "quote_line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("project_quotes.id"), nullable=False)
    item_type: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    cable_type: Mapped[str | None] = mapped_column(String(120))
    fiber_count: Mapped[int | None] = mapped_column(Integer)
    splice_count: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1.000"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    quote = relationship("ProjectQuote", back_populates="line_items")


class ProposedRouteRevision(Base):
    __tablename__ = "proposed_route_revisions"
    __table_args__ = (UniqueConstraint("quote_id", "revision_number", name="uq_proposed_route_quote_revision"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("project_quotes.id"), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ProposedRouteRevisionStatus] = mapped_column(
        Enum(ProposedRouteRevisionStatus), default=ProposedRouteRevisionStatus.draft
    )
    route_geom = mapped_column(Geometry("LINESTRING", srid=4326), nullable=True)
    length_meters: Mapped[float | None] = mapped_column(Float)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    review_notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    quote = relationship("ProjectQuote", back_populates="route_revisions")
    submitted_by = relationship("Person", foreign_keys=[submitted_by_person_id])
    reviewed_by = relationship("Person", foreign_keys=[reviewed_by_person_id])


class AsBuiltRoute(Base):
    __tablename__ = "as_built_routes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("installation_projects.id"), nullable=False
    )
    proposed_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposed_route_revisions.id")
    )
    status: Mapped[AsBuiltRouteStatus] = mapped_column(Enum(AsBuiltRouteStatus), default=AsBuiltRouteStatus.submitted)
    route_geom = mapped_column(Geometry("LINESTRING", srid=4326), nullable=True)
    actual_length_meters: Mapped[float | None] = mapped_column(Float)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    review_notes: Mapped[str | None] = mapped_column(Text)
    fiber_segment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("fiber_segments.id"))
    report_file_path: Mapped[str | None] = mapped_column(String(500))
    report_file_name: Mapped[str | None] = mapped_column(String(255))
    report_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    project = relationship("InstallationProject", back_populates="as_built_routes")
    proposed_revision = relationship("ProposedRouteRevision")
    submitted_by = relationship("Person", foreign_keys=[submitted_by_person_id])
    reviewed_by = relationship("Person", foreign_keys=[reviewed_by_person_id])
    fiber_segment = relationship("FiberSegment")


class InstallationProjectNote(Base):
    __tablename__ = "installation_project_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("installation_projects.id"), nullable=False
    )
    author_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    attachments: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    project = relationship("InstallationProject", back_populates="project_notes")
    author = relationship("Person")
