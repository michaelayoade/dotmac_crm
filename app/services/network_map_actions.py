from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import cast
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy.sql import text

from app.models.network import FdhCabinet, OLTDevice
from app.services.common import coerce_uuid
from app.services.fiber_plant import fiber_plant
from app.services.pdf_utils import ensure_pydyf_compat
from app.web.templates import Jinja2Templates

templates = Jinja2Templates(directory="templates")


def _haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c


def _format_distance(meters: float) -> str:
    if meters >= 1000:
        return f"{meters / 1000:.2f} km"
    return f"{meters:.1f} m"


def _parse_lat_lng(lat: float | str, lng: float | str) -> tuple[float, float]:
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid coordinates") from exc
    if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        raise ValueError("Coordinates out of range")
    return lat_f, lng_f


def _json_safe(value: object):
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(v) for v in value]
    return str(value)


def _get_cabinets_with_location(db: Session) -> list[FdhCabinet]:
    from sqlalchemy.orm import load_only

    return (
        db.query(FdhCabinet)
        .options(load_only(FdhCabinet.id, FdhCabinet.name, FdhCabinet.code, FdhCabinet.latitude, FdhCabinet.longitude))
        .filter(
            FdhCabinet.is_active.is_(True),
            FdhCabinet.latitude.isnot(None),
            FdhCabinet.longitude.isnot(None),
        )
        .all()
    )


async def update_olt_role(request: Request, db: Session):
    try:
        data = await request.json()
        device_id = (data.get("id") or "").strip()
        site_role = (data.get("site_role") or "").strip().lower()

        if not device_id:
            return JSONResponse({"error": "Missing id"}, status_code=400)
        if site_role not in {"olt", "base_station"}:
            return JSONResponse({"error": "Invalid site_role"}, status_code=400)

        device = db.get(OLTDevice, coerce_uuid(device_id))
        if not device:
            return JSONResponse({"error": "OLT device not found"}, status_code=404)

        device.site_role = site_role
        db.commit()
        return JSONResponse({"success": True, "id": device_id, "site_role": site_role})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:
        db.rollback()
        return JSONResponse({"error": "Failed to update OLT role"}, status_code=500)


async def find_nearest_cabinet(lat: float, lng: float, db: Session):
    try:
        lat_f, lng_f = _parse_lat_lng(lat, lng)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    cabinets = _get_cabinets_with_location(db)
    if not cabinets:
        return JSONResponse({"error": "No cabinets found"}, status_code=404)

    nearest = None
    nearest_dist = None
    for cabinet in cabinets:
        dist = _haversine_distance_m(lat_f, lng_f, float(cabinet.latitude), float(cabinet.longitude))
        if nearest_dist is None or dist < nearest_dist:
            nearest = cabinet
            nearest_dist = dist

    if not nearest or nearest_dist is None:
        return JSONResponse({"error": "No cabinets found"}, status_code=404)

    return JSONResponse(
        {
            "id": str(nearest.id),
            "name": nearest.name,
            "code": nearest.code,
            "latitude": nearest.latitude,
            "longitude": nearest.longitude,
            "distance_m": nearest_dist,
            "distance_display": _format_distance(nearest_dist),
        }
    )


async def plan_options(lat: float, lng: float, db: Session):
    try:
        lat_f, lng_f = _parse_lat_lng(lat, lng)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    cabinets = _get_cabinets_with_location(db)
    options = []
    for cabinet in cabinets:
        dist = _haversine_distance_m(lat_f, lng_f, float(cabinet.latitude), float(cabinet.longitude))
        options.append(
            {
                "id": str(cabinet.id),
                "name": cabinet.name,
                "code": cabinet.code,
                "latitude": cabinet.latitude,
                "longitude": cabinet.longitude,
                "distance_m": dist,
                "distance_display": _format_distance(dist),
            }
        )
    options.sort(key=lambda item: cast(float, item["distance_m"]))
    return JSONResponse({"options": options[:5]})


async def plan_route(lat: float, lng: float, cabinet_id: str, db: Session):
    try:
        lat_f, lng_f = _parse_lat_lng(lat, lng)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    cabinet = db.get(FdhCabinet, cabinet_id)
    if not cabinet or cabinet.latitude is None or cabinet.longitude is None:
        return JSONResponse({"error": "Cabinet not found"}, status_code=404)

    lat_c = float(cabinet.latitude)
    lng_c = float(cabinet.longitude)
    dist = _haversine_distance_m(lat_f, lng_f, lat_c, lng_c)
    return JSONResponse(
        {
            "path_coords": [[lat_f, lng_f], [lat_c, lng_c]],
            "distance_m": dist,
            "distance_display": _format_distance(dist),
        }
    )


def closure_duplicates_pdf(request: Request, db: Session):
    report = fiber_plant.get_splice_closure_duplicate_rows(db)
    template = templates.get_template("admin/network/fiber/closure-duplicates-pdf.html")
    html = template.render(
        {
            "request": request,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "report": report,
        }
    )
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="WeasyPrint is not installed on the server. Install it to generate PDFs.",
        ) from exc

    ensure_pydyf_compat()
    pdf_bytes = HTML(string=html, base_url=str(request.base_url)).write_pdf()
    filename = "closure_duplicates.pdf"
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


def segment_geometry(segment_id: str, db: Session):
    try:
        seg_uuid = coerce_uuid(segment_id)
    except Exception:
        return JSONResponse({"error": "Invalid segment_id"}, status_code=400)

    row = (
        db.execute(
            text(
                """
                select
                    s.id,
                    s.name,
                    s.segment_type,
                    st_asgeojson(s.geometry)::text as geometry_json
                from fiber_segments s
                where s.id = :sid
                limit 1
                """
            ),
            {"sid": str(seg_uuid)},
        )
        .mappings()
        .first()
    )
    if not row:
        return JSONResponse({"error": "Segment not found"}, status_code=404)

    geometry = row.get("geometry_json")
    if geometry is None:
        return JSONResponse({"error": "Segment has no geometry"}, status_code=404)

    return JSONResponse(
        {
            "id": str(row["id"]),
            "name": row.get("name"),
            "segment_type": row.get("segment_type"),
            "geometry": _json_safe(geometry if isinstance(geometry, dict) else __import__("json").loads(geometry)),
        }
    )
