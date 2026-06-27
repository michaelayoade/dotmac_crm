"""Public JSON API for "Track My Visit" — token-authorized, no user login.

Thin wrappers over ``app.services.field.tracking`` (the same service the web
``/track`` routes call). Mounted under ``/api/v1`` only — the web
``/track/{token}`` HTML page owns the root path. The visit token is the
capability; the global API rate limiter covers ``/api/*``.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.track import TrackRescheduleRequest
from app.services.field import tracking as tracking_service

router = APIRouter(prefix="/track", tags=["track"])

_STATE_TO_STATUS = {"not_found": 404, "expired": 410, "closed": 410}


def _load_token_or_error(db: Session, token: str):
    token_row = tracking_service.tokens.get_by_token(db, token)
    state = tracking_service.token_state(token_row)
    if token_row is None or state != "ok":
        raise HTTPException(status_code=_STATE_TO_STATUS.get(state, 404), detail=f"Tracking link {state}")
    return token_row


@router.get("/{token}")
def get_visit_state(token: str, db: Session = Depends(get_db)) -> dict:
    """Current visit state: status, ETA, timeline, technician, destination, live position."""
    token_row = _load_token_or_error(db, token)
    tracking_service.tokens.mark_accessed(db, token_row)
    return {"available": True, **tracking_service.public_state(db, token_row.work_order)}


@router.post("/{token}/confirm")
def confirm_visit(token: str, db: Session = Depends(get_db)) -> dict:
    """Customer confirms they'll be present (idempotent; 409 if the visit is closed)."""
    token_row = _load_token_or_error(db, token)
    return tracking_service.confirm_appointment(db, token_row)


@router.post("/{token}/reschedule")
def reschedule_visit(token: str, payload: TrackRescheduleRequest, db: Session = Depends(get_db)) -> dict:
    """Request a reschedule, routed to dispatch (409 if one is pending or the visit is complete)."""
    token_row = _load_token_or_error(db, token)
    return tracking_service.request_reschedule(
        db, token_row, note=payload.note, preferred_window=payload.preferred_window
    )
