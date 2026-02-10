"""Admin network map routes and helpers."""

from __future__ import annotations

import json
import math
from typing import cast

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, text
from sqlalchemy.orm import Session, load_only

from app.db import SessionLocal
from app.models.domain_settings import SettingDomain
from app.models.fiber_change_request import FiberChangeRequestOperation
from app.models.gis import GeoAreaType, GeoLocation, GeoLocationType
from app.models.network import (
    FdhCabinet,
    FiberAccessPoint,
    FiberSegment,
    FiberSplice,
    FiberSpliceClosure,
    FiberSpliceTray,
    Splitter,
)
from app.services import fiber_change_requests as change_request_service
from app.services import gis as gis_service
from app.services import settings_spec
from app.services import vendor as vendor_service
from app.services.common import coerce_uuid

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/network", tags=["web-admin-network"])


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
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
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


def _postgis_available(db: Session) -> bool:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return False
    try:
        return db.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")).scalar() == 1
    except Exception:
        return False


@router.get("/map", response_class=HTMLResponse)
def network_map(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user, get_sidebar_stats

    features: list[dict] = []

    # FDH Cabinets
    fdh_cabinets = (
        db.query(FdhCabinet)
        .options(
            load_only(
                FdhCabinet.id,
                FdhCabinet.name,
                FdhCabinet.code,
                FdhCabinet.latitude,
                FdhCabinet.longitude,
                FdhCabinet.is_active,
            )
        )
        .filter(
            FdhCabinet.is_active.is_(True),
            FdhCabinet.latitude.isnot(None),
            FdhCabinet.longitude.isnot(None),
        )
        .all()
    )
    splitter_counts: dict = {}
    if fdh_cabinets:
        fdh_ids = [fdh.id for fdh in fdh_cabinets]
        splitter_counts = {
            row[0]: row[1]
            for row in (
                db.query(Splitter.fdh_id, func.count(Splitter.id))
                .filter(Splitter.fdh_id.in_(fdh_ids))
                .group_by(Splitter.fdh_id)
                .all()
            )
        }
    for fdh in fdh_cabinets:
        splitter_count = splitter_counts.get(fdh.id, 0)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [fdh.longitude, fdh.latitude]},
                "properties": {
                    "id": str(fdh.id),
                    "type": "fdh_cabinet",
                    "name": fdh.name,
                    "code": fdh.code,
                    "splitter_count": splitter_count,
                },
            }
        )

    # Splice Closures
    closures = (
        db.query(FiberSpliceClosure)
        .options(
            load_only(
                FiberSpliceClosure.id,
                FiberSpliceClosure.name,
                FiberSpliceClosure.latitude,
                FiberSpliceClosure.longitude,
                FiberSpliceClosure.is_active,
            )
        )
        .filter(
            FiberSpliceClosure.is_active.is_(True),
            FiberSpliceClosure.latitude.isnot(None),
            FiberSpliceClosure.longitude.isnot(None),
        )
        .all()
    )
    splice_counts: dict = {}
    tray_counts: dict = {}
    if closures:
        closure_ids = [closure.id for closure in closures]
        splice_counts = {
            row[0]: row[1]
            for row in (
                db.query(FiberSplice.closure_id, func.count(FiberSplice.id))
                .filter(FiberSplice.closure_id.in_(closure_ids))
                .group_by(FiberSplice.closure_id)
                .all()
            )
        }
        tray_counts = {
            row[0]: row[1]
            for row in (
                db.query(FiberSpliceTray.closure_id, func.count(FiberSpliceTray.id))
                .filter(FiberSpliceTray.closure_id.in_(closure_ids))
                .group_by(FiberSpliceTray.closure_id)
                .all()
            )
        }
    for closure in closures:
        splice_count = splice_counts.get(closure.id, 0)
        tray_count = tray_counts.get(closure.id, 0)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [closure.longitude, closure.latitude]},
                "properties": {
                    "id": str(closure.id),
                    "type": "splice_closure",
                    "name": closure.name,
                    "splice_count": splice_count,
                    "tray_count": tray_count,
                },
            }
        )

    # Access Points
    access_points = (
        db.query(FiberAccessPoint)
        .options(
            load_only(
                FiberAccessPoint.id,
                FiberAccessPoint.name,
                FiberAccessPoint.code,
                FiberAccessPoint.access_point_type,
                FiberAccessPoint.placement,
                FiberAccessPoint.latitude,
                FiberAccessPoint.longitude,
                FiberAccessPoint.is_active,
            )
        )
        .filter(
            FiberAccessPoint.is_active.is_(True),
            FiberAccessPoint.latitude.isnot(None),
            FiberAccessPoint.longitude.isnot(None),
        )
        .all()
    )
    for ap in access_points:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [ap.longitude, ap.latitude]},
                "properties": {
                    "id": str(ap.id),
                    "type": "access_point",
                    "name": ap.name,
                    "code": ap.code,
                    "ap_type": ap.access_point_type,
                    "placement": ap.placement,
                },
            }
        )

    # Fiber Segments
    segments_count = (
        db.query(func.count(FiberSegment.id))
        .filter(FiberSegment.is_active.is_(True))
        .scalar()
        or 0
    )
    segment_geoms = []
    if _postgis_available(db):
        segment_geoms = (
            db.query(FiberSegment, func.ST_AsGeoJSON(FiberSegment.route_geom))
            .filter(
                FiberSegment.is_active.is_(True),
                FiberSegment.route_geom.isnot(None),
            )
            .all()
        )
    for segment, geojson_str in segment_geoms:
        if not geojson_str:
            continue
        geom = json.loads(geojson_str)
        features.append(
            {
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "id": str(segment.id),
                    "type": "fiber_segment",
                    "name": segment.name,
                    "segment_type": segment.segment_type.value if segment.segment_type else None,
                    "cable_type": segment.cable_type.value if segment.cable_type else None,
                    "fiber_count": segment.fiber_count,
                    "length_m": segment.length_m,
                },
            }
        )

    geojson_data = {"type": "FeatureCollection", "features": features}

    stats = {
        "fdh_cabinets": db.query(func.count(FdhCabinet.id)).filter(FdhCabinet.is_active.is_(True)).scalar(),
        "fdh_with_location": len(fdh_cabinets),
        "splice_closures": db.query(func.count(FiberSpliceClosure.id)).filter(FiberSpliceClosure.is_active.is_(True)).scalar(),
        "closures_with_location": len(closures),
        "splitters": db.query(func.count(Splitter.id)).filter(Splitter.is_active.is_(True)).scalar(),
        "total_splices": db.query(func.count(FiberSplice.id)).scalar(),
        "segments": segments_count,
        "access_points": db.query(func.count(FiberAccessPoint.id)).filter(FiberAccessPoint.is_active.is_(True)).scalar(),
        "access_points_with_location": len(access_points),
    }

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

    cabinets = (
        db.query(FdhCabinet)
        .order_by(FdhCabinet.name.asc())
        .all()
    )
    stats = {
        "total": db.query(func.count(FdhCabinet.id)).scalar() or 0,
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


    regions = gis_service.geo_areas.list(
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
    return templates.TemplateResponse(
        "admin/network/fiber/fdh-cabinet-form.html",
        {
            "request": request,
            "active_page": "fdh-cabinets",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "cabinet": None,
            "regions": regions,
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
        error = str(exc)

    from app.web.admin import get_current_user, get_sidebar_stats

    regions = gis_service.geo_areas.list(
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
    return templates.TemplateResponse(
        "admin/network/fiber/fdh-cabinet-form.html",
        {
            "request": request,
            "active_page": "fdh-cabinets",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "cabinet": None,
            "regions": regions,
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
    regions = gis_service.geo_areas.list(
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
    region_map = {str(region.id): region for region in regions}
    cabinet.region = region_map.get(str(cabinet.region_id)) if cabinet.region_id else None
    splitters = (
        db.query(Splitter)
        .filter(Splitter.fdh_id == cabinet.id)
        .order_by(Splitter.name.asc())
        .all()
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
            "splitters": splitters,
            "activities": [],
        },
    )


@router.get("/fdh-cabinets/{cabinet_id}/edit", response_class=HTMLResponse)
def fdh_cabinet_edit(request: Request, cabinet_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user, get_sidebar_stats

    cabinet = db.get(FdhCabinet, cabinet_id)
    if not cabinet:
        return RedirectResponse(url="/admin/network/fdh-cabinets", status_code=303)
    regions = gis_service.geo_areas.list(
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
    return templates.TemplateResponse(
        "admin/network/fiber/fdh-cabinet-form.html",
        {
            "request": request,
            "active_page": "fdh-cabinets",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "cabinet": cabinet,
            "regions": regions,
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
        error = str(exc)

    from app.web.admin import get_current_user, get_sidebar_stats

    regions = gis_service.geo_areas.list(
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
    return templates.TemplateResponse(
        "admin/network/fiber/fdh-cabinet-form.html",
        {
            "request": request,
            "active_page": "fdh-cabinets",
            "active_menu": "network",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "cabinet": cabinet,
            "regions": regions,
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
    except Exception as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, status_code=500)


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
    except Exception as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/fiber-map/nearest-cabinet")
async def fiber_map_nearest_cabinet(lat: float, lng: float, db: Session = Depends(get_db)):
    return await find_nearest_cabinet(None, lat, lng, db)


async def find_nearest_cabinet(_request: Request | None, lat: float, lng: float, db: Session):
    try:
        lat_f, lng_f = _parse_lat_lng(lat, lng)
        cabinets = db.query(FdhCabinet).filter(
            FdhCabinet.is_active.is_(True),
            FdhCabinet.latitude.isnot(None),
            FdhCabinet.longitude.isnot(None),
        ).all()
        if not cabinets:
            return JSONResponse({"error": "No cabinets found"}, status_code=404)

        nearest = None
        nearest_dist = None
        for cabinet in cabinets:
            if cabinet.latitude is None or cabinet.longitude is None:
                continue
            dist = _haversine_distance_m(
                lat_f, lng_f, float(cabinet.latitude), float(cabinet.longitude)
            )
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
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/fiber-map/plan-options")
async def fiber_map_plan_options(lat: float, lng: float, db: Session = Depends(get_db)):
    return await plan_options(None, lat, lng, db)


async def plan_options(_request: Request | None, lat: float, lng: float, db: Session):
    try:
        lat_f, lng_f = _parse_lat_lng(lat, lng)
        cabinets = db.query(FdhCabinet).filter(
            FdhCabinet.is_active.is_(True),
            FdhCabinet.latitude.isnot(None),
            FdhCabinet.longitude.isnot(None),
        ).all()
        options = []
        for cabinet in cabinets:
            if cabinet.latitude is None or cabinet.longitude is None:
                continue
            dist = _haversine_distance_m(
                lat_f, lng_f, float(cabinet.latitude), float(cabinet.longitude)
            )
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
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/fiber-map/route")
async def fiber_map_route(lat: float, lng: float, cabinet_id: str, db: Session = Depends(get_db)):
    return await plan_route(None, lat, lng, cabinet_id, db)


async def plan_route(_request: Request | None, lat: float, lng: float, cabinet_id: str, db: Session):
    try:
        lat_f, lng_f = _parse_lat_lng(lat, lng)
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
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
