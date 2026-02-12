"""Subscriber API endpoints."""

import math
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.subscriber import SubscriberStatus
from app.schemas.subscriber import (
    SubscriberBulkSync,
    SubscriberCreate,
    SubscriberListResponse,
    SubscriberResponse,
    SubscriberStats,
    SubscriberUpdate,
)
from app.services.subscriber import subscriber as subscriber_service

router = APIRouter(prefix="/subscribers", tags=["subscribers"])


@router.get("", response_model=SubscriberListResponse)
def list_subscribers(
    db: Session = Depends(get_db),
    search: str | None = Query(None, description="Search by number, name, or external ID"),
    status: SubscriberStatus | None = Query(None, description="Filter by status"),
    external_system: str | None = Query(None, description="Filter by external system"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List subscribers with pagination and filtering."""
    offset = (page - 1) * per_page

    items = subscriber_service.list(
        db,
        search=search,
        status=status,
        external_system=external_system,
        limit=per_page,
        offset=offset,
    )

    total = subscriber_service.count(
        db,
        search=search,
        status=status,
        external_system=external_system,
    )

    pages = math.ceil(total / per_page) if total > 0 else 1

    return SubscriberListResponse(
        items=[SubscriberResponse.model_validate(s) for s in items],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/stats", response_model=SubscriberStats)
def get_subscriber_stats(db: Session = Depends(get_db)):
    """Get subscriber statistics."""
    stats = subscriber_service.get_stats(db)
    return SubscriberStats(**stats)


@router.get("/{subscriber_id}", response_model=SubscriberResponse)
def get_subscriber(
    subscriber_id: UUID,
    db: Session = Depends(get_db),
):
    """Get a subscriber by ID."""
    sub = subscriber_service.get(db, subscriber_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    return SubscriberResponse.model_validate(sub)


@router.post("", response_model=SubscriberResponse, status_code=201)
def create_subscriber(
    data: SubscriberCreate,
    db: Session = Depends(get_db),
):
    """Create a new subscriber (manual creation)."""
    sub = subscriber_service.create(db, data.model_dump(exclude_unset=True))
    return SubscriberResponse.model_validate(sub)


@router.patch("/{subscriber_id}", response_model=SubscriberResponse)
def update_subscriber(
    subscriber_id: UUID,
    data: SubscriberUpdate,
    db: Session = Depends(get_db),
):
    """Update a subscriber (limited fields - most data comes from sync)."""
    sub = subscriber_service.get(db, subscriber_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found")

    update_data = data.model_dump(exclude_unset=True, exclude_none=True)
    sub = subscriber_service.update(db, sub, update_data)
    return SubscriberResponse.model_validate(sub)


@router.delete("/{subscriber_id}", status_code=204)
def delete_subscriber(
    subscriber_id: UUID,
    db: Session = Depends(get_db),
):
    """Soft delete a subscriber."""
    sub = subscriber_service.get(db, subscriber_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    subscriber_service.delete(db, sub)


@router.post("/{subscriber_id}/link-person", response_model=SubscriberResponse)
def link_subscriber_to_person(
    subscriber_id: UUID,
    person_id: UUID,
    db: Session = Depends(get_db),
):
    """Link a subscriber to a person contact."""
    sub = subscriber_service.get(db, subscriber_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    sub = subscriber_service.link_to_person(db, sub, person_id)
    return SubscriberResponse.model_validate(sub)


@router.post("/{subscriber_id}/link-organization", response_model=SubscriberResponse)
def link_subscriber_to_organization(
    subscriber_id: UUID,
    organization_id: UUID,
    db: Session = Depends(get_db),
):
    """Link a subscriber to an organization."""
    sub = subscriber_service.get(db, subscriber_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    sub = subscriber_service.link_to_organization(db, sub, organization_id)
    return SubscriberResponse.model_validate(sub)


# ============================================================================
# Sync Endpoints (for external billing system integration)
# ============================================================================


@router.post("/sync", response_model=dict)
def sync_subscribers(
    data: SubscriberBulkSync,
    db: Session = Depends(get_db),
):
    """
    Bulk sync subscribers from external billing system.

    This endpoint is called by integration jobs or webhooks from
    external systems (Splynx, UCRM, WHMCS, etc.) to push subscriber
    data into the platform.
    """
    errors: list[dict[str, str]] = []
    created = 0
    updated = 0

    for sub_data in data.subscribers:
        try:
            existing = subscriber_service.get_by_external_id(db, data.external_system, sub_data.external_id)

            sync_data = sub_data.model_dump(
                exclude={"person_email", "person_phone"},
                exclude_unset=True,
            )

            subscriber_service.sync_from_external(
                db,
                external_system=data.external_system,
                external_id=sub_data.external_id,
                data=sync_data,
            )

            if existing:
                updated += 1
            else:
                created += 1

        except Exception as e:
            db.rollback()
            errors.append(
                {
                    "external_id": sub_data.external_id,
                    "error": str(e),
                }
            )

    return {
        "created": created,
        "updated": updated,
        "errors": errors,
    }


@router.post("/sync/webhook/{external_system}")
def sync_webhook(
    external_system: str,
    payload: dict,
    db: Session = Depends(get_db),
):
    """
    Webhook endpoint for real-time sync from external systems.

    Each external system may send different payload formats.
    This endpoint normalizes the data and syncs.
    """
    # Parse based on external system
    if external_system == "splynx":
        return _handle_splynx_webhook(db, payload)
    elif external_system == "ucrm":
        return _handle_ucrm_webhook(db, payload)
    else:
        # Generic handling - expect normalized format
        return _handle_generic_webhook(db, external_system, payload)


def _handle_splynx_webhook(db: Session, payload: dict) -> dict:
    """Handle Splynx webhook payload."""
    # Splynx sends customer data in specific format
    # Map to our normalized format
    external_id = str(payload.get("id"))
    data = {
        "external_id": external_id,
        "subscriber_number": payload.get("login"),
        "status": _map_splynx_status(payload.get("status")),
        "service_name": payload.get("tariff_name"),
        "balance": str(payload.get("balance", 0)),
        "service_address_line1": payload.get("street"),
        "service_city": payload.get("city"),
    }

    sub = subscriber_service.sync_from_external(db, "splynx", external_id, data)
    return {"status": "ok", "subscriber_id": str(sub.id)}


def _handle_ucrm_webhook(db: Session, payload: dict) -> dict:
    """Handle UCRM/UNMS webhook payload."""
    # UCRM format mapping
    client = payload.get("client", {})
    external_id = str(client.get("id"))
    data = {
        "external_id": external_id,
        "subscriber_number": client.get("userIdent"),
        "status": _map_ucrm_status(client.get("isActive")),
        "service_name": payload.get("servicePlanName"),
        "balance": str(client.get("accountBalance", 0)),
    }

    sub = subscriber_service.sync_from_external(db, "ucrm", external_id, data)
    return {"status": "ok", "subscriber_id": str(sub.id)}


def _handle_generic_webhook(db: Session, external_system: str, payload: dict) -> dict:
    """Handle generic webhook with normalized format."""
    external_id = payload.get("external_id") or payload.get("id")
    if not external_id:
        raise HTTPException(status_code=400, detail="external_id or id required")

    sub = subscriber_service.sync_from_external(db, external_system, str(external_id), payload)
    return {"status": "ok", "subscriber_id": str(sub.id)}


def _map_splynx_status(status: str | int | None) -> SubscriberStatus:
    """Map Splynx status to our status enum."""
    status_map = {
        "active": SubscriberStatus.active,
        "blocked": SubscriberStatus.suspended,
        "inactive": SubscriberStatus.terminated,
        "new": SubscriberStatus.pending,
        1: SubscriberStatus.active,
        2: SubscriberStatus.suspended,
        0: SubscriberStatus.terminated,
    }
    return status_map.get(status, SubscriberStatus.active)


def _map_ucrm_status(is_active: bool | None) -> SubscriberStatus:
    """Map UCRM active status to our status enum."""
    return SubscriberStatus.active if is_active else SubscriberStatus.suspended
