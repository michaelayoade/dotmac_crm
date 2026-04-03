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
    VariationType,
    VendorAssignmentType,
    VendorPurchaseInvoiceStatus,
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
    erp_purchase_order_id: str | None = None
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
    erp_purchase_order_id: str | None = None
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
    vat_rate_percent: Decimal | None = Field(default=None, ge=0, le=100)
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
    vat_rate_percent: Decimal | None = Field(default=None, ge=0, le=100)


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
    quantity: Decimal = Field(default=Decimal("1.000"), ge=1)
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
    quantity: Decimal = Field(default=Decimal("1.000"), ge=1)
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
    quantity: Decimal | None = Field(default=None, ge=1)
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    notes: str | None = None
    is_active: bool | None = None


class QuoteLineItemRead(QuoteLineItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class VendorPurchaseInvoiceBase(BaseModel):
    project_id: UUID
    invoice_number: str | None = None
    vendor_id: UUID | None = None
    status: VendorPurchaseInvoiceStatus = VendorPurchaseInvoiceStatus.draft
    currency: str = Field(default="NGN", max_length=3)
    tax_rate_percent: Decimal | None = Field(default=None, ge=0, le=100)
    subtotal: Decimal = Decimal("0.00")
    tax_total: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    reviewed_by_person_id: UUID | None = None
    review_notes: str | None = None
    created_by_person_id: UUID | None = None
    attachment_storage_key: str | None = None
    attachment_file_name: str | None = None
    attachment_mime_type: str | None = None
    attachment_file_size: int | None = None
    erp_purchase_order_id: str | None = None
    erp_purchase_invoice_id: str | None = None
    erp_sync_error: str | None = None
    erp_synced_at: datetime | None = None
    is_active: bool = True


class VendorPurchaseInvoiceCreate(BaseModel):
    project_id: UUID


class VendorPurchaseInvoiceUpdate(BaseModel):
    tax_rate_percent: Decimal | None = Field(default=None, ge=0, le=100)
    review_notes: str | None = None
    erp_purchase_order_id: str | None = None
    erp_purchase_invoice_id: str | None = None
    erp_sync_error: str | None = None
    erp_synced_at: datetime | None = None


class VendorPurchaseInvoiceTaxRateUpdateRequest(BaseModel):
    tax_rate_percent: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)


class VendorPurchaseInvoiceRead(VendorPurchaseInvoiceBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class VendorPurchaseInvoiceLineItemBase(BaseModel):
    invoice_id: UUID
    item_type: str | None = Field(default=None, max_length=80)
    description: str | None = None
    quantity: Decimal = Field(default=Decimal("1.000"), ge=1)
    unit_price: Decimal = Decimal("0.00")
    amount: Decimal = Decimal("0.00")
    notes: str | None = None
    is_active: bool = True


class VendorPurchaseInvoiceLineItemCreate(VendorPurchaseInvoiceLineItemBase):
    pass


class VendorPurchaseInvoiceLineItemCreateRequest(BaseModel):
    item_type: str | None = Field(default=None, max_length=80)
    description: str | None = None
    quantity: Decimal = Field(default=Decimal("1.000"), ge=1)
    unit_price: Decimal = Decimal("0.00")
    amount: Decimal = Decimal("0.00")
    notes: str | None = None
    is_active: bool = True


class VendorPurchaseInvoiceLineItemUpdate(BaseModel):
    item_type: str | None = Field(default=None, max_length=80)
    description: str | None = None
    quantity: Decimal | None = Field(default=None, ge=1)
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    notes: str | None = None
    is_active: bool | None = None


class VendorPurchaseInvoiceLineItemRead(VendorPurchaseInvoiceLineItemBase):
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
    fiber_segment_id: UUID | None = None


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
    variation_type: VariationType | None = None
    variation_reason: str | None = None
    version: int = 1
    work_order_ref: str | None = Field(default=None, max_length=120)
    erp_sync_status: str | None = Field(default=None, max_length=40)
    erp_reference: str | None = Field(default=None, max_length=120)


class AsBuiltLineItemInput(BaseModel):
    item_type: str | None = Field(default=None, max_length=80)
    description: str | None = None
    cable_type: str | None = Field(default=None, max_length=120)
    fiber_count: int | None = None
    splice_count: int | None = None
    quantity: Decimal = Field(default=Decimal("1.000"), ge=1)
    unit_price: Decimal = Decimal("0.00")
    amount: Decimal | None = None
    notes: str | None = None
    is_active: bool = True


class AsBuiltLineItemRead(AsBuiltLineItemInput):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    as_built_id: UUID
    amount: Decimal
    created_at: datetime
    updated_at: datetime


class AsBuiltRouteCreate(BaseModel):
    project_id: UUID
    proposed_revision_id: UUID | None = None
    geojson: dict | None = None
    actual_length_meters: float | None = None
    line_items: list[AsBuiltLineItemInput] = Field(default_factory=list)
    variation_type: VariationType | None = None
    variation_reason: str | None = None
    work_order_ref: str | None = Field(default=None, max_length=120)


class AsBuiltRouteRead(AsBuiltRouteBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    line_items: list[AsBuiltLineItemRead] = Field(default_factory=list)


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


class QuoteVatUpdateRequest(BaseModel):
    vat_rate_percent: Decimal = Field(ge=0, le=100)
