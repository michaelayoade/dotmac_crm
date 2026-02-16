"""Admin network map routes and helpers."""

from __future__ import annotations

import logging
import math
from typing import cast

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

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
            key: value for key, value in vars(asset).items() if not key.startswith("_sa_") and not key.startswith("_")
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

    change_request_service.reject_request(db, request_id, reviewer_person_id, review_notes.strip())
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
