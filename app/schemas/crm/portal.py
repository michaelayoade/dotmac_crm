"""Schemas for the customer Portal API (RFC #73)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PortalSessionMintRequest(BaseModel):
    """Trusted server-to-server mint request (sub backend asserts the subject)."""

    crm_subscriber_id: str = Field(..., description="CRM subscriber/org id to scope the token to")
    actor: str = Field(default="subscriber", description="'subscriber' or 'reseller'")
    scopes: list[str] = Field(default_factory=list, description="Granted portal scopes")


class PortalSessionMintResponse(BaseModel):
    portal_token: str
    expires_at: int = Field(..., description="Unix epoch seconds")
    api_base: str = Field(default="/api/v1/portal")


class PortalMeResponse(BaseModel):
    """Echoes the scoped subject behind the portal token (whoami)."""

    subject_id: str
    actor: str
    scopes: list[str]


class PortalReferralProgram(BaseModel):
    """Advertised program terms shown on the Refer & Earn card."""

    enabled: bool
    reward_amount: str = Field(..., description="Reward per qualified referral, as a decimal string")
    reward_currency: str = "NGN"


class PortalReferralTotals(BaseModel):
    total: int = 0
    pending: int = 0
    qualified: int = 0
    rewarded: int = 0
    total_earned: str = Field(default="0", description="Sum of rewarded amounts, as a decimal string")


class PortalReferralItem(BaseModel):
    id: str
    status: str
    referred_name: str | None = None
    reward_amount: str | None = None
    reward_currency: str = "NGN"
    reward_status: str
    created_at: str
    qualified_at: str | None = None


class PortalReferralsResponse(BaseModel):
    """The signed-in subscriber's referral code, program terms, and history."""

    code: str
    share_url: str
    program: PortalReferralProgram
    totals: PortalReferralTotals
    referrals: list[PortalReferralItem] = Field(default_factory=list)


class PortalReferRequest(BaseModel):
    """Refer a friend: at least one of email/phone is required."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    note: str | None = None


class PortalReferResponse(BaseModel):
    id: str
    status: str
    created_at: str
    message: str = "Referral submitted"
