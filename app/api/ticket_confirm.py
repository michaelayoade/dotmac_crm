"""Public JSON API for customer ticket-resolution confirmation — token-authorized,
no login. The magic-link token is the capability, exactly like "Track My Visit".
Mounted under ``/api/v1``; covered by the global API rate limiter.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.tickets import ticket_access_tokens, tickets

router = APIRouter(prefix="/ticket-confirm", tags=["ticket-confirm"])

_STATE_TO_STATUS = {"not_found": 404, "expired": 410, "closed": 410}


class TicketDisputeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


def _load_token_or_error(db: Session, token: str):
    token_row = ticket_access_tokens.get_by_token(db, token)
    state = ticket_access_tokens.token_state(token_row)
    if token_row is None or state != "ok":
        raise HTTPException(status_code=_STATE_TO_STATUS.get(state, 404), detail=f"Confirmation link {state}")
    return token_row


@router.get("/{token}")
def get_confirmation_state(token: str, db: Session = Depends(get_db)) -> dict:
    """The ticket's resolution state for the confirmation landing page."""
    token_row = _load_token_or_error(db, token)
    ticket_access_tokens.mark_accessed(db, token_row)
    ticket = token_row.ticket
    return {
        "available": True,
        "ticket_ref": ticket.number or str(ticket.id),
        "subject": ticket.title,
        "status": ticket.status.value,
        "resolved_at": ticket.resolved_at,
    }


@router.post("/{token}/confirm")
def confirm_resolution(token: str, db: Session = Depends(get_db)) -> dict:
    """Customer confirms the fix → the ticket closes (idempotent)."""
    token_row = _load_token_or_error(db, token)
    ticket = tickets.confirm_resolution(db, token_row)
    return {"confirmed": True, "status": ticket.status.value}


@router.post("/{token}/dispute")
def dispute_resolution(token: str, payload: TicketDisputeRequest, db: Session = Depends(get_db)) -> dict:
    """Customer says it isn't fixed → the ticket reopens."""
    token_row = _load_token_or_error(db, token)
    ticket = tickets.dispute_resolution(db, token_row, reason=payload.reason)
    return {"disputed": True, "status": ticket.status.value}
