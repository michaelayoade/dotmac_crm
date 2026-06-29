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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.logging import get_logger
from app.models.person import Person
from app.schemas.crm.portal import (
    PortalMeResponse,
    PortalSessionMintRequest,
    PortalSessionMintResponse,
)
from app.services.auth_dependencies import require_user_auth
from app.services.auth_flow import create_portal_token
from app.services.common import coerce_uuid
from app.services.portal_auth import PortalPrincipal, require_portal_auth

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
    raw = os.getenv("PORTAL_MINT_SERVICE_ACCOUNTS") or os.getenv(
        "CHAT_MINT_SERVICE_ACCOUNTS", "selfcare-sync@dotmac.io"
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
