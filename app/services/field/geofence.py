"""Geofence auto-status: arrival at an assigned job auto-starts it.

When a location ping places the assigned technician inside a job's arrival
radius, fire a ``start`` transition through the existing field transition engine
(which owns rules, evidence gates, idempotency, and domain events). We add
nothing to the state machine — we just press the button the tech would have
pressed on arrival.

Conservative by default: gated by the ``field:geofence_auto_status_enabled``
domain setting (default OFF), and only ever advances a job that is already
scheduled/dispatched. The ``client_event_id`` is derived deterministically from
the work order so repeated pings near a job de-duplicate to a single transition.
"""

from __future__ import annotations

import logging
import math
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services.common import coerce_uuid
from app.services.field.location import resolve_job_location
from app.services.field.transitions import field_transitions

logger = logging.getLogger(__name__)

# Fixed namespace so geofence-issued client_event_ids are stable across pings.
_GEOFENCE_NS = uuid.UUID("9f1c0d2e-7b3a-4c6e-9a8d-1e2f3a4b5c6d")
DEFAULT_ARRIVAL_RADIUS_M = 120.0
_ARRIVABLE_STATUSES = {WorkOrderStatus.scheduled, WorkOrderStatus.dispatched}


def _setting_row(db: Session, key: str) -> DomainSetting | None:
    return (
        db.query(DomainSetting)
        .filter(DomainSetting.domain == SettingDomain.field)
        .filter(DomainSetting.key == key)
        .filter(DomainSetting.is_active.is_(True))
        .first()
    )


def geofence_enabled(db: Session) -> bool:
    row = _setting_row(db, "geofence_auto_status_enabled")
    if not row:
        return False  # opt-in: status should not change without explicit configuration
    value = row.value_json if row.value_json is not None else row.value_text
    return str(value).lower() in ("true", "1", "yes")


def arrival_radius_m(db: Session) -> float:
    row = _setting_row(db, "geofence_arrival_radius_m")
    if not row:
        return DEFAULT_ARRIVAL_RADIUS_M
    value = row.value_json if row.value_json is not None else row.value_text
    try:
        radius = float(str(value))
    except (TypeError, ValueError):
        return DEFAULT_ARRIVAL_RADIUS_M
    return radius if radius > 0 else DEFAULT_ARRIVAL_RADIUS_M


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _arrivable_jobs(db: Session, person_uuid) -> list[WorkOrder]:
    return (
        db.query(WorkOrder)
        .filter(WorkOrder.assigned_to_person_id == person_uuid)
        .filter(WorkOrder.status.in_(_ARRIVABLE_STATUSES))
        .all()
    )


def evaluate(db: Session, person_id: str, latitude: float, longitude: float) -> list[dict]:
    """Auto-start any assigned job whose arrival radius now contains the tech.

    Returns one entry per freshly fired transition (replays are suppressed).
    Never raises — geofence is a best-effort convenience over the ingest path.
    """
    if not geofence_enabled(db):
        return []
    person_uuid = coerce_uuid(person_id)
    if person_uuid is None:
        return []
    radius = arrival_radius_m(db)
    fired: list[dict] = []
    for work_order in _arrivable_jobs(db, person_uuid):
        try:
            loc = resolve_job_location(db, work_order)
        except Exception:
            logger.exception("geofence_resolve_location_failed work_order_id=%s", work_order.id)
            continue
        if loc.get("latitude") is None or loc.get("longitude") is None:
            continue
        distance = haversine_m(latitude, longitude, float(loc["latitude"]), float(loc["longitude"]))
        if distance > radius:
            continue
        client_event_id = str(uuid.uuid5(_GEOFENCE_NS, f"start:{work_order.id}"))
        try:
            result = field_transitions.apply(
                db,
                person_id,
                str(work_order.id),
                event="start",
                client_event_id=client_event_id,
                latitude=latitude,
                longitude=longitude,
                note="Auto-started on geofence arrival",
            )
        except HTTPException:
            # Wrong status, not the primary tech, etc. — leave it to the tech.
            continue
        if not result.get("replayed"):
            fired.append(
                {
                    "work_order_id": str(work_order.id),
                    "event": "start",
                    "distance_m": round(distance, 1),
                }
            )
    return fired
