"""Schemas for the referral program."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.crm.referral import ReferralRewardStatus, ReferralStatus


class ReferralCodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    person_id: UUID
    code: str
    is_active: bool
    created_at: datetime


class ReferralCaptureRequest(BaseModel):
    """Public capture payload: a prospect signing up via a referral code."""

    code: str = Field(min_length=1, max_length=24)
    name: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    region: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=1000)


class ReferralRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    referrer_person_id: UUID
    referral_code_id: UUID | None
    referred_person_id: UUID | None
    referred_lead_id: UUID | None
    referred_subscriber_id: UUID | None
    status: ReferralStatus
    reward_amount: Decimal | None
    reward_currency: str
    reward_status: ReferralRewardStatus
    reward_issued_at: datetime | None
    qualified_at: datetime | None
    source: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ReferralRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
