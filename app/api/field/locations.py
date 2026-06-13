from fastapi import APIRouter, Depends
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
