"""Movement tracking for field work orders.

Movement is separate from work execution: en route/arrived describes travel,
while start/pause/resume/complete describes active work time.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.field import WorkOrderMovement
from app.models.workforce import WorkOrder
from app.services.common import coerce_uuid
from app.services.field.jobs import get_scoped_work_order
from app.services.field.location import resolve_job_location
from app.services.field.map_assets import list_nearby_map_assets

_CUSTOMER_DESTINATION = "customer"
_ALLOWED_DESTINATIONS = {
    _CUSTOMER_DESTINATION,
    "cabinet",
    "closure",
    "pop",
    "other",
    # Internal asset aliases used by the field map API.
    "fdh",
    "splice_closure",
    "fiber_access_point",
    "olt",
}
_ASSET_TO_DESTINATION = {
    "fdh": "cabinet",
    "splice_closure": "closure",
    "olt": "pop",
}


def _as_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _destination_payload(db: Session, payload: dict | None, work_order: WorkOrder) -> dict:
    data = dict(payload or {})
    destination_type = str(data.get("destination_type") or _CUSTOMER_DESTINATION).strip().lower()
    if destination_type not in _ALLOWED_DESTINATIONS:
        raise HTTPException(status_code=422, detail=f"Unsupported destination_type: {destination_type}")
    destination_type = _ASSET_TO_DESTINATION.get(destination_type, destination_type)

    if destination_type == _CUSTOMER_DESTINATION:
        location = resolve_job_location(db, work_order)
        return {
            "destination_type": _CUSTOMER_DESTINATION,
            "destination_id": str(work_order.subscriber_id) if work_order.subscriber_id else None,
            "destination_label": data.get("destination_label") or "Customer site",
            "destination_latitude": _as_float(data.get("destination_latitude") or location.get("latitude")),
            "destination_longitude": _as_float(data.get("destination_longitude") or location.get("longitude")),
        }

    return {
        "destination_type": destination_type,
        "destination_id": str(data["destination_id"]) if data.get("destination_id") else None,
        "destination_label": data.get("destination_label") or data.get("label") or destination_type.replace("_", " "),
        "destination_latitude": _as_float(data.get("destination_latitude") or data.get("latitude")),
        "destination_longitude": _as_float(data.get("destination_longitude") or data.get("longitude")),
    }


def is_customer_destination(payload: dict | None) -> bool:
    destination_type = str((payload or {}).get("destination_type") or _CUSTOMER_DESTINATION).strip().lower()
    return _ASSET_TO_DESTINATION.get(destination_type, destination_type) == _CUSTOMER_DESTINATION


def validate_destination_payload(db: Session, work_order: WorkOrder, payload: dict | None) -> None:
    movement_id = (payload or {}).get("movement_session_id")
    if movement_id:
        try:
            coerce_uuid(str(movement_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid movement_session_id") from exc
    _destination_payload(db, payload, work_order)


def list_destinations(db: Session, person_id: str, work_order_id: str) -> list[dict]:
    work_order = get_scoped_work_order(db, person_id, work_order_id)
    location = resolve_job_location(db, work_order)
    items: list[dict] = []
    items.append(
        {
            "destination_type": _CUSTOMER_DESTINATION,
            "destination_id": str(work_order.subscriber_id) if work_order.subscriber_id else None,
            "label": "Customer site",
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "address_text": location.get("address_text"),
        }
    )

    latitude = _as_float(location.get("latitude"))
    longitude = _as_float(location.get("longitude"))
    if latitude is not None and longitude is not None:
        nearby = list_nearby_map_assets(
            db,
            latitude=latitude,
            longitude=longitude,
            radius_m=750,
            asset_types=["fdh", "splice_closure", "fiber_access_point", "olt"],
            limit=20,
        )
        for asset in nearby:
            destination_type = _ASSET_TO_DESTINATION.get(asset["type"], asset["type"])
            items.append(
                {
                    "destination_type": destination_type,
                    "destination_id": str(asset["id"]),
                    "label": asset["title"],
                    "latitude": asset["latitude"],
                    "longitude": asset["longitude"],
                    "address_text": asset.get("subtitle"),
                }
            )

    items.append(
        {
            "destination_type": "other",
            "destination_id": None,
            "label": "Other location",
            "latitude": None,
            "longitude": None,
            "address_text": None,
        }
    )
    return items


def start_movement(
    db: Session,
    work_order: WorkOrder,
    person_id: UUID,
    *,
    client_ref: UUID,
    occurred_at: datetime,
    latitude: float | None,
    longitude: float | None,
    payload: dict | None,
) -> WorkOrderMovement:
    existing = db.query(WorkOrderMovement).filter(WorkOrderMovement.client_ref == client_ref).first()
    if existing:
        return existing
    destination = _destination_payload(db, payload, work_order)
    movement = WorkOrderMovement(
        work_order_id=work_order.id,
        actor_person_id=person_id,
        started_at=occurred_at,
        start_latitude=latitude,
        start_longitude=longitude,
        status="en_route",
        client_ref=client_ref,
        **destination,
    )
    db.add(movement)
    db.commit()
    db.refresh(movement)
    return movement


def arrive_movement(
    db: Session,
    work_order: WorkOrder,
    person_id: UUID,
    *,
    client_ref: UUID,
    occurred_at: datetime,
    latitude: float | None,
    longitude: float | None,
    payload: dict | None,
) -> WorkOrderMovement:
    existing = db.query(WorkOrderMovement).filter(WorkOrderMovement.client_ref == client_ref).first()
    if existing:
        return existing
    movement_id = (payload or {}).get("movement_session_id")
    try:
        movement_uuid = coerce_uuid(str(movement_id)) if movement_id else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid movement_session_id") from exc
    movement = db.get(WorkOrderMovement, movement_uuid) if movement_uuid else None
    if movement is None:
        movement = (
            db.query(WorkOrderMovement)
            .filter(WorkOrderMovement.work_order_id == work_order.id)
            .filter(WorkOrderMovement.actor_person_id == person_id)
            .filter(WorkOrderMovement.status == "en_route")
            .order_by(WorkOrderMovement.started_at.desc())
            .first()
        )
    if movement is None:
        destination = _destination_payload(db, payload, work_order)
        movement = WorkOrderMovement(
            work_order_id=work_order.id,
            actor_person_id=person_id,
            started_at=occurred_at,
            status="arrived",
            client_ref=client_ref,
            **destination,
        )
        db.add(movement)
    movement.arrived_at = occurred_at
    movement.arrival_latitude = latitude
    movement.arrival_longitude = longitude
    movement.status = "arrived"
    movement.client_ref = client_ref
    db.commit()
    db.refresh(movement)
    return movement
