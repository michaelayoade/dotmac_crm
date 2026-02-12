"""Subscriber and reseller schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.subscriber import SubscriberStatus

# ============================================================================
# Subscriber Schemas (for external sync)
# ============================================================================


class SubscriberBase(BaseModel):
    """Base subscriber schema."""

    person_id: UUID | None = None
    organization_id: UUID | None = None
    external_id: str | None = None
    external_system: str | None = None
    subscriber_number: str | None = None
    account_number: str | None = None
    status: SubscriberStatus = SubscriberStatus.active
    service_name: str | None = None
    service_plan: str | None = None
    service_speed: str | None = None
    service_address_line1: str | None = None
    service_address_line2: str | None = None
    service_city: str | None = None
    service_region: str | None = None
    service_postal_code: str | None = None
    service_country_code: str | None = None
    balance: str | None = None
    currency: str | None = None
    billing_cycle: str | None = None
    next_bill_date: datetime | None = None
    activated_at: datetime | None = None
    notes: str | None = None


class SubscriberCreate(SubscriberBase):
    """Schema for creating a subscriber."""

    pass


class SubscriberUpdate(BaseModel):
    """Schema for updating a subscriber.

    Only exposes fields that may be edited manually.  Other fields
    (service_name, service_plan, balance, etc.) are managed exclusively
    by the external sync pipeline and should not be set via this schema.
    """

    person_id: UUID | None = None
    organization_id: UUID | None = None
    status: SubscriberStatus | None = None
    notes: str | None = None


class SubscriberSyncData(BaseModel):
    """Schema for syncing subscriber data from external system."""

    external_id: str = Field(..., description="ID in external system")
    subscriber_number: str | None = None
    account_number: str | None = None
    status: SubscriberStatus = SubscriberStatus.active
    service_name: str | None = None
    service_plan: str | None = None
    service_speed: str | None = None
    service_address_line1: str | None = None
    service_address_line2: str | None = None
    service_city: str | None = None
    service_region: str | None = None
    service_postal_code: str | None = None
    service_country_code: str | None = None
    balance: str | None = None
    currency: str | None = None
    billing_cycle: str | None = None
    next_bill_date: datetime | None = None
    activated_at: datetime | None = None
    suspended_at: datetime | None = None
    terminated_at: datetime | None = None
    person_email: str | None = Field(None, description="Email to match/link person")
    person_phone: str | None = Field(None, description="Phone to match/link person")
    sync_metadata: dict | None = None


class SubscriberBulkSync(BaseModel):
    """Schema for bulk sync from external system."""

    external_system: str = Field(..., description="External system identifier")
    subscribers: list[SubscriberSyncData]


class PersonSummary(BaseModel):
    """Summary of linked person."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None


class OrganizationSummary(BaseModel):
    """Summary of linked organization."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class SubscriberResponse(BaseModel):
    """Schema for subscriber response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    person_id: UUID | None = None
    organization_id: UUID | None = None
    sales_order_id: UUID | None = None
    external_id: str | None = None
    external_system: str | None = None
    subscriber_number: str | None = None
    account_number: str | None = None
    status: SubscriberStatus
    service_name: str | None = None
    service_plan: str | None = None
    service_speed: str | None = None
    service_address_line1: str | None = None
    service_address_line2: str | None = None
    service_city: str | None = None
    service_region: str | None = None
    service_postal_code: str | None = None
    service_country_code: str | None = None
    balance: str | None = None
    currency: str | None = None
    billing_cycle: str | None = None
    next_bill_date: datetime | None = None
    activated_at: datetime | None = None
    suspended_at: datetime | None = None
    terminated_at: datetime | None = None
    last_synced_at: datetime | None = None
    sync_error: str | None = None
    notes: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    person: PersonSummary | None = None
    organization: OrganizationSummary | None = None


class SubscriberListResponse(BaseModel):
    """Schema for paginated subscriber list."""

    items: list[SubscriberResponse]
    total: int
    page: int
    per_page: int
    pages: int


class SubscriberStats(BaseModel):
    """Schema for subscriber statistics."""

    total: int
    active: int
    suspended: int
    terminated: int
    pending: int
