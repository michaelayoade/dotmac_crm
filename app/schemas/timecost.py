from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkLogBase(BaseModel):
    work_order_id: UUID
    person_id: UUID
    start_at: datetime
    end_at: datetime | None = None
    minutes: int = Field(default=0, ge=0)
    hourly_rate: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None
    is_active: bool = True


class WorkLogCreate(WorkLogBase):
    pass


class WorkLogUpdate(BaseModel):
    start_at: datetime | None = None
    end_at: datetime | None = None
    minutes: int | None = Field(default=None, ge=0)
    hourly_rate: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None
    is_active: bool | None = None


class WorkLogRead(WorkLogBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ExpenseLineBase(BaseModel):
    work_order_id: UUID | None = None
    project_id: UUID | None = None
    amount: Decimal = Field(ge=0)
    currency: str = Field(default="NGN", min_length=3, max_length=3)
    description: str | None = None
    is_active: bool = True


class ExpenseLineCreate(ExpenseLineBase):
    pass


class ExpenseLineUpdate(BaseModel):
    work_order_id: UUID | None = None
    project_id: UUID | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    description: str | None = None
    is_active: bool | None = None


class ExpenseLineRead(ExpenseLineBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class CostRateBase(BaseModel):
    person_id: UUID | None = None
    hourly_rate: Decimal = Field(ge=0)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    is_active: bool = True


class CostRateCreate(CostRateBase):
    pass


class CostRateUpdate(BaseModel):
    person_id: UUID | None = None
    hourly_rate: Decimal | None = Field(default=None, ge=0)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    is_active: bool | None = None


class CostRateRead(CostRateBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class BillingRateBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    hourly_rate: Decimal = Field(ge=0)
    currency: str = Field(default="NGN", min_length=3, max_length=3)
    is_active: bool = True


class BillingRateCreate(BillingRateBase):
    pass


class BillingRateUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    hourly_rate: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_active: bool | None = None


class BillingRateRead(BillingRateBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class CostSummary(BaseModel):
    work_order_id: UUID | None = None
    project_id: UUID | None = None
    labor_cost: Decimal
    expense_total: Decimal
    total_cost: Decimal
