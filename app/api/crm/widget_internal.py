"""Trusted server-to-server endpoint for minting identified chat sessions.

Unlike the public, browser-driven widget endpoints (origin-validated, IP
rate-limited, visitor-supplied email), this is called by a trusted, authenticated
backend (e.g. the DotMac Sub self-care app, logged in as a service user) which
has *already* authenticated the end user. The caller asserts the visitor's
identity, so the session is minted already-identified and the browser/app never
touches the spoofable public ``identify`` flow.

Mounted behind ``require_user_auth`` (main.py) and additionally gated by
``require_chat_mint`` to the trusted service principal(s) in
``CHAT_MINT_SERVICE_ACCOUNTS``.
"""

from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.logging import get_logger
from app.models.person import Person
from app.schemas.crm.chat_widget import WidgetSessionCreate, WidgetSessionRead
from app.services.auth_dependencies import require_user_auth
from app.services.common import coerce_uuid
from app.services.crm.chat_widget import widget_visitors

logger = get_logger(__name__)

router = APIRouter(prefix="/widget/internal", tags=["widget-internal"])


def require_chat_mint(
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
) -> dict:
    """Restrict minting to the trusted service principal(s).

    Layered on top of ``require_user_auth``: the caller must be authenticated AND
    its account email must be in ``CHAT_MINT_SERVICE_ACCOUNTS`` (comma-separated;
    defaults to the DotMac Sub self-care sync account). This is the
    server-to-server boundary — only the sub backend may assert visitor identity.
    """
    allowed = {
        e.strip().lower()
        for e in os.getenv("CHAT_MINT_SERVICE_ACCOUNTS", "selfcare-sync@dotmac.io").split(",")
        if e.strip()
    }
    person = db.get(Person, coerce_uuid(auth.get("person_id")))
    email = (getattr(person, "email", "") or "").strip().lower()
    if email and email in allowed:
        return auth
    logger.warning("widget_mint_forbidden person_id=%s email=%s", auth.get("person_id"), email)
    raise HTTPException(status_code=403, detail="Not permitted to mint chat sessions")


class WidgetInternalSessionCreate(BaseModel):
    """Trusted-caller request to mint an identified visitor session."""

    config_id: UUID
    email: str = Field(..., min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=160)
    # Optional cross-system identifiers carried into session metadata for agent
    # context and downstream routing/notification (not used to authenticate).
    crm_subscriber_id: UUID | None = None
    metadata: dict | None = None


@router.post("/session", response_model=WidgetSessionRead)
def mint_internal_session(
    payload: WidgetInternalSessionCreate,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_chat_mint),
):
    """Create an already-identified chat_widget session for a trusted caller.

    Returns the same shape as the public create-session endpoint; the caller
    hands the opaque ``visitor_token`` to its client, which then talks to the
    public ``/widget/session/...`` REST + ``/ws/widget`` endpoints directly.
    """
    try:
        # No fingerprint/origin/IP-rate-limit: the caller is trusted and
        # supplies the identity itself.
        session, _token = widget_visitors.create_session(
            db,
            payload.config_id,
            WidgetSessionCreate(),
            user_agent="dotmac-internal",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    session = widget_visitors.identify_visitor(
        db,
        session,
        email=payload.email,
        name=payload.name,
    )

    # Tag session metadata so agents see the originating surface and downstream
    # notifications (mobile push) can resolve the subscriber.
    metadata = dict(session.metadata_ or {})
    if payload.metadata:
        metadata.update(payload.metadata)
    if payload.crm_subscriber_id:
        metadata["crm_subscriber_id"] = str(payload.crm_subscriber_id)
    session.metadata_ = metadata
    db.commit()
    db.refresh(session)

    logger.info(
        "widget_internal_session_minted session_id=%s person_id=%s surface=%s",
        session.id,
        session.person_id,
        metadata.get("surface"),
    )

    return WidgetSessionRead(
        session_id=session.id,
        visitor_token=session.visitor_token,
        conversation_id=session.conversation_id,
        is_identified=session.is_identified,
        identified_name=session.identified_name,
    )
