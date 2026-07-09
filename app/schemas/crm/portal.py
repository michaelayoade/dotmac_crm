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


class PortalProjectStage(BaseModel):
    key: str | None = None
    title: str
    status: str = "pending"
    completed_at: str | None = None


class PortalProjectItem(BaseModel):
    id: str
    name: str
    status: str
    project_type: str | None = None
    progress_pct: int = 0
    current_stage: str | None = None
    stages: list[PortalProjectStage] = Field(default_factory=list)
    customer_address: str | None = None
    region: str | None = None
    start_at: str | None = None
    due_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None


class PortalProjectsResponse(BaseModel):
    projects: list[PortalProjectItem] = Field(default_factory=list)
    total: int = 0


class PortalWorkOrderItem(BaseModel):
    id: str
    title: str
    description: str | None = None
    status: str
    work_type: str | None = None
    priority: str | None = None
    ticket_id: str | None = None
    project_id: str | None = None
    assigned_to_person_id: str | None = None
    assigned_to_name: str | None = None
    technician_name: str | None = None
    technician_phone: str | None = None
    address: str | None = None
    scheduled_start: str | None = None
    scheduled_end: str | None = None
    estimated_arrival_at: str | None = None
    estimated_duration_minutes: int | None = None
    started_at: str | None = None
    paused_at: str | None = None
    resumed_at: str | None = None
    completed_at: str | None = None
    total_active_seconds: int | None = None
    required_skills: list | None = None
    tags: list | None = None
    access_notes: str | None = None
    is_active: bool = True
    metadata: dict | None = None
    created_at: str | None = None


class PortalWorkOrdersResponse(BaseModel):
    work_orders: list[PortalWorkOrderItem] = Field(default_factory=list)
    total: int = 0


class PortalTechnicianLocation(BaseModel):
    """Live position of the technician on an active work order, for the customer
    'where's my technician' map. ``available`` is False (with a ``reason``) when
    the map should be hidden — outside the Start work → End work window, no
    technician assigned, sharing off, or no GPS fix yet."""

    available: bool = False
    reason: str | None = None
    work_order_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    accuracy_m: float | None = None
    updated_at: str | None = None
    estimated_arrival_at: str | None = None


class PortalTechnicianRatingRequest(BaseModel):
    """Customer's rating of the technician after a completed work order."""

    rating: int = Field(..., ge=1, le=5, description="1-5 star rating")
    comment: str | None = Field(default=None, max_length=2000)


class PortalTechnicianRatingResponse(BaseModel):
    ok: bool = True
    already_rated: bool = False
    rating: int | None = None
    work_order_id: str | None = None


# --- Self-serve quotes (Sales/Quotes vertical) ----------------------------


class PortalQuoteRequest(BaseModel):
    """Map-pinned installation quote request.

    ``for_subscriber_id`` is required for reseller actors (the target customer);
    ignored for subscriber actors (always scoped to self).
    """

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    address: str | None = None
    region: str | None = None
    note: str | None = None
    for_subscriber_id: str | None = None


class PortalQuoteAcceptRequest(BaseModel):
    """Accept a quote after the deposit has been verified by the sub backend."""

    deposit_reference: str = Field(..., description="Verified deposit payment reference")
    deposit_amount: str = Field(..., description="Deposit amount paid, as a decimal string")
    provider: str | None = Field(default=None, description="Payment provider (e.g. paystack)")


class PortalQuoteFeasibility(BaseModel):
    coverage: str | None = None  # covered | survey_required | out_of_area
    feasible: bool | None = None
    distance_meters: float | None = None
    nearest_fap_name: str | None = None


class PortalQuoteLineItem(BaseModel):
    description: str
    quantity: str
    unit_price: str
    amount: str


class PortalQuoteItem(BaseModel):
    id: str
    status: str
    currency: str
    subtotal: str
    tax_total: str
    total: str
    project_type: str | None = None
    subscriber_id: str | None = None
    subscriber_external_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    address: str | None = None
    region: str | None = None
    feasibility: PortalQuoteFeasibility = Field(default_factory=PortalQuoteFeasibility)
    estimate_provisional: bool = False
    deposit_percent: int = 0
    deposit_amount: str = "0"
    deposit_paid: bool = False
    deposit_reference: str | None = None
    line_items: list[PortalQuoteLineItem] = Field(default_factory=list)
    sales_order_id: str | None = None
    project_id: str | None = None
    already_accepted: bool = False
    created_at: str | None = None
    expires_at: str | None = None


class PortalQuotesResponse(BaseModel):
    quotes: list[PortalQuoteItem] = Field(default_factory=list)
    total: int = 0
