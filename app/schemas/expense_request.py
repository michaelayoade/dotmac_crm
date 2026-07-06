from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.expense_request import ExpenseRequestERPSyncStatus, ExpenseRequestStatus


class ExpenseRequestItemBase(BaseModel):
    category_code: str = Field(min_length=1, max_length=30)
    category_name: str | None = Field(default=None, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    amount: Decimal = Field(gt=0, le=Decimal("999999999999.99"))
    expense_date: date | None = None
    vendor_name: str | None = Field(default=None, max_length=200)
    receipt_url: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class ExpenseRequestItemCreate(ExpenseRequestItemBase):
    pass


class ExpenseRequestItemRead(ExpenseRequestItemBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    expense_request_id: UUID
    created_at: datetime


class ExpenseRequestBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ticket_id: UUID | None = None
    project_id: UUID | None = None
    work_order_id: UUID | None = None
    requested_by_person_id: UUID
    purpose: str = Field(min_length=1, max_length=500)
    expense_date: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class ExpenseRequestCreate(ExpenseRequestBase):
    items: list[ExpenseRequestItemCreate] = Field(min_length=1, max_length=50)


class ExpenseRequestRead(ExpenseRequestBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    status: ExpenseRequestStatus
    number: str | None = None
    rejection_reason: str | None = None
    erp_expense_claim_id: str | None = None
    erp_claim_number: str | None = None
    erp_claim_status: str | None = None
    erp_sync_status: ExpenseRequestERPSyncStatus | None = None
    erp_sync_error: str | None = None
    total_amount: Decimal = Decimal("0")
    is_active: bool
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    paid_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    items: list[ExpenseRequestItemRead] = []


class ExpenseCategoryRead(BaseModel):
    category_code: str
    category_name: str
    requires_receipt: bool = False
    max_amount_per_claim: Decimal | None = None
