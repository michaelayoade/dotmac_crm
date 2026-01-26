import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.crm.enums import LeadStatus, QuoteStatus


class Pipeline(Base):
    __tablename__ = "crm_pipelines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    stages = relationship("PipelineStage", back_populates="pipeline")
    leads = relationship("Lead", back_populates="pipeline")


class PipelineStage(Base):
    __tablename__ = "crm_pipeline_stages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_pipelines.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    pipeline = relationship("Pipeline", back_populates="stages")
    leads = relationship("Lead", back_populates="stage")


class Lead(Base):
    """CRM Lead linked to a Person in the unified party model.

    Organization context is available via lead.person.organization.
    """
    __tablename__ = "crm_leads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    pipeline_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_pipelines.id")
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_pipeline_stages.id")
    )
    owner_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_agents.id")
    )
    title: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[LeadStatus] = mapped_column(Enum(LeadStatus), default=LeadStatus.new)
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    person = relationship("Person", back_populates="leads")
    pipeline = relationship("Pipeline", back_populates="leads")
    stage = relationship("PipelineStage", back_populates="leads")
    quotes = relationship("Quote", back_populates="lead")

    @hybrid_property
    def contact_id(self):
        return self.person_id

    @contact_id.expression
    def contact_id(cls):
        return cls.person_id


class Quote(Base):
    """CRM Quote linked to a Person in the unified party model.

    Organization context is available via quote.person.organization.
    """
    __tablename__ = "crm_quotes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_leads.id")
    )
    status: Mapped[QuoteStatus] = mapped_column(
        Enum(QuoteStatus), default=QuoteStatus.draft
    )
    currency: Mapped[str] = mapped_column(String(3), default="NGN")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    tax_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    person = relationship("Person", back_populates="quotes")
    lead = relationship("Lead", back_populates="quotes")
    line_items = relationship("CrmQuoteLineItem", back_populates="quote")
    sales_order = relationship("SalesOrder", back_populates="quote", uselist=False)

    @hybrid_property
    def sales_order_id(self):
        return self.sales_order.id if self.sales_order else None

    @hybrid_property
    def contact_id(self):
        return self.person_id

    @contact_id.expression
    def contact_id(cls):
        return cls.person_id


class CrmQuoteLineItem(Base):
    __tablename__ = "crm_quote_line_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_quotes.id"), nullable=False
    )
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_items.id")
    )
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1.000"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    quote = relationship("Quote", back_populates="line_items")
    inventory_item = relationship("InventoryItem")
