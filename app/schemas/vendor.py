from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.vendor import (
    AsBuiltRouteStatus,
    InstallationProjectStatus,
    ProjectQuoteStatus,
    ProposedRouteRevisionStatus,
    VendorAssignmentType,
)


class VendorBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    code: str | None = Field(default=None, max_length=60)
    contact_name: str | None = Field(default=None, max_length=160)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    license_number: str | None = Field(default=None, max_length=120)
    service_area: str | None = None
    is_active: bool = True
    notes: str | None = None
    erp_id: str | None = Field(default=None, max_length=100)


class VendorCreate(VendorBase):
    pass


class VendorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    code: str | None = Field(default=None, max_length=60)
    contact_name: str | None = Field(default=None, max_length=160)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    license_number: str | None = Field(default=None, max_length=120)
    service_area: str | None = None
    is_active: bool | None = None
    notes: str | None = None
    erp_id: str | None = Field(default=None, max_length=100)


class VendorRead(VendorBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class InstallationProjectBase(BaseModel):
    project_id: UUID
    buildout_project_id: UUID | None = None
    subscriber_id: UUID | None = None
    address_id: UUID | None = None
    assigned_vendor_id: UUID | None = None
    assignment_type: VendorAssignmentType | None = None
    status: InstallationProjectStatus = InstallationProjectStatus.draft
    bidding_open_at: datetime | None = None
    bidding_close_at: datetime | None = None
    approved_quote_id: UUID | None = None
    created_by_person_id: UUID | None = None
    notes: str | None = None
    is_active: bool = True


class InstallationProjectCreate(InstallationProjectBase):
    pass


class InstallationProjectUpdate(BaseModel):
    buildout_project_id: UUID | None = None
    subscriber_id: UUID | None = None
    address_id: UUID | None = None
    assigned_vendor_id: UUID | None = None
    assignment_type: VendorAssignmentType | None = None
    status: InstallationProjectStatus | None = None
    bidding_open_at: datetime | None = None
    bidding_close_at: datetime | None = None
    approved_quote_id: UUID | None = None
    notes: str | None = None
    is_active: bool | None = None


class InstallationProjectRead(InstallationProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ProjectQuoteBase(BaseModel):
    project_id: UUID
    vendor_id: UUID | None = None
    status: ProjectQuoteStatus = ProjectQuoteStatus.draft
    currency: str = Field(default="NGN", max_length=3)
    subtotal: Decimal = Decimal("0.00")
    tax_total: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    reviewed_by_person_id: UUID | None = None
    review_notes: str | None = None
    created_by_person_id: UUID | None = None
    is_active: bool = True


class ProjectQuoteCreate(BaseModel):
    project_id: UUID


class ProjectQuoteUpdate(BaseModel):
    status: ProjectQuoteStatus | None = None
    review_notes: str | None = None
    reviewed_by_person_id: UUID | None = None


class ProjectQuoteRead(ProjectQuoteBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class QuoteLineItemBase(BaseModel):
    quote_id: UUID
    item_type: str | None = Field(default=None, max_length=80)
    description: str | None = None
    cable_type: str | None = Field(default=None, max_length=120)
    fiber_count: int | None = None
    splice_count: int | None = None
    quantity: Decimal = Decimal("1.000")
    unit_price: Decimal = Decimal("0.00")
    amount: Decimal = Decimal("0.00")
    notes: str | None = None
    is_active: bool = True


class QuoteLineItemCreate(QuoteLineItemBase):
    pass


class QuoteLineItemCreateRequest(BaseModel):
    item_type: str | None = Field(default=None, max_length=80)
    description: str | None = None
    cable_type: str | None = Field(default=None, max_length=120)
    fiber_count: int | None = None
    splice_count: int | None = None
    quantity: Decimal = Decimal("1.000")
    unit_price: Decimal = Decimal("0.00")
    amount: Decimal = Decimal("0.00")
    notes: str | None = None
    is_active: bool = True


class QuoteLineItemUpdate(BaseModel):
    item_type: str | None = Field(default=None, max_length=80)
    description: str | None = None
    cable_type: str | None = Field(default=None, max_length=120)
    fiber_count: int | None = None
    splice_count: int | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    notes: str | None = None
    is_active: bool | None = None


class QuoteLineItemRead(QuoteLineItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ProposedRouteRevisionBase(BaseModel):
    quote_id: UUID
    revision_number: int
    status: ProposedRouteRevisionStatus = ProposedRouteRevisionStatus.draft
    length_meters: float | None = None
    submitted_at: datetime | None = None
    submitted_by_person_id: UUID | None = None
    reviewed_at: datetime | None = None
    reviewed_by_person_id: UUID | None = None
    review_notes: str | None = None


class ProposedRouteRevisionCreate(BaseModel):
    quote_id: UUID
    geojson: dict
    length_meters: float | None = None


class ProposedRouteRevisionCreateRequest(BaseModel):
    geojson: dict
    length_meters: float | None = None


class ProposedRouteRevisionRead(ProposedRouteRevisionBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


class AsBuiltRouteBase(BaseModel):
    project_id: UUID
    proposed_revision_id: UUID | None = None
    status: AsBuiltRouteStatus = AsBuiltRouteStatus.submitted
    actual_length_meters: float | None = None
    submitted_at: datetime | None = None
    submitted_by_person_id: UUID | None = None
    reviewed_at: datetime | None = None
    reviewed_by_person_id: UUID | None = None
    review_notes: str | None = None
    fiber_segment_id: UUID | None = None
    report_file_name: str | None = None
    report_generated_at: datetime | None = None


class AsBuiltRouteCreate(BaseModel):
    project_id: UUID
    proposed_revision_id: UUID | None = None
    geojson: dict
    actual_length_meters: float | None = None


class AsBuiltRouteRead(AsBuiltRouteBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class InstallationProjectNoteCreate(BaseModel):
    project_id: UUID
    author_person_id: UUID | None = None
    body: str
    is_internal: bool = False
    attachments: list | None = None


class InstallationProjectNoteRead(InstallationProjectNoteCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


class ProjectBidOpenRequest(BaseModel):
    bid_days: int | None = Field(default=None, ge=1)


class QuoteApprovalRequest(BaseModel):
    reviewer_person_id: UUID
    review_notes: str | None = None
    override_threshold: bool = False


class QuoteRejectRequest(BaseModel):
    reviewer_person_id: UUID
    review_notes: str | None = None


class AsBuiltCompareResponse(BaseModel):
    proposed_geojson: dict | None = None
    as_built_geojson: dict | None = None
