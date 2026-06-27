"""Resolve a work order's physical location for the field app.

``WorkOrder.address_id`` is a dangling reference (the addresses domain was
removed), so coordinates come from geocoding the subscriber's service address
(falling back to the person's address) via the existing geocoding service.
Results are cached on ``work_order.metadata_`` so each job geocodes at most
once; failures degrade to a text address the app can hand to a maps app as a
search query.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.workforce import WorkOrder
from app.services import geocoding as geocoding_service
from app.services.workforce import _resolve_site_address

logger = logging.getLogger(__name__)

_CACHE_KEY = "resolved_location"


def _address_parts(work_order: WorkOrder) -> dict:
    subscriber = work_order.subscriber
    if not subscriber:
        return {}
    parts = {
        "address_line1": subscriber.service_address_line1,
        "address_line2": subscriber.service_address_line2,
        "city": subscriber.service_city,
        "region": subscriber.service_region,
        "postal_code": subscriber.service_postal_code,
    }
    if any(parts.values()):
        return parts
    person = subscriber.person
    if not person:
        return {}
    return {
        "address_line1": person.address_line1,
        "address_line2": person.address_line2,
        "city": getattr(person, "city", None),
        "region": getattr(person, "region", None),
        "postal_code": getattr(person, "postal_code", None),
    }


def cached_job_location(work_order: WorkOrder) -> dict | None:
    """Return the already-cached coordinates without geocoding, or None.

    For high-frequency / unauthenticated read paths (e.g. the customer live
    poll) that must never trigger an external geocoder call.
    """
    meta = work_order.metadata_ or {}
    cached = meta.get(_CACHE_KEY)
    if isinstance(cached, dict) and cached.get("latitude") is not None:
        return {**cached, "source": cached.get("source") or "cached"}
    return None


def resolve_job_location(db: Session, work_order: WorkOrder) -> dict:
    """Return {latitude, longitude, address_text, source} for a job.

    source is one of: cached | geocoded | manual | address_only | none.
    Only successful geocodes are cached; failures retry on the next call.
    """
    meta = work_order.metadata_ or {}
    cached = meta.get(_CACHE_KEY)
    if isinstance(cached, dict) and cached.get("latitude") is not None:
        return {**cached, "source": cached.get("source") or "cached"}

    address_text = _resolve_site_address(work_order)
    if not address_text:
        return {"latitude": None, "longitude": None, "address_text": None, "source": "none"}

    data = _address_parts(work_order)
    try:
        geocoded = geocoding_service.geocode_address(db, dict(data))
    except HTTPException:
        logger.warning("field_job_geocode_failed work_order_id=%s", work_order.id)
        return {"latitude": None, "longitude": None, "address_text": address_text, "source": "address_only"}

    latitude = geocoded.get("latitude")
    longitude = geocoded.get("longitude")
    if latitude is None or longitude is None:
        return {"latitude": None, "longitude": None, "address_text": address_text, "source": "address_only"}

    result = {"latitude": latitude, "longitude": longitude, "address_text": address_text}
    work_order.metadata_ = {**meta, _CACHE_KEY: result}
    flag_modified(work_order, "metadata_")
    db.commit()
    return {**result, "source": "geocoded"}


def update_job_location(
    db: Session,
    work_order: WorkOrder,
    *,
    latitude: float,
    longitude: float,
) -> dict:
    """Persist a technician-pinned job location.

    Manual pins use the same metadata slot as geocoded coordinates so routing,
    geofencing, and the mobile map consume one location source.
    """
    address_text = _resolve_site_address(work_order)
    result = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "address_text": address_text,
        "source": "manual",
    }
    meta = work_order.metadata_ or {}
    work_order.metadata_ = {
        **meta,
        _CACHE_KEY: {
            "latitude": result["latitude"],
            "longitude": result["longitude"],
            "address_text": address_text,
            "source": "manual",
        },
    }
    flag_modified(work_order, "metadata_")
    db.commit()
    return result
