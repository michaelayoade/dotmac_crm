from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.field import (
    FieldPresenceRead,
    LocationIngestResponse,
    LocationPingBatch,
    LocationSharingUpdate,
)
from app.services.auth_dependencies import require_user_auth
from app.services.field.location_tracking import field_location_tracking

router = APIRouter(prefix="/locations", tags=["field-locations"])


@router.post("", response_model=LocationIngestResponse)
def ingest_locations(
    payload: LocationPingBatch,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    """Ingest a batch of (possibly offline-queued) location pings for the authed tech."""
    result = field_location_tracking.record_batch(
        db,
        auth["person_id"],
        [p.model_dump() for p in payload.pings],
    )
    return LocationIngestResponse(
        accepted=result["accepted"],
        errors=result["errors"],
        presence=FieldPresenceRead.from_presence(result["presence"]),
        transitions=result.get("transitions", []),
    )


@router.put("/sharing", response_model=FieldPresenceRead)
def update_sharing(
    payload: LocationSharingUpdate,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    """Opt in/out of location sharing and set shift status."""
    presence = field_location_tracking.set_sharing(
        db,
        auth["person_id"],
        enabled=payload.enabled,
        status=payload.status,
    )
    return FieldPresenceRead.from_presence(presence)


@router.get("/me", response_model=FieldPresenceRead)
def get_my_presence(
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    presence = field_location_tracking.get_or_create_presence(db, auth["person_id"])
    db.commit()
    return FieldPresenceRead.from_presence(presence)


@router.get("/route")
def my_day_route(
    start_lat: float = Query(ge=-90, le=90),
    start_lng: float = Query(ge=-180, le=180),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    """Greedy nearest-neighbour order of the authed tech's open jobs (task #47)."""
    from app.services.field.routing import order_day_route

    return {"route": order_day_route(db, auth["person_id"], start_latitude=start_lat, start_longitude=start_lng)}
