from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.crm.enums import LeadStatus, QuoteStatus


class PipelineBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    is_active: bool = True
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class PipelineCreate(PipelineBase):
    pass


class PipelineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    is_active: bool | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class PipelineRead(PipelineBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class PipelineStageBase(BaseModel):
    pipeline_id: UUID
    name: str = Field(min_length=1, max_length=160)
    order_index: int = 0
    is_active: bool = True
    default_probability: int = Field(default=50, ge=0, le=100)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class PipelineStageCreate(PipelineStageBase):
    pass


class PipelineStageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    order_index: int | None = None
    is_active: bool | None = None
    default_probability: int | None = Field(default=None, ge=0, le=100)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class PipelineStageRead(PipelineStageBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class LeadBase(BaseModel):
    """Lead linked to a Person in the unified party model."""
    person_id: UUID  # Required - links to Person
    pipeline_id: UUID | None = None
    stage_id: UUID | None = None
    owner_agent_id: UUID | None = None
    title: str | None = Field(default=None, max_length=200)
    status: LeadStatus = LeadStatus.new
    estimated_value: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    probability: int | None = Field(default=None, ge=0, le=100)
    expected_close_date: date | None = None
    lost_reason: str | None = Field(default=None, max_length=200)
    notes: str | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")
    is_active: bool = True


class LeadCreate(LeadBase):
    pass


class LeadUpdate(BaseModel):
    person_id: UUID | None = None
    pipeline_id: UUID | None = None
    stage_id: UUID | None = None
    owner_agent_id: UUID | None = None
    title: str | None = Field(default=None, max_length=200)
    status: LeadStatus | None = None
    estimated_value: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    probability: int | None = Field(default=None, ge=0, le=100)
    expected_close_date: date | None = None
    lost_reason: str | None = Field(default=None, max_length=200)
    notes: str | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")
    is_active: bool | None = None


class LeadRead(LeadBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    weighted_value: Decimal | None = None
    created_at: datetime
    updated_at: datetime


class QuoteBase(BaseModel):
    """Quote linked to a Person in the unified party model."""
    person_id: UUID  # Required - links to Person
    lead_id: UUID | None = None
    status: QuoteStatus = QuoteStatus.draft
    currency: str = Field(default="NGN", min_length=3, max_length=3)
    subtotal: Decimal = Decimal("0.00")
    tax_total: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")
    expires_at: datetime | None = None
    notes: str | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")
    is_active: bool = True


class QuoteCreate(QuoteBase):
    pass


class QuoteUpdate(BaseModel):
    person_id: UUID | None = None
    lead_id: UUID | None = None
    status: QuoteStatus | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    subtotal: Decimal | None = None
    tax_total: Decimal | None = None
    total: Decimal | None = None
    expires_at: datetime | None = None
    notes: str | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")
    is_active: bool | None = None


class QuoteRead(QuoteBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    sales_order_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class QuoteLineItemBase(BaseModel):
    quote_id: UUID
    inventory_item_id: UUID | None = None
    description: str = Field(min_length=1, max_length=255)
    quantity: Decimal = Field(default=Decimal("1.000"), gt=0)
    unit_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class QuoteLineItemCreate(QuoteLineItemBase):
    pass


class QuoteLineItemUpdate(BaseModel):
    inventory_item_id: UUID | None = None
    description: str | None = Field(default=None, min_length=1, max_length=255)
    quantity: Decimal | None = Field(default=None, gt=0)
    unit_price: Decimal | None = Field(default=None, ge=0)
    amount: Decimal | None = Field(default=None, ge=0)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class QuoteLineItemRead(QuoteLineItemBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
