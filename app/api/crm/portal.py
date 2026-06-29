"""Customer Portal API (RFC #73) — foundation.

Two surfaces:
 - ``/portal/internal/session`` — trusted server-to-server mint (the sub backend,
   already authenticated as a service account, asserts the subject). Gated like
   the chat-widget mint.
 - ``/portal/*`` — customer/reseller-scoped data routes, each authorized by the
   minted portal token via ``require_portal_auth``.

This PR lands the foundation + a ``/portal/me`` whoami that proves the
token-mint → scoped-access rails end-to-end. Feature verticals (referrals,
projects, work orders, quotes) build on top.
"""

from __future__ import annotations

import os
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.logging import get_logger
from app.models.person import Person
from app.models.subscriber import Subscriber
from app.schemas.crm.portal import (
    PortalMeResponse,
    PortalProjectsResponse,
    PortalReferralItem,
    PortalReferralProgram,
    PortalReferralsResponse,
    PortalReferralTotals,
    PortalReferRequest,
    PortalReferResponse,
    PortalSessionMintRequest,
    PortalSessionMintResponse,
    PortalWorkOrdersResponse,
)
from app.services.auth_dependencies import require_user_auth
from app.services.auth_flow import create_portal_token
from app.services.common import coerce_uuid
from app.services.crm.referrals import referrals as referrals_service
from app.services.portal_auth import PortalPrincipal, require_portal_auth
from app.services.projects import Projects as projects_service
from app.services.workforce import WorkOrders as work_orders_service

logger = get_logger(__name__)

_ALLOWED_ACTORS = {"subscriber", "reseller"}


def require_portal_mint(
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
) -> dict:
    """Restrict portal-token minting to trusted service principal(s).

    Mirrors the chat-widget mint gate: layered on ``require_user_auth``, the
    caller's account email must be in ``PORTAL_MINT_SERVICE_ACCOUNTS`` (falls
    back to ``CHAT_MINT_SERVICE_ACCOUNTS`` / the sub self-care sync account).
    """
    raw = (
        os.getenv("PORTAL_MINT_SERVICE_ACCOUNTS")
        or os.getenv("CHAT_MINT_SERVICE_ACCOUNTS")
        or "selfcare-sync@dotmac.io"
    )
    allowed = {e.strip().lower() for e in raw.split(",") if e.strip()}
    person = db.get(Person, coerce_uuid(auth.get("person_id")))
    email = (getattr(person, "email", "") or "").strip().lower()
    if email and email in allowed:
        return auth
    logger.warning("portal_mint_forbidden person_id=%s", auth.get("person_id"))
    raise HTTPException(status_code=403, detail="Not permitted to mint portal sessions")


# Trusted mint (mounted behind require_user_auth in main.py).
internal_router = APIRouter(prefix="/portal/internal", tags=["portal-internal"])


@internal_router.post("/session", response_model=PortalSessionMintResponse)
def mint_portal_session(
    payload: PortalSessionMintRequest,
    auth: dict = Depends(require_portal_mint),
    db: Session = Depends(get_db),
) -> PortalSessionMintResponse:
    if payload.actor not in _ALLOWED_ACTORS:
        raise HTTPException(status_code=422, detail="Invalid actor")
    subject = (payload.crm_subscriber_id or "").strip()
    if not subject:
        raise HTTPException(status_code=422, detail="crm_subscriber_id is required")
    token, expires_at = create_portal_token(db, subject_id=subject, actor=payload.actor, scopes=payload.scopes)
    return PortalSessionMintResponse(portal_token=token, expires_at=expires_at)


# Customer/reseller-scoped data routes (authorized by the portal token).
router = APIRouter(prefix="/portal", tags=["portal"])


@router.get("/me", response_model=PortalMeResponse)
def portal_me(
    principal: PortalPrincipal = Depends(require_portal_auth),
) -> PortalMeResponse:
    return PortalMeResponse(
        subject_id=principal.subject_id,
        actor=principal.actor,
        scopes=sorted(principal.scopes),
    )


# --- Refer & Earn (RFC #73, increment 2) ----------------------------------


def _referrer_person_id(db: Session, principal: PortalPrincipal) -> str:
    """Resolve the portal subject to the CRM Person that owns referrals.

    The portal token subject is a CRM ``Subscriber`` id; referrals are keyed on
    the subscriber's ``person_id``. Referrals are a subscriber-only feature.
    """
    if principal.actor != "subscriber":
        raise HTTPException(status_code=403, detail="Referrals are available to subscribers only")
    subscriber = db.get(Subscriber, coerce_uuid(principal.subject_id))
    if subscriber is None or subscriber.person_id is None:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    return str(subscriber.person_id)


def _share_url(code: str) -> str:
    base = (os.getenv("PORTAL_REFERRAL_SHARE_BASE") or "https://app.dotmac.io").rstrip("/")
    return f"{base}/r/{code}"


@router.get("/referrals", response_model=PortalReferralsResponse)
def portal_list_referrals(
    principal: PortalPrincipal = Depends(require_portal_auth),
    db: Session = Depends(get_db),
) -> PortalReferralsResponse:
    principal.require_scope("referrals:read")
    person_id = _referrer_person_id(db, principal)
    code = referrals_service.ensure_code(db, person_id)
    rows = referrals_service.list(db, referrer_person_id=person_id, limit=100)
    program = referrals_service.program(db)

    # Friendly names for referred prospects, batched to avoid N+1.
    referred_ids = [r.referred_person_id for r in rows if r.referred_person_id]
    names: dict = {}
    if referred_ids:
        for p in db.query(Person).filter(Person.id.in_(referred_ids)).all():
            names[p.id] = (p.display_name or f"{p.first_name} {p.last_name}".strip()) or None

    totals = PortalReferralTotals()
    earned = Decimal("0")
    items: list[PortalReferralItem] = []
    for r in rows:
        status = r.status.value
        totals.total += 1
        if status == "pending":
            totals.pending += 1
        elif status == "qualified":
            totals.qualified += 1
        elif status == "rewarded":
            totals.rewarded += 1
            earned += r.reward_amount or Decimal("0")
        items.append(
            PortalReferralItem(
                id=str(r.id),
                status=status,
                referred_name=names.get(r.referred_person_id),
                reward_amount=str(r.reward_amount) if r.reward_amount is not None else None,
                reward_currency=r.reward_currency or "NGN",
                reward_status=r.reward_status.value,
                created_at=r.created_at.isoformat(),
                qualified_at=r.qualified_at.isoformat() if r.qualified_at else None,
            )
        )
    totals.total_earned = str(earned)

    return PortalReferralsResponse(
        code=code.code,
        share_url=_share_url(code.code),
        program=PortalReferralProgram(
            enabled=program["enabled"],
            reward_amount=str(program["amount"]),
            reward_currency=program["currency"],
        ),
        totals=totals,
        referrals=items,
    )


@router.post("/referrals", response_model=PortalReferResponse, status_code=201)
def portal_refer_a_friend(
    payload: PortalReferRequest,
    principal: PortalPrincipal = Depends(require_portal_auth),
    db: Session = Depends(get_db),
) -> PortalReferResponse:
    principal.require_scope("referrals:write")
    person_id = _referrer_person_id(db, principal)
    code = referrals_service.ensure_code(db, person_id)
    referral = referrals_service.capture(
        db,
        code=code.code,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        notes=payload.note,
        source="portal",
    )
    return PortalReferResponse(
        id=str(referral.id),
        status=referral.status.value,
        created_at=referral.created_at.isoformat(),
    )


@router.get("/projects", response_model=PortalProjectsResponse)
def portal_list_projects(
    principal: PortalPrincipal = Depends(require_portal_auth),
    db: Session = Depends(get_db),
) -> PortalProjectsResponse:
    """The subscriber's installations/projects with stage timeline + progress %
    (Installation tracker; consumed by the dotmac_sub mirror)."""
    principal.require_scope("projects:read")
    projects = projects_service.portal_list(db, principal.subject_id)
    payload = {"projects": projects, "total": len(projects)}
    return PortalProjectsResponse.model_validate(payload)


@router.get("/work-orders", response_model=PortalWorkOrdersResponse)
def portal_list_work_orders(
    principal: PortalPrincipal = Depends(require_portal_auth),
    db: Session = Depends(get_db),
) -> PortalWorkOrdersResponse:
    """The subscriber's field-service work orders (status, schedule, ETA,
    technician; consumed by the dotmac_sub mirror)."""
    principal.require_scope("work_orders:read")
    work_orders = work_orders_service.portal_list(db, principal.subject_id)
    payload = {"work_orders": work_orders, "total": len(work_orders)}
    return PortalWorkOrdersResponse.model_validate(payload)
