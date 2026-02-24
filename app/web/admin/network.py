"""Admin network map routes and helpers."""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, date, datetime
from enum import Enum
from typing import cast
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.sql import text

from app.db import SessionLocal
from app.models.domain_settings import SettingDomain
from app.models.fiber_change_request import FiberChangeRequestOperation, FiberChangeRequestStatus
from app.models.gis import GeoAreaType, GeoLocation, GeoLocationType
from app.models.network import (
    FdhCabinet,
    FiberSegment,
    FiberSplice,
    FiberSpliceClosure,
    FiberSpliceTray,
    FiberStrand,
    FiberTerminationPoint,
    OLTDevice,
    Splitter,
    SplitterPort,
)
from app.services import fiber_change_requests as change_request_service
from app.services import gis as gis_service
from app.services import settings_spec
from app.services import vendor as vendor_service
from app.services.common import coerce_uuid
from app.services.fiber_plant import fiber_plant
from app.services.network_impl import fdh_cabinets as fdh_cabinets_service
from app.services.network_impl import splitters as splitters_service
from app.services.pdf_utils import ensure_pydyf_compat

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/network", tags=["web-admin-network"])

_ASSET_MODEL_BY_TYPE = {
    "fdh_cabinet": FdhCabinet,
    "splice_closure": FiberSpliceClosure,
    "fiber_segment": FiberSegment,
    "fiber_splice": FiberSplice,
    "fiber_splice_tray": FiberSpliceTray,
    "fiber_strand": FiberStrand,
    "fiber_termination_point": FiberTerminationPoint,
    "splitter": Splitter,
    "splitter_port": SplitterPort,
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _get_regions(db: Session) -> list:
    """Load region GeoAreas for FDH cabinet forms."""
    return gis_service.geo_areas.list(
        db,
        area_type=GeoAreaType.region.value,
        is_active=True,
        min_latitude=None,
        min_longitude=None,
        max_latitude=None,
        max_longitude=None,
        order_by="name",
        order_dir="asc",
        limit=500,
        offset=0,
    )


def _sanitize_error(exc: Exception) -> str:
    """Return a safe error message without leaking internal details."""
    msg = str(exc)
    if "duplicate key" in msg.lower() or "unique" in msg.lower():
        return "A record with these details already exists."
    if "foreign key" in msg.lower() or "violates" in msg.lower():
        return "This operation references data that does not exist or has been removed."
    if "Coordinates out of range" in msg or "Invalid coordinates" in msg:
        return msg
    return "An unexpected error occurred. Please check your input and try again."


def _json_safe(value: object):
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime | date):
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


def _get_cabinets_with_location(db: Session) -> list:
    """Load active FDH cabinets that have coordinates."""
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


def _normalize_asset_type(asset_type: str | None) -> str:
    normalized = (asset_type or "").strip().lower()
    if normalized == "fiber_splice_closure":
        return "splice_closure"
    return normalized


def _asset_snapshot(db: Session, asset_type: str | None, asset_id: str | None):
    if not asset_type or not asset_id:
        return None
    model = _ASSET_MODEL_BY_TYPE.get(_normalize_asset_type(asset_type))
    if not model:
        return None
    try:
        return db.get(model, coerce_uuid(str(asset_id)))
    except Exception:
        return None


def _is_conflict(change_request, asset) -> bool:
    if not change_request or not asset:
        return False
    request_created = getattr(change_request, "created_at", None)
    asset_updated = getattr(asset, "updated_at", None)
    if not request_created or not asset_updated:
        return False
    return asset_updated > request_created


@router.get("/map", response_class=HTMLResponse)
def network_map(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user, get_sidebar_stats

    # Delegate GeoJSON generation to the service (single source of truth)
    geojson_data = fiber_plant.get_geojson(db)
    stats = fiber_plant.get_stats(db)
    qa_stats = fiber_plant.get_quality_stats(db)

    cost_settings = {
        "drop_cable_per_meter": _coerce_float(
            settings_spec.resolve_value(db, SettingDomain.network, "fiber_drop_cable_cost_per_meter"),
            2.50,
        ),
        "labor_per_meter": _coerce_float(
            settings_spec.resolve_value(db, SettingDomain.network, "fiber_labor_cost_per_meter"),
            1.50,
        ),
        "ont_device": _coerce_float(
            settings_spec.resolve_value(db, SettingDomain.network, "fiber_ont_device_cost"),
            85.00,
        ),
        "installation_base": _coerce_float(
            settings_spec.resolve_value(db, SettingDomain.network, "fiber_installation_base_fee"),
            50.00,
        ),
        "currency": settings_spec.resolve_value(db, SettingDomain.billing, "default_currency") or "NGN",
    }

    return templates.TemplateResponse(
        "admin/network/fiber/map.html",
        {
            "request": request,
            "active_page": "network-map",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "geojson_data": geojson_data,
            "stats": stats,
            "qa_stats": qa_stats,
            "cost_settings": cost_settings,
        },
    )


@router.get("/pop-sites", response_class=HTMLResponse)
def pop_sites_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=5, le=100),
):
    from app.web.admin import get_current_user, get_sidebar_stats

    offset = (page - 1) * per_page
    pop_sites = gis_service.geo_locations.list(
        db=db,
        location_type=GeoLocationType.pop.value,
        address_id=None,
        pop_site_id=None,
        is_active=None,
        min_latitude=None,
        min_longitude=None,
        max_latitude=None,
        max_longitude=None,
        order_by="name",
        order_dir="asc",
        limit=per_page,
        offset=offset,
    )
    total = (
        db.query(func.count(GeoLocation.id))
        .filter(
            GeoLocation.location_type == GeoLocationType.pop,
            GeoLocation.is_active.is_(True),
        )
        .scalar()
        or 0
    )
    total_pages = math.ceil(total / per_page) if total > 0 else 1

    return templates.TemplateResponse(
        "admin/network/pop_sites.html",
        {
            "request": request,
            "active_page": "pop-sites",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "pop_sites": pop_sites,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
        },
    )


@router.get("/fdh-cabinets", response_class=HTMLResponse)
def fdh_cabinets_list(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user, get_sidebar_stats

    cabinets = fdh_cabinets_service.list(db, region_id=None, order_by="name", order_dir="asc", limit=500, offset=0)
    stats = {
        "total": db.query(func.count(FdhCabinet.id)).filter(FdhCabinet.is_active.is_(True)).scalar() or 0,
    }
    return templates.TemplateResponse(
        "admin/network/fiber/fdh-cabinets.html",
        {
            "request": request,
            "active_page": "fdh-cabinets",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "cabinets": cabinets,
            "stats": stats,
        },
    )


@router.get("/fdh-cabinets/new", response_class=HTMLResponse)
def fdh_cabinet_new(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user, get_sidebar_stats

    return templates.TemplateResponse(
        "admin/network/fiber/fdh-cabinet-form.html",
        {
            "request": request,
            "active_page": "fdh-cabinets",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "cabinet": None,
            "regions": _get_regions(db),
            "action_url": "/admin/network/fdh-cabinets/new",
        },
    )


@router.post("/fdh-cabinets/new", response_class=HTMLResponse)
def fdh_cabinet_create(
    request: Request,
    name: str = Form(...),
    code: str | None = Form(None),
    region_id: str | None = Form(None),
    latitude: str | None = Form(None),
    longitude: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    error = None
    try:
        lat_value = None
        lng_value = None
        if latitude or longitude:
            lat_value, lng_value = _parse_lat_lng(latitude or "", longitude or "")
        cabinet = FdhCabinet(
            name=name.strip(),
            code=(code or "").strip() or None,
            region_id=coerce_uuid(region_id) if region_id else None,
            latitude=lat_value,
            longitude=lng_value,
            notes=(notes or "").strip() or None,
            is_active=is_active == "true",
        )
        db.add(cabinet)
        db.commit()
        return RedirectResponse(
            url=f"/admin/network/fdh-cabinets/{cabinet.id}",
            status_code=303,
        )
    except Exception as exc:
        db.rollback()
        error = _sanitize_error(exc)
        logger.exception("FDH cabinet create failed")

    from app.web.admin import get_current_user, get_sidebar_stats

    return templates.TemplateResponse(
        "admin/network/fiber/fdh-cabinet-form.html",
        {
            "request": request,
            "active_page": "fdh-cabinets",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "cabinet": None,
            "regions": _get_regions(db),
            "action_url": "/admin/network/fdh-cabinets/new",
            "error": error,
        },
        status_code=400,
    )


@router.get("/fdh-cabinets/{cabinet_id}", response_class=HTMLResponse)
def fdh_cabinet_detail(request: Request, cabinet_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user, get_sidebar_stats

    cabinet = db.get(FdhCabinet, cabinet_id)
    if not cabinet:
        return RedirectResponse(url="/admin/network/fdh-cabinets", status_code=303)
    regions = _get_regions(db)
    region_map = {str(region.id): region for region in regions}
    region = region_map.get(str(cabinet.region_id)) if cabinet.region_id else None
    cabinet_splitters = splitters_service.list(
        db, fdh_id=str(cabinet.id), order_by="name", order_dir="asc", limit=500, offset=0
    )
    return templates.TemplateResponse(
        "admin/network/fiber/fdh-cabinet-detail.html",
        {
            "request": request,
            "active_page": "fdh-cabinets",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "cabinet": cabinet,
            "region": region,
            "splitters": cabinet_splitters,
            "activities": [],
        },
    )


@router.get("/fdh-cabinets/{cabinet_id}/edit", response_class=HTMLResponse)
def fdh_cabinet_edit(request: Request, cabinet_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user, get_sidebar_stats

    cabinet = db.get(FdhCabinet, cabinet_id)
    if not cabinet:
        return RedirectResponse(url="/admin/network/fdh-cabinets", status_code=303)
    return templates.TemplateResponse(
        "admin/network/fiber/fdh-cabinet-form.html",
        {
            "request": request,
            "active_page": "fdh-cabinets",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "cabinet": cabinet,
            "regions": _get_regions(db),
            "action_url": f"/admin/network/fdh-cabinets/{cabinet_id}/edit",
        },
    )


@router.post("/fdh-cabinets/{cabinet_id}/edit", response_class=HTMLResponse)
def fdh_cabinet_update(
    request: Request,
    cabinet_id: str,
    name: str = Form(...),
    code: str | None = Form(None),
    region_id: str | None = Form(None),
    latitude: str | None = Form(None),
    longitude: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    error = None
    cabinet = db.get(FdhCabinet, cabinet_id)
    if not cabinet:
        return RedirectResponse(url="/admin/network/fdh-cabinets", status_code=303)
    try:
        lat_value = None
        lng_value = None
        if latitude or longitude:
            lat_value, lng_value = _parse_lat_lng(latitude or "", longitude or "")
        cabinet.name = name.strip()
        cabinet.code = (code or "").strip() or None
        cabinet.region_id = coerce_uuid(region_id) if region_id else None
        cabinet.latitude = lat_value
        cabinet.longitude = lng_value
        cabinet.notes = (notes or "").strip() or None
        cabinet.is_active = is_active == "true"
        db.commit()
        return RedirectResponse(
            url=f"/admin/network/fdh-cabinets/{cabinet_id}",
            status_code=303,
        )
    except Exception as exc:
        db.rollback()
        error = _sanitize_error(exc)
        logger.exception("FDH cabinet update failed for %s", cabinet_id)

    from app.web.admin import get_current_user, get_sidebar_stats

    return templates.TemplateResponse(
        "admin/network/fiber/fdh-cabinet-form.html",
        {
            "request": request,
            "active_page": "fdh-cabinets",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "cabinet": cabinet,
            "regions": _get_regions(db),
            "action_url": f"/admin/network/fdh-cabinets/{cabinet_id}/edit",
            "error": error,
        },
        status_code=400,
    )


@router.get("/fiber-map", response_class=HTMLResponse)
def network_map_alias():
    return RedirectResponse(url="/admin/network/map", status_code=302)


@router.get("/fiber-plant", response_class=HTMLResponse)
def fiber_plant_alias():
    return RedirectResponse(url="/admin/network/map", status_code=302)


@router.get("/fiber-change-requests", response_class=HTMLResponse)
def fiber_change_requests_list(
    request: Request,
    bulk_status: str | None = Query(None),
    skipped: int | None = Query(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user, get_sidebar_stats

    requests = change_request_service.list_requests(db, status=FiberChangeRequestStatus.pending)
    conflicts: dict[str, bool] = {}
    for req in requests:
        asset = _asset_snapshot(db, req.asset_type, str(req.asset_id) if req.asset_id else None)
        conflicts[str(req.id)] = _is_conflict(req, asset)

    return templates.TemplateResponse(
        "admin/network/fiber/change_requests.html",
        {
            "request": request,
            "active_page": "fiber-change-requests",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "requests": requests,
            "conflicts": conflicts,
            "bulk_status": bulk_status,
            "skipped": skipped or 0,
        },
    )


@router.get("/fiber-change-requests/{request_id}", response_class=HTMLResponse)
def fiber_change_request_detail(
    request: Request,
    request_id: str,
    error: str | None = Query(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user, get_sidebar_stats

    change_request = change_request_service.get_request(db, request_id)
    asset = _asset_snapshot(
        db,
        change_request.asset_type,
        str(change_request.asset_id) if change_request.asset_id else None,
    )
    conflict = _is_conflict(change_request, asset)
    pending = change_request.status == FiberChangeRequestStatus.pending

    activities = []
    if change_request.created_at:
        activities.append(
            {
                "title": "Request submitted",
                "description": f"Operation: {change_request.operation.value}",
                "occurred_at": change_request.created_at,
            }
        )
    if change_request.reviewed_at:
        reviewer_name = "-"
        if change_request.reviewed_by:
            reviewer_name = (
                (getattr(change_request.reviewed_by, "display_name", None))
                or f"{change_request.reviewed_by.first_name} {change_request.reviewed_by.last_name}".strip()
                or "-"
            )
        activities.append(
            {
                "title": f"Reviewed ({change_request.status.value})",
                "description": f"By {reviewer_name}",
                "occurred_at": change_request.reviewed_at,
            }
        )
    if change_request.applied_at:
        activities.append(
            {
                "title": "Applied to live asset",
                "description": None,
                "occurred_at": change_request.applied_at,
            }
        )

    asset_data = {}
    if asset is not None:
        # Best-effort snapshot for UI inspection.
        asset_data = {
            key: _json_safe(value)
            for key, value in vars(asset).items()
            if not key.startswith("_sa_") and not key.startswith("_")
        }

    return templates.TemplateResponse(
        "admin/network/fiber/change_request_detail.html",
        {
            "request": request,
            "active_page": "fiber-change-requests",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "change_request": change_request,
            "asset_data": asset_data,
            "conflict": conflict,
            "pending": pending,
            "activities": activities,
            "error": error,
        },
    )


@router.post("/fiber-change-requests/{request_id}/approve", response_class=HTMLResponse)
async def fiber_change_request_approve(
    request: Request,
    request_id: str,
    review_notes: str | None = Form(None),
    force_apply: bool = Form(False),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    reviewer_person_id = str(current_user.get("person_id") or "")
    if not reviewer_person_id:
        return RedirectResponse(
            url=f"/admin/network/fiber-change-requests/{request_id}?error=forbidden", status_code=303
        )

    change_request = change_request_service.get_request(db, request_id)
    asset = _asset_snapshot(
        db,
        change_request.asset_type,
        str(change_request.asset_id) if change_request.asset_id else None,
    )
    if _is_conflict(change_request, asset) and not force_apply:
        return RedirectResponse(
            url=f"/admin/network/fiber-change-requests/{request_id}?error=conflict", status_code=303
        )

    change_request_service.approve_request(db, request_id, reviewer_person_id, review_notes)
    return RedirectResponse(url=f"/admin/network/fiber-change-requests/{request_id}", status_code=303)


@router.post("/fiber-change-requests/{request_id}/reject", response_class=HTMLResponse)
async def fiber_change_request_reject(
    request: Request,
    request_id: str,
    review_notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user

    if not (review_notes or "").strip():
        return RedirectResponse(
            url=f"/admin/network/fiber-change-requests/{request_id}?error=reject_note_required",
            status_code=303,
        )

    current_user = get_current_user(request)
    reviewer_person_id = str(current_user.get("person_id") or "")
    if not reviewer_person_id:
        return RedirectResponse(
            url=f"/admin/network/fiber-change-requests/{request_id}?error=forbidden", status_code=303
        )

    change_request_service.reject_request(db, request_id, reviewer_person_id, (review_notes or "").strip())
    return RedirectResponse(url=f"/admin/network/fiber-change-requests/{request_id}", status_code=303)


@router.post("/fiber-change-requests/bulk-approve", response_class=HTMLResponse)
async def fiber_change_requests_bulk_approve(
    request: Request,
    force_apply: bool = Form(False),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user

    form = await request.form()
    request_ids = [value for value in form.getlist("request_ids") if isinstance(value, str) and value.strip()]

    current_user = get_current_user(request)
    reviewer_person_id = str(current_user.get("person_id") or "")
    if not reviewer_person_id:
        return RedirectResponse(url="/admin/network/fiber-change-requests?bulk_status=forbidden", status_code=303)

    skipped = 0
    for request_id in request_ids:
        try:
            change_request = change_request_service.get_request(db, request_id)
            asset = _asset_snapshot(
                db,
                change_request.asset_type,
                str(change_request.asset_id) if change_request.asset_id else None,
            )
            if _is_conflict(change_request, asset) and not force_apply:
                skipped += 1
                continue
            change_request_service.approve_request(db, request_id, reviewer_person_id, None)
        except Exception:
            db.rollback()
            skipped += 1

    return RedirectResponse(
        url=f"/admin/network/fiber-change-requests?bulk_status=approved&skipped={skipped}",
        status_code=303,
    )


@router.post("/fiber-map/update-position")
async def fiber_map_update_position(request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
        asset_type = data.get("type")
        asset_id = data.get("id")
        latitude = data.get("latitude")
        longitude = data.get("longitude")

        if not all([asset_type, asset_id, latitude is not None, longitude is not None]):
            return JSONResponse({"error": "Missing required fields"}, status_code=400)

        lat_f, lng_f = _parse_lat_lng(latitude, longitude)
        actor_id = getattr(request.state, "actor_id", None)
        request_record = change_request_service.create_request(
            db,
            asset_type=asset_type,
            asset_id=asset_id,
            operation=FiberChangeRequestOperation.update,
            payload={"latitude": lat_f, "longitude": lng_f},
            requested_by_person_id=str(actor_id) if actor_id else None,
            requested_by_vendor_id=None,
        )
        return JSONResponse(
            {"success": True, "request_id": str(request_record.id), "status": request_record.status.value}
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:
        db.rollback()
        logger.exception("fiber_map_update_position failed")
        return JSONResponse({"error": "Failed to create position update request"}, status_code=500)


@router.post("/fiber-map/update-olt-role")
async def fiber_map_update_olt_role(request: Request, db: Session = Depends(get_db)):
    """Update OLT map role (OLT vs base station). This is intentionally a small, surgical update endpoint."""
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
        logger.exception("fiber_map_update_olt_role failed")
        return JSONResponse({"error": "Failed to update OLT role"}, status_code=500)


@router.post("/fiber-map/save-plan")
async def fiber_map_save_plan(request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
        quote_id = (data.get("quote_id") or "").strip()
        geojson = data.get("geojson")
        length_meters = data.get("length_meters")

        if not quote_id:
            return JSONResponse({"error": "Quote ID is required"}, status_code=400)
        if not geojson:
            return JSONResponse({"error": "Route geometry is required"}, status_code=400)

        revision = vendor_service.proposed_route_revisions.create_for_admin(
            db, quote_id=quote_id, geojson=geojson, length_meters=length_meters
        )
        return JSONResponse(
            {"success": True, "revision_id": str(revision.id), "revision_number": revision.revision_number}
        )
    except Exception:
        db.rollback()
        logger.exception("fiber_map_save_plan failed")
        return JSONResponse({"error": "Failed to save route plan"}, status_code=500)


@router.get("/fiber-map/nearest-cabinet")
async def fiber_map_nearest_cabinet(lat: float, lng: float, db: Session = Depends(get_db)):
    return await find_nearest_cabinet(None, lat, lng, db)


async def find_nearest_cabinet(_request: Request | None, lat: float, lng: float, db: Session):
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


@router.get("/fiber-map/plan-options")
async def fiber_map_plan_options(lat: float, lng: float, db: Session = Depends(get_db)):
    return await plan_options(None, lat, lng, db)


async def plan_options(_request: Request | None, lat: float, lng: float, db: Session):
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


@router.get("/fiber-map/route")
async def fiber_map_route(lat: float, lng: float, cabinet_id: str, db: Session = Depends(get_db)):
    return await plan_route(None, lat, lng, cabinet_id, db)


async def plan_route(_request: Request | None, lat: float, lng: float, cabinet_id: str, db: Session):
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


# ── Asset merge endpoints ─────────────────────────────────────────────────


@router.get("/fiber-map/asset-details")
async def fiber_map_asset_details(
    asset_type: str = Query(...),
    asset_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Return field values and child counts for the merge comparison panel."""
    try:
        details = fiber_plant.get_asset_details(db, asset_type, asset_id)
        return JSONResponse(details)
    except Exception as exc:
        logger.exception("fiber_map_asset_details failed")
        status = getattr(exc, "status_code", 500)
        detail = getattr(exc, "detail", "Failed to load asset details")
        return JSONResponse({"error": detail}, status_code=status)


@router.post("/fiber-map/merge")
async def fiber_map_merge(request: Request, db: Session = Depends(get_db)):
    """Merge two duplicate fiber assets."""
    from app.web.admin import get_current_user

    try:
        data = await request.json()
        asset_type = data.get("asset_type")
        source_id = data.get("source_id")
        target_id = data.get("target_id")
        field_choices = data.get("field_choices", {})

        if not all([asset_type, source_id, target_id]):
            return JSONResponse({"error": "Missing required fields"}, status_code=400)

        current_user = get_current_user(request)
        person_id = str(current_user.get("person_id") or "")

        result = fiber_plant.merge_assets(
            db,
            asset_type=asset_type,
            source_id=source_id,
            target_id=target_id,
            field_choices=field_choices,
            merged_by_id=person_id or None,
        )

        # Return updated target details for the map to refresh the marker
        target_details = fiber_plant.get_asset_details(db, asset_type, result["target_id"])
        result["target"] = target_details

        return JSONResponse(result)
    except Exception as exc:
        db.rollback()
        logger.exception("fiber_map_merge failed")
        status = getattr(exc, "status_code", 500)
        detail = getattr(exc, "detail", "Merge failed")
        return JSONResponse({"error": detail}, status_code=status)


@router.get("/fiber-map/closure-duplicates.pdf")
def fiber_map_closure_duplicates_pdf(request: Request, db: Session = Depends(get_db)):
    """Download a PDF report of duplicate splice closures and their locations."""
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
        # Keep error consistent with other PDF exports.
        from fastapi import HTTPException

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


@router.get("/fiber-map/segments/geometry")
def fiber_map_segment_geometry(segment_id: str = Query(...), db: Session = Depends(get_db)):
    """Fetch a single segment's geometry for deep-link focusing (read-only)."""
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
                    s.fiber_count,
                    s.length_m,
                    s.from_point_id,
                    s.to_point_id,
                    case
                        when s.route_geom is not null
                        then st_asgeojson(st_linemerge(s.route_geom))
                    end as geom_geojson
                from fiber_segments s
                where s.is_active = true
                  and s.id = :id
                """
            ),
            {"id": seg_uuid},
        )
        .mappings()
        .first()
    )
    if not row:
        return JSONResponse({"error": "Segment not found"}, status_code=404)

    geom = None
    geom_geojson = row.get("geom_geojson")
    if geom_geojson:
        try:
            geom = json.loads(geom_geojson)
        except Exception:
            geom = None

    if not geom and row.get("from_point_id") and row.get("to_point_id"):
        tp_row = (
            db.execute(
                text(
                    """
                    select
                        fp.latitude as from_lat,
                        fp.longitude as from_lng,
                        tp.latitude as to_lat,
                        tp.longitude as to_lng
                    from fiber_segments s
                    join fiber_termination_points fp on fp.id = s.from_point_id
                    join fiber_termination_points tp on tp.id = s.to_point_id
                    where s.id = :id
                      and s.is_active = true
                    """
                ),
                {"id": seg_uuid},
            )
            .mappings()
            .first()
        )
        if tp_row and tp_row.get("from_lat") is not None and tp_row.get("from_lng") is not None:
            geom = {
                "type": "LineString",
                "coordinates": [
                    [float(tp_row["from_lng"]), float(tp_row["from_lat"])],
                    [float(tp_row["to_lng"]), float(tp_row["to_lat"])],
                ],
            }

    if not geom:
        return JSONResponse({"error": "Segment has no geometry"}, status_code=409)

    payload = {k: _json_safe(v) for k, v in dict(row).items() if k != "geom_geojson"}
    payload["geometry"] = geom
    return JSONResponse(payload)


@router.get("/qa/remediations", response_class=HTMLResponse)
def qa_remediations_list(
    request: Request,
    status: str | None = Query("pending"),
    message: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """List draft QA remediation items. No production data changes happen here."""
    from app.web.admin import get_current_user, get_sidebar_stats

    allowed_status = {"pending", "approved", "ignored"}
    status_value = (status or "pending").strip().lower()
    if status_value not in allowed_status:
        status_value = "pending"

    # Only splice-closure QA items for now.
    # We show:
    # - proximity_duplicate: has source+target IDs and distance/coords
    # - bulk_rename: proposed name edits for one asset
    sql = text(
        """
        with logs as (
            select
                id as log_id,
                asset_id as source_id,
                target_asset_id as target_id,
                status,
                review_notes,
                old_value,
                new_value,
                issue_type
            from fiber_qa_remediation_logs
            where asset_type = 'splice_closure'
              and status = :status
        ),
        c as (
            select
                id,
                name,
                latitude,
                longitude,
                coalesce(
                    geom,
                    case
                        when latitude is not null and longitude is not null
                        then st_setsrid(st_makepoint(longitude, latitude), 4326)
                    end
                ) as g
            from fiber_splice_closures
            where is_active = true
        )
        select
            l.log_id,
            l.issue_type,
            c1.name as name,
            c2.name as target_name,
            l.source_id,
            l.target_id,
            l.status,
            l.review_notes,
            l.old_value,
            l.new_value,
            st_distance(c1.g::geography, c2.g::geography) as distance_m,
            c1.latitude as source_lat,
            c1.longitude as source_lng,
            c2.latitude as target_lat,
            c2.longitude as target_lng
        from logs l
        join c c1 on c1.id = l.source_id
        left join c c2 on c2.id = l.target_id
        where l.issue_type = 'proximity_duplicate'
          and c1.g is not null and c2.g is not null
        union all
        select
            l.log_id,
            l.issue_type,
            c1.name as name,
            null as target_name,
            l.source_id,
            null as target_id,
            l.status,
            l.review_notes,
            l.old_value,
            l.new_value,
            null as distance_m,
            c1.latitude as source_lat,
            c1.longitude as source_lng,
            null as target_lat,
            null as target_lng
        from logs l
        join c c1 on c1.id = l.source_id
        where l.issue_type = 'bulk_rename'
        order by issue_type asc, distance_m nulls last, log_id asc
        """
    )
    all_rows = [dict(r._mapping) for r in db.execute(sql, {"status": status_value}).all()]
    proximity_rows = [r for r in all_rows if r.get("issue_type") == "proximity_duplicate"]
    rename_rows = [r for r in all_rows if r.get("issue_type") == "bulk_rename"]

    # Segment geometry duplicates (staged only; no merge logic exists yet).
    seg_sql = text(
        """
        select
            l.id as log_id,
            l.asset_id as source_id,
            l.target_asset_id as target_id,
            l.status,
            l.review_notes,
            s1.name as source_name,
            s2.name as target_name,
            l.old_value,
            st_y(st_startpoint(st_linemerge(s1.route_geom))) as source_lat,
            st_x(st_startpoint(st_linemerge(s1.route_geom))) as source_lng,
            st_y(st_startpoint(st_linemerge(s2.route_geom))) as target_lat,
            st_x(st_startpoint(st_linemerge(s2.route_geom))) as target_lng
        from fiber_qa_remediation_logs l
        join fiber_segments s1 on s1.id = l.asset_id
        join fiber_segments s2 on s2.id = l.target_asset_id
        where l.asset_type = 'fiber_segment'
          and l.issue_type = 'geometry_duplicate'
          and l.status = :status
        order by l.id asc
        """
    )
    segment_rows = [dict(r._mapping) for r in db.execute(seg_sql, {"status": status_value}).all()]

    manual_sql = text(
        """
        select
            l.id as log_id,
            l.asset_id as segment_id,
            l.status,
            l.review_notes,
            l.new_value,
            s.name as segment_name
        from fiber_qa_remediation_logs l
        join fiber_segments s on s.id = l.asset_id
        where l.asset_type = 'fiber_segment'
          and l.issue_type = 'manual_connectivity'
          and l.status = :status
        order by l.id asc
        """
    )
    manual_rows_raw = [dict(r._mapping) for r in db.execute(manual_sql, {"status": status_value}).all()]

    def _parse_manual_endpoints(raw: str | None) -> tuple[str, str]:
        if not raw:
            return "", ""
        s = str(raw).strip()
        if not s:
            return "", ""
        if s.startswith("{"):
            try:
                payload = json.loads(s)
                return (str(payload.get("from") or "").strip(), str(payload.get("to") or "").strip())
            except Exception:
                return "", ""
        upper = s.upper()
        if "FROM:" in upper and "TO:" in upper:
            try:
                parts = [p.strip() for p in s.split("|")]
                from_part = next((p for p in parts if p.lower().startswith("from:")), "")
                to_part = next((p for p in parts if p.lower().startswith("to:")), "")
                from_val = from_part.split(":", 1)[1].strip() if ":" in from_part else ""
                to_val = to_part.split(":", 1)[1].strip() if ":" in to_part else ""
                return from_val, to_val
            except Exception:
                return "", ""
        return "", ""

    manual_rows = []
    for r in manual_rows_raw:
        from_val, to_val = _parse_manual_endpoints(r.get("new_value"))
        manual_rows.append({**r, "from_value": from_val, "to_value": to_val})

    return templates.TemplateResponse(
        "admin/network/qa/remediations.html",
        {
            "request": request,
            "active_page": "network-qa-remediations",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "proximity_rows": proximity_rows,
            "rename_rows": rename_rows,
            "segment_rows": segment_rows,
            "manual_rows": manual_rows,
            "status": status_value,
            "message": message,
            "error": error,
        },
    )


@router.post("/qa/remediations/{log_id}/update", response_class=HTMLResponse)
def qa_remediations_update(
    request: Request,
    log_id: int,
    target_asset_id: str | None = Form(None),
    source_asset_id: str | None = Form(None),
    candidate_target_asset_id: str | None = Form(None),
    keep_source: str | None = Form(None),
    keep_target: str | None = Form(None),
    keep_both_rename: str | None = Form(None),
    rename_side: str | None = Form(None),
    rename_to: str | None = Form(None),
    status: str = Form("pending"),
    review_notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Update draft remediation details. Does not apply to production assets."""
    allowed_status = {"pending", "ignored"}
    status_value = (status or "pending").strip().lower()
    if status_value not in allowed_status:
        status_value = "pending"

    source_uuid = None
    target_uuid = None
    old_value = None
    new_value = None
    use_keep_selector = bool(source_asset_id and candidate_target_asset_id)

    if use_keep_selector:
        keep_source_checked = bool(keep_source)
        keep_target_checked = bool(keep_target)
        keep_both_checked = bool(keep_both_rename)
        selected_count = int(keep_source_checked) + int(keep_target_checked) + int(keep_both_checked)
        if selected_count != 1:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Select+exactly+one+closure+to+keep",
                status_code=303,
            )
        try:
            source_candidate_uuid = coerce_uuid(source_asset_id)
            target_candidate_uuid = coerce_uuid(candidate_target_asset_id)
        except Exception:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Invalid+closure+IDs+in+draft",
                status_code=303,
            )

        # Merge semantics: source is archived; target remains.
        if keep_both_checked:
            source_uuid = source_candidate_uuid
            target_uuid = target_candidate_uuid
            rename_side_value = (rename_side or "target").strip().lower()
            if rename_side_value not in {"source", "target"}:
                rename_side_value = "target"
            rename_to_value = (rename_to or "").strip()
            if not rename_to_value:
                return RedirectResponse(
                    url="/admin/network/qa/remediations?status=pending&error=New+name+is+required+when+keeping+both",
                    status_code=303,
                )
            rename_asset_uuid = source_candidate_uuid if rename_side_value == "source" else target_candidate_uuid
            current_name = db.execute(
                text(
                    """
                    select name
                      from fiber_splice_closures
                     where id = :asset_id
                    """
                ),
                {"asset_id": rename_asset_uuid},
            ).scalar_one_or_none()
            old_value = str(current_name or "").strip() or rename_side_value
            new_value = rename_to_value
        elif keep_source_checked:
            source_uuid = target_candidate_uuid
            target_uuid = source_candidate_uuid
            old_value = None
            new_value = None
        else:
            source_uuid = source_candidate_uuid
            target_uuid = target_candidate_uuid
            old_value = None
            new_value = None
    else:
        if not target_asset_id:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Invalid+target+asset+ID",
                status_code=303,
            )
        try:
            target_uuid = coerce_uuid(target_asset_id)
        except Exception:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Invalid+target+asset+ID",
                status_code=303,
            )

    db.execute(
        text(
            """
            update fiber_qa_remediation_logs
               set asset_id = coalesce(:source_id, asset_id),
                   target_asset_id = :target_id,
                   old_value = :old_value,
                   new_value = :new_value,
                   status = :status,
                   review_notes = :review_notes
             where id = :log_id
               and asset_type = 'splice_closure'
               and issue_type = 'proximity_duplicate'
            """
        ),
        {
            "source_id": source_uuid,
            "target_id": target_uuid,
            "old_value": old_value,
            "new_value": new_value,
            "status": status_value,
            "review_notes": (review_notes or "").strip() or None,
            "log_id": log_id,
        },
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/network/qa/remediations?status={status_value}&message=Draft+saved",
        status_code=303,
    )


@router.post("/qa/remediations/{log_id}/approve-merge", response_class=HTMLResponse)
def qa_remediations_approve_merge(
    request: Request,
    log_id: int,
    source_asset_id: str | None = Form(None),
    candidate_target_asset_id: str | None = Form(None),
    keep_source: str | None = Form(None),
    keep_target: str | None = Form(None),
    keep_both_rename: str | None = Form(None),
    rename_side: str | None = Form(None),
    rename_to: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Approve and execute a merge for a pending proximity-duplicate closure."""
    from app.web.admin import get_current_user

    row = (
        db.execute(
            text(
                """
            select id, asset_type, asset_id, target_asset_id, status
              from fiber_qa_remediation_logs
             where id = :log_id
               and asset_type = 'splice_closure'
               and issue_type = 'proximity_duplicate'
            """
            ),
            {"log_id": log_id},
        )
        .mappings()
        .first()
    )
    if not row:
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error=Remediation+row+not+found",
            status_code=303,
        )

    if (row.get("status") or "").strip().lower() != "pending":
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error=Only+pending+rows+can+be+approved",
            status_code=303,
        )

    source_id = str(row["asset_id"])
    target_id = str(row["target_asset_id"] or "")

    keep_both_checked = bool(keep_both_rename)

    # Allow direct approve from checkbox selection form without a prior "Save Draft".
    if source_asset_id and candidate_target_asset_id:
        keep_source_checked = bool(keep_source)
        keep_target_checked = bool(keep_target)
        selected_count = int(keep_source_checked) + int(keep_target_checked) + int(keep_both_checked)
        if selected_count != 1:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Select+exactly+one+closure+to+keep",
                status_code=303,
            )
        try:
            source_candidate_uuid = coerce_uuid(source_asset_id)
            target_candidate_uuid = coerce_uuid(candidate_target_asset_id)
        except Exception:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Invalid+closure+IDs+in+draft",
                status_code=303,
            )
        if keep_both_checked:
            source_id = str(source_candidate_uuid)
            target_id = str(target_candidate_uuid)
        elif keep_source_checked:
            source_id = str(target_candidate_uuid)
            target_id = str(source_candidate_uuid)
        else:
            source_id = str(source_candidate_uuid)
            target_id = str(target_candidate_uuid)

    if not target_id:
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error=Missing+target+asset+ID",
            status_code=303,
        )

    current_user = get_current_user(request)
    person_id = str(current_user.get("person_id") or "").strip() or None
    performed_by = (
        (current_user.get("email") or "").strip() or (current_user.get("display_name") or "").strip() or "admin"
    )

    try:
        if keep_both_checked:
            rename_side_value = (rename_side or "target").strip().lower()
            if rename_side_value not in {"source", "target"}:
                rename_side_value = "target"
            rename_to_value = (rename_to or "").strip()
            if not rename_to_value:
                return RedirectResponse(
                    url="/admin/network/qa/remediations?status=pending&error=New+name+is+required+when+keeping+both",
                    status_code=303,
                )
            rename_asset_id = source_id if rename_side_value == "source" else target_id
            db.execute(
                text(
                    """
                    update fiber_splice_closures
                       set name = :new_name,
                           updated_at = :updated_at
                     where id = :asset_id
                       and is_active = true
                    """
                ),
                {
                    "new_name": rename_to_value,
                    "updated_at": datetime.now(UTC),
                    "asset_id": coerce_uuid(rename_asset_id),
                },
            )
            db.execute(
                text(
                    """
                    update fiber_qa_remediation_logs
                       set status = 'approved',
                           action_taken = 'renamed_keep_both',
                           old_value = :old_value,
                           new_value = :new_value,
                           performed_by = :performed_by,
                           approved_by = :approved_by,
                           approved_at = :approved_at
                     where id = :log_id
                    """
                ),
                {
                    "old_value": (
                        db.execute(
                            text(
                                """
                                select name
                                  from fiber_splice_closures
                                 where id = :asset_id
                                """
                            ),
                            {"asset_id": coerce_uuid(rename_asset_id)},
                        ).scalar_one_or_none()
                        or rename_side_value
                    ),
                    "new_value": rename_to_value,
                    "performed_by": performed_by,
                    "approved_by": coerce_uuid(person_id) if person_id else None,
                    "approved_at": datetime.now(UTC),
                    "log_id": log_id,
                },
            )
        else:
            fiber_plant.merge_assets(
                db,
                asset_type="splice_closure",
                source_id=source_id,
                target_id=target_id,
                field_choices={},
                merged_by_id=person_id,
            )
            db.execute(
                text(
                    """
                    update fiber_qa_remediation_logs
                       set status = 'approved',
                           action_taken = 'merged',
                           performed_by = :performed_by,
                           approved_by = :approved_by,
                           approved_at = :approved_at
                     where id = :log_id
                    """
                ),
                {
                    "performed_by": performed_by,
                    "approved_by": coerce_uuid(person_id) if person_id else None,
                    "approved_at": datetime.now(UTC),
                    "log_id": log_id,
                },
            )
        db.commit()
        message = "Rename+approved+and+applied+(both+closures+kept)" if keep_both_checked else "Merge+approved+and+applied"
        return RedirectResponse(
            url=f"/admin/network/qa/remediations?status=pending&message={message}",
            status_code=303,
        )
    except Exception as exc:
        db.rollback()
        logger.exception("qa_remediations_approve_merge_failed log_id=%s", log_id)
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error="
            + quote(str(getattr(exc, "detail", None) or str(exc) or "Merge failed")),
            status_code=303,
        )


@router.post("/qa/remediations/{log_id}/update-rename", response_class=HTMLResponse)
def qa_remediations_update_rename(
    request: Request,
    log_id: int,
    new_value: str = Form(...),
    status: str = Form("pending"),
    review_notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Update a bulk-rename draft. Does not apply to production assets."""
    allowed_status = {"pending", "ignored"}
    status_value = (status or "pending").strip().lower()
    if status_value not in allowed_status:
        status_value = "pending"

    new_name = (new_value or "").strip()
    if not new_name:
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error=Name+cannot+be+blank",
            status_code=303,
        )

    db.execute(
        text(
            """
            update fiber_qa_remediation_logs
               set new_value = :new_value,
                   status = :status,
                   review_notes = :review_notes
             where id = :log_id
               and asset_type = 'splice_closure'
               and issue_type = 'bulk_rename'
            """
        ),
        {
            "new_value": new_name,
            "status": status_value,
            "review_notes": (review_notes or "").strip() or None,
            "log_id": log_id,
        },
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/network/qa/remediations?status={status_value}&message=Draft+saved",
        status_code=303,
    )


@router.post("/qa/remediations/{log_id}/approve-rename", response_class=HTMLResponse)
def qa_remediations_approve_rename(
    request: Request,
    log_id: int,
    db: Session = Depends(get_db),
):
    """Approve and apply a bulk rename draft to the live closure row."""
    from app.web.admin import get_current_user

    row = (
        db.execute(
            text(
                """
            select id, asset_id, status, new_value
              from fiber_qa_remediation_logs
             where id = :log_id
               and asset_type = 'splice_closure'
               and issue_type = 'bulk_rename'
            """
            ),
            {"log_id": log_id},
        )
        .mappings()
        .first()
    )
    if not row:
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error=Remediation+row+not+found",
            status_code=303,
        )
    if (row.get("status") or "").strip().lower() != "pending":
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error=Only+pending+rows+can+be+approved",
            status_code=303,
        )

    new_name = (row.get("new_value") or "").strip()
    if not new_name:
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error=Missing+proposed+name",
            status_code=303,
        )

    current_user = get_current_user(request)
    performed_by = (
        (current_user.get("email") or "").strip() or (current_user.get("display_name") or "").strip() or "admin"
    )
    person_id = str(current_user.get("person_id") or "").strip() or None

    try:
        closure_id = coerce_uuid(str(row["asset_id"]))
        db.execute(
            text(
                """
                update fiber_splice_closures
                   set name = :new_name
                 where id = :closure_id
                   and is_active = true
                """
            ),
            {"new_name": new_name, "closure_id": closure_id},
        )
        db.execute(
            text(
                """
                update fiber_qa_remediation_logs
                   set status = 'approved',
                       action_taken = 'renamed',
                       performed_by = :performed_by,
                       approved_by = :approved_by,
                       approved_at = :approved_at
                 where id = :log_id
                """
            ),
            {
                "performed_by": performed_by,
                "approved_by": coerce_uuid(person_id) if person_id else None,
                "approved_at": datetime.now(UTC),
                "log_id": log_id,
            },
        )
        db.commit()
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&message=Rename+approved+and+applied",
            status_code=303,
        )
    except Exception as exc:
        db.rollback()
        logger.exception("qa_remediations_approve_rename_failed log_id=%s", log_id)
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error="
            + quote(str(getattr(exc, "detail", None) or str(exc) or "Rename failed")),
            status_code=303,
        )


@router.post("/qa/remediations/{log_id}/update-segment-dup", response_class=HTMLResponse)
def qa_remediations_update_segment_dup(
    request: Request,
    log_id: int,
    target_asset_id: str | None = Form(None),
    source_asset_id: str | None = Form(None),
    candidate_target_asset_id: str | None = Form(None),
    keep_source: str | None = Form(None),
    keep_target: str | None = Form(None),
    status: str = Form("pending"),
    review_notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Update a staged segment geometry-duplicate item (registry only)."""
    allowed_status = {"pending", "ignored"}
    status_value = (status or "pending").strip().lower()
    if status_value not in allowed_status:
        status_value = "pending"

    source_uuid = None
    target_uuid = None
    use_keep_selector = bool(source_asset_id and candidate_target_asset_id)

    if use_keep_selector:
        keep_source_checked = bool(keep_source)
        keep_target_checked = bool(keep_target)
        if keep_source_checked == keep_target_checked:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Select+exactly+one+segment+to+keep",
                status_code=303,
            )
        try:
            source_candidate_uuid = coerce_uuid(source_asset_id)
            target_candidate_uuid = coerce_uuid(candidate_target_asset_id)
        except Exception:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Invalid+segment+IDs+in+draft",
                status_code=303,
            )
        if keep_source_checked:
            source_uuid = target_candidate_uuid
            target_uuid = source_candidate_uuid
        else:
            source_uuid = source_candidate_uuid
            target_uuid = target_candidate_uuid
    else:
        if not target_asset_id:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Invalid+target+segment+ID",
                status_code=303,
            )
        try:
            target_uuid = coerce_uuid(target_asset_id)
        except Exception:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Invalid+target+segment+ID",
                status_code=303,
            )

    db.execute(
        text(
            """
            update fiber_qa_remediation_logs
               set asset_id = coalesce(:source_id, asset_id),
                   target_asset_id = :target_id,
                   status = :status,
                   review_notes = :review_notes
             where id = :log_id
               and asset_type = 'fiber_segment'
               and issue_type = 'geometry_duplicate'
            """
        ),
        {
            "source_id": source_uuid,
            "target_id": target_uuid,
            "status": status_value,
            "review_notes": (review_notes or "").strip() or None,
            "log_id": log_id,
        },
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/network/qa/remediations?status={status_value}&message=Draft+saved",
        status_code=303,
    )


@router.post("/qa/remediations/{log_id}/update-manual-connectivity", response_class=HTMLResponse)
def qa_remediations_update_manual_connectivity(
    request: Request,
    log_id: int,
    from_value: str = Form(""),
    to_value: str = Form(""),
    status: str = Form("pending"),
    review_notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Update a staged manual connectivity request (registry only)."""
    allowed_status = {"pending", "ignored"}
    status_value = (status or "pending").strip().lower()
    if status_value not in allowed_status:
        status_value = "pending"

    from_clean = (from_value or "").strip()
    to_clean = (to_value or "").strip()
    if not from_clean or not to_clean:
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error=FROM+and+TO+are+required",
            status_code=303,
        )

    new_value_json = json.dumps({"from": from_clean, "to": to_clean}, ensure_ascii=True, sort_keys=True)

    db.execute(
        text(
            """
            update fiber_qa_remediation_logs
               set new_value = :new_value,
                   status = :status,
                   review_notes = :review_notes
             where id = :log_id
               and asset_type = 'fiber_segment'
               and issue_type = 'manual_connectivity'
            """
        ),
        {
            "new_value": new_value_json,
            "status": status_value,
            "review_notes": (review_notes or "").strip() or None,
            "log_id": log_id,
        },
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/network/qa/remediations?status={status_value}&message=Draft+saved",
        status_code=303,
    )


@router.post("/qa/remediations/stage-manual-connectivity", response_class=HTMLResponse)
def qa_remediations_stage_manual_connectivity(
    request: Request,
    segment_id: str = Form(...),
    from_value: str = Form(""),
    to_value: str = Form(""),
    review_notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Create or update a pending manual connectivity draft for a segment (registry only)."""
    from app.web.admin import get_current_user

    seg_input = (segment_id or "").strip()
    if not seg_input:
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error=Segment+is+required",
            status_code=303,
        )

    seg_uuid = None
    try:
        seg_uuid = coerce_uuid(seg_input)
    except Exception:
        seg_uuid = None

    from_clean = (from_value or "").strip()
    to_clean = (to_value or "").strip()
    if not from_clean or not to_clean:
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&error=FROM+and+TO+are+required",
            status_code=303,
        )

    # Resolve segment by UUID (preferred) or by name/code (fallback).
    if seg_uuid:
        exists = (
            db.execute(
                text("select 1 from fiber_segments where id = :id and is_active = true"),
                {"id": seg_uuid},
            ).scalar()
            is not None
        )
        if not exists:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Segment+not+found",
                status_code=303,
            )
    else:
        matches = (
            db.execute(
                text(
                    """
                    select id
                      from fiber_segments
                     where is_active = true
                       and name = :name
                     order by updated_at desc nulls last, created_at desc nulls last, id asc
                     limit 2
                    """
                ),
                {"name": seg_input},
            )
            .scalars()
            .all()
        )
        if len(matches) == 0:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=No+segment+found+for+that+code",
                status_code=303,
            )
        if len(matches) > 1:
            return RedirectResponse(
                url="/admin/network/qa/remediations?status=pending&error=Segment+code+is+ambiguous%3B+use+the+UUID",
                status_code=303,
            )
        seg_uuid = matches[0]

    current_user = get_current_user(request)
    performed_by = (
        (current_user.get("email") or "").strip() or (current_user.get("display_name") or "").strip() or "admin"
    )

    new_value_json = json.dumps({"from": from_clean, "to": to_clean}, ensure_ascii=True, sort_keys=True)
    notes_clean = (review_notes or "").strip() or None

    # Keep it surgical: at most one pending draft per segment. If it already exists, update it.
    pending_row_id = (
        db.execute(
            text(
                """
                select id
                  from fiber_qa_remediation_logs
                 where asset_type = 'fiber_segment'
                   and issue_type = 'manual_connectivity'
                   and status = 'pending'
                   and asset_id = :asset_id
                 order by id desc
                 limit 1
                """
            ),
            {"asset_id": seg_uuid},
        ).scalar()
        or None
    )
    if pending_row_id:
        db.execute(
            text(
                """
                update fiber_qa_remediation_logs
                   set new_value = :new_value,
                       review_notes = :review_notes,
                       performed_by = :performed_by
                 where id = :id
                """
            ),
            {
                "new_value": new_value_json,
                "review_notes": notes_clean,
                "performed_by": performed_by,
                "id": int(pending_row_id),
            },
        )
        db.commit()
        return RedirectResponse(
            url="/admin/network/qa/remediations?status=pending&message=Draft+updated",
            status_code=303,
        )

    db.execute(
        text(
            """
            insert into fiber_qa_remediation_logs (
                asset_type,
                asset_id,
                issue_type,
                status,
                action_taken,
                performed_by,
                new_value,
                review_notes
            ) values (
                'fiber_segment',
                :asset_id,
                'manual_connectivity',
                'pending',
                'endpoint_assignment',
                :performed_by,
                :new_value,
                :review_notes
            )
            """
        ),
        {
            "asset_id": seg_uuid,
            "performed_by": performed_by,
            "new_value": new_value_json,
            "review_notes": notes_clean,
        },
    )
    db.commit()
    return RedirectResponse(
        url="/admin/network/qa/remediations?status=pending&message=Draft+created",
        status_code=303,
    )


@router.get("/qa/segments/lookup")
def qa_segments_lookup(
    query: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """Lookup a segment by UUID or exact name, and return its start/end coordinates.

    This is read-only and exists to help users draft manual connectivity.
    """
    q = (query or "").strip()
    if not q:
        return JSONResponse({"error": "query is required"}, status_code=400)

    seg_uuid = None
    try:
        seg_uuid = coerce_uuid(q)
    except Exception:
        seg_uuid = None

    if seg_uuid:
        row = (
            db.execute(
                text(
                    """
                    select
                        id,
                        name,
                        from_point_id,
                        to_point_id,
                        st_y(st_startpoint(st_linemerge(route_geom))) as start_lat,
                        st_x(st_startpoint(st_linemerge(route_geom))) as start_lng,
                        st_y(st_endpoint(st_linemerge(route_geom))) as end_lat,
                        st_x(st_endpoint(st_linemerge(route_geom))) as end_lng
                    from fiber_segments
                    where is_active = true
                      and id = :id
                    """
                ),
                {"id": seg_uuid},
            )
            .mappings()
            .first()
        )
        if not row:
            return JSONResponse({"error": "Segment not found"}, status_code=404)
        return JSONResponse({"segment": {k: _json_safe(v) for k, v in dict(row).items()}})

    matches = (
        db.execute(
            text(
                """
                select
                    id,
                    name,
                    from_point_id,
                    to_point_id,
                    st_y(st_startpoint(st_linemerge(route_geom))) as start_lat,
                    st_x(st_startpoint(st_linemerge(route_geom))) as start_lng,
                    st_y(st_endpoint(st_linemerge(route_geom))) as end_lat,
                    st_x(st_endpoint(st_linemerge(route_geom))) as end_lng
                from fiber_segments
                where is_active = true
                  and name = :name
                order by updated_at desc nulls last, created_at desc nulls last, id asc
                limit 2
                """
            ),
            {"name": q},
        )
        .mappings()
        .all()
    )
    if len(matches) == 0:
        return JSONResponse({"error": "No segment found for that code"}, status_code=404)
    if len(matches) > 1:
        return JSONResponse({"error": "Segment code is ambiguous; use the UUID"}, status_code=409)
    return JSONResponse({"segment": {k: _json_safe(v) for k, v in dict(matches[0]).items()}})
