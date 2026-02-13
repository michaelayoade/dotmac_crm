"""Service helpers for vendor portal routes."""

import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.models.projects import Project
from app.models.rbac import PersonRole, Role
from app.models.vendor import InstallationProject
from app.services import vendor as vendor_service
from app.services import vendor_portal
from app.services.common import coerce_uuid

templates = Jinja2Templates(directory="templates")

_VENDOR_ROLE_NAME = "vendors"


def _coerce_float(value: object | None, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _require_vendor_context(request: Request, db: Session):
    context = vendor_portal.get_context(db, request.cookies.get(vendor_portal.SESSION_COOKIE_NAME))
    if not context:
        return None
    return context


def _resolve_installation_project(db: Session, project_ref: str) -> InstallationProject:
    # Accept canonical installation project UUIDs and friendly project code/number refs.
    try:
        return vendor_service.installation_projects.get(db, project_ref)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise

    query = (
        db.query(InstallationProject)
        .join(Project, Project.id == InstallationProject.project_id)
        .filter(InstallationProject.is_active.is_(True))
    )
    project = query.filter((Project.code == project_ref) | (Project.number == project_ref)).first()
    if project:
        return project

    try:
        project_uuid = coerce_uuid(project_ref)
    except Exception:
        project_uuid = None
    if project_uuid:
        project = query.filter(InstallationProject.project_id == project_uuid).first()
        if project:
            return project

    raise HTTPException(status_code=404, detail="Installation project not found")


def _as_built_eligible_project_ids(db: Session, projects: list[InstallationProject], vendor_id: str) -> set[str]:
    from app.models.vendor import ProjectQuote, ProjectQuoteStatus

    project_ids = [project.id for project in projects]
    if not project_ids:
        return set()
    eligible_statuses = (ProjectQuoteStatus.submitted, ProjectQuoteStatus.under_review, ProjectQuoteStatus.approved)
    rows = (
        db.query(ProjectQuote.project_id)
        .filter(
            ProjectQuote.project_id.in_(project_ids),
            ProjectQuote.vendor_id == coerce_uuid(vendor_id),
            ProjectQuote.is_active.is_(True),
            ProjectQuote.status.in_(eligible_statuses),
        )
        .distinct()
        .all()
    )
    return {str(row[0]) for row in rows}


def _has_vendor_role(db: Session, person_id: str, vendor_role: str | None) -> bool:
    if vendor_role and vendor_role.strip().lower() == _VENDOR_ROLE_NAME:
        return True
    role = db.query(Role).filter(Role.name.ilike(_VENDOR_ROLE_NAME)).first()
    if not role:
        return False
    return (
        db.query(PersonRole).filter(PersonRole.person_id == person_id).filter(PersonRole.role_id == role.id).first()
        is not None
    )


def vendor_home(request: Request, db: Session):
    context = _require_vendor_context(request, db)
    if not context:
        return RedirectResponse(url="/vendor/auth/login", status_code=303)
    return RedirectResponse(url="/vendor/dashboard", status_code=303)


def vendor_dashboard(request: Request, db: Session):
    context = _require_vendor_context(request, db)
    if not context:
        return RedirectResponse(url="/vendor/auth/login", status_code=303)
    vendor_id = str(context["vendor"].id)
    available = vendor_service.installation_projects.list_available_for_vendor(db, vendor_id, limit=10, offset=0)
    mine = vendor_service.installation_projects.list_for_vendor(db, vendor_id, limit=10, offset=0)
    as_built_project_ids = _as_built_eligible_project_ids(db, mine, vendor_id)
    return templates.TemplateResponse(
        "vendor/dashboard/index.html",
        {
            "request": request,
            "active_page": "dashboard",
            "vendor": context["vendor"],
            "current_user": context["current_user"],
            "available_projects": available,
            "my_projects": mine,
            "as_built_project_ids": as_built_project_ids,
        },
    )


def vendor_projects_available(request: Request, db: Session):
    context = _require_vendor_context(request, db)
    if not context:
        return RedirectResponse(url="/vendor/auth/login", status_code=303)
    vendor_id = str(context["vendor"].id)
    projects = vendor_service.installation_projects.list_available_for_vendor(db, vendor_id, limit=50, offset=0)
    return templates.TemplateResponse(
        "vendor/projects/available.html",
        {
            "request": request,
            "active_page": "available-projects",
            "vendor": context["vendor"],
            "current_user": context["current_user"],
            "projects": projects,
        },
    )


def vendor_projects_mine(request: Request, db: Session):
    context = _require_vendor_context(request, db)
    if not context:
        return RedirectResponse(url="/vendor/auth/login", status_code=303)
    vendor_id = str(context["vendor"].id)
    projects = vendor_service.installation_projects.list_for_vendor(db, vendor_id, limit=50, offset=0)
    as_built_project_ids = _as_built_eligible_project_ids(db, projects, vendor_id)
    return templates.TemplateResponse(
        "vendor/projects/my-projects.html",
        {
            "request": request,
            "active_page": "fiber-map",
            "vendor": context["vendor"],
            "current_user": context["current_user"],
            "projects": projects,
            "as_built_project_ids": as_built_project_ids,
        },
    )


def quote_builder(request: Request, project_id: str, db: Session):
    context = _require_vendor_context(request, db)
    if not context:
        return RedirectResponse(url="/vendor/auth/login", status_code=303)
    vendor_id = str(context["vendor"].id)
    project = _resolve_installation_project(db, project_id)
    existing_quote = vendor_service.project_quotes.get_latest_for_vendor_project(
        db,
        installation_project_id=str(project.id),
        vendor_id=vendor_id,
    )
    # Prevent vendors from accessing arbitrary InstallationProjects by ID.
    from datetime import UTC, datetime

    from app.models.vendor import InstallationProjectStatus

    now = datetime.now(UTC)
    is_direct_assigned = str(project.assigned_vendor_id or "") == vendor_id
    is_open_for_bidding = (
        project.status == InstallationProjectStatus.open_for_bidding
        and project.bidding_open_at
        and project.bidding_open_at <= now
        and project.bidding_close_at
        and project.bidding_close_at >= now
    )
    if not (is_direct_assigned or is_open_for_bidding or existing_quote):
        return HTMLResponse(content="Forbidden", status_code=403)

    quote = existing_quote or vendor_service.project_quotes.get_or_create_for_vendor_project(
        db,
        installation_project_id=str(project.id),
        vendor_id=vendor_id,
        created_by_person_id=str(context["person"].id),
    )
    route_revisions = vendor_service.proposed_route_revisions.list(
        db,
        quote_id=str(quote.id),
        status=None,
        order_by="revision_number",
        order_dir="desc",
        limit=50,
        offset=0,
    )
    line_items = vendor_service.quote_line_items.list(
        db,
        quote_id=str(quote.id),
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    return templates.TemplateResponse(
        "vendor/quotes/builder.html",
        {
            "request": request,
            "active_page": "quote-builder",
            "vendor": context["vendor"],
            "current_user": context["current_user"],
            "project": project,
            "quote": quote,
            "route_revisions": route_revisions,
            "line_items": line_items,
        },
    )


def as_built_submit(request: Request, project_id: str, db: Session):
    context = _require_vendor_context(request, db)
    if not context:
        return RedirectResponse(url="/vendor/auth/login", status_code=303)
    vendor_id = str(context["vendor"].id)
    project = _resolve_installation_project(db, project_id)
    existing_quote = vendor_service.project_quotes.get_latest_for_vendor_project(
        db,
        installation_project_id=str(project.id),
        vendor_id=vendor_id,
    )
    approved_quote_vendor_match = False
    if project.approved_quote_id:
        approved_quote = None
        try:
            approved_quote = vendor_service.project_quotes.get(db, str(project.approved_quote_id))
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        if approved_quote:
            approved_quote_vendor_match = str(approved_quote.vendor_id) == vendor_id

    # Allow as-built only for assigned vendor, approved quote vendor, or a vendor with an existing quote.
    is_assigned_vendor = str(project.assigned_vendor_id or "") == vendor_id
    if not (is_assigned_vendor or approved_quote_vendor_match or existing_quote):
        return HTMLResponse(content="Forbidden", status_code=403)
    if not vendor_service.project_quotes.has_submitted_for_vendor_project(db, str(project.id), vendor_id):
        return HTMLResponse(content="Quote must be submitted before as-built can be provided.", status_code=403)
    return templates.TemplateResponse(
        "vendor/as-built/submit.html",
        {
            "request": request,
            "active_page": "as-built",
            "vendor": context["vendor"],
            "current_user": context["current_user"],
            "project": project,
            "project_id": str(project.id),
        },
    )


def vendor_fiber_map(request: Request, db: Session):
    context = _require_vendor_context(request, db)
    if not context:
        return RedirectResponse(url="/vendor/auth/login", status_code=303)
    if not _has_vendor_role(db, str(context["person"].id), context["vendor_user"].role):
        return HTMLResponse(content="Forbidden", status_code=403)
    initial_quote_id = request.query_params.get("quote_id") or ""

    import json

    from sqlalchemy import func

    from app.models.domain_settings import SettingDomain
    from app.models.network import FdhCabinet, FiberSegment, FiberSplice, FiberSpliceClosure, FiberSpliceTray, Splitter
    from app.services import settings_spec
    from app.services.fiber_plant import fiber_plant

    features = []

    # FDH Cabinets
    fdh_cabinets = (
        db.query(FdhCabinet)
        .filter(FdhCabinet.is_active.is_(True), FdhCabinet.latitude.isnot(None), FdhCabinet.longitude.isnot(None))
        .all()
    )
    splitter_counts: dict[uuid.UUID | None, int] = {}
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
        .filter(
            FiberSpliceClosure.is_active.is_(True),
            FiberSpliceClosure.latitude.isnot(None),
            FiberSpliceClosure.longitude.isnot(None),
        )
        .all()
    )
    splice_counts: dict[uuid.UUID | None, int] = {}
    tray_counts: dict[uuid.UUID | None, int] = {}
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

    # Fiber Segments
    segments = db.query(FiberSegment).filter(FiberSegment.is_active.is_(True)).all()
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
        "splice_closures": db.query(func.count(FiberSpliceClosure.id))
        .filter(FiberSpliceClosure.is_active.is_(True))
        .scalar(),
        "closures_with_location": len(closures),
        "splitters": db.query(func.count(Splitter.id)).filter(Splitter.is_active.is_(True)).scalar(),
        "total_splices": db.query(func.count(FiberSplice.id)).scalar(),
        "segments": len(segments),
    }
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
        "currency": str(settings_spec.resolve_value(db, SettingDomain.billing, "default_currency") or "NGN"),
    }

    return templates.TemplateResponse(
        "vendor/projects/fiber-map.html",
        {
            "request": request,
            "active_page": "my-projects",
            "vendor": context["vendor"],
            "current_user": context["current_user"],
            "geojson_data": geojson_data,
            "stats": stats,
            "qa_stats": qa_stats,
            "cost_settings": cost_settings,
            "initial_quote_id": initial_quote_id,
        },
    )


async def vendor_fiber_map_update_position(request: Request, db: Session):
    context = _require_vendor_context(request, db)
    if not context:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not _has_vendor_role(db, str(context["person"].id), context["vendor_user"].role):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    from app.models.fiber_change_request import FiberChangeRequestOperation
    from app.services import fiber_change_requests as change_request_service

    try:
        data = await request.json()
        asset_type = data.get("type")
        asset_id = data.get("id")
        latitude = data.get("latitude")
        longitude = data.get("longitude")

        if not all([asset_type, asset_id, latitude is not None, longitude is not None]):
            return JSONResponse({"error": "Missing required fields"}, status_code=400)

        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError):
            return JSONResponse({"error": "Invalid coordinates"}, status_code=400)

        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return JSONResponse({"error": "Coordinates out of range"}, status_code=400)

        request_record = change_request_service.create_request(
            db,
            asset_type=asset_type,
            asset_id=asset_id,
            operation=FiberChangeRequestOperation.update,
            payload={"latitude": latitude, "longitude": longitude},
            requested_by_person_id=str(context["person"].id),
            requested_by_vendor_id=str(context["vendor"].id),
        )

        return JSONResponse(
            {
                "success": True,
                "request_id": str(request_record.id),
                "status": request_record.status.value,
            }
        )
    except Exception as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, status_code=500)


async def vendor_fiber_map_nearest_cabinet(request: Request, lat: float, lng: float, db: Session):
    context = _require_vendor_context(request, db)
    if not context:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not _has_vendor_role(db, str(context["person"].id), context["vendor_user"].role):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    from app.web.admin import network as admin_network

    return await admin_network.find_nearest_cabinet(request, lat, lng, db)


async def vendor_fiber_map_plan_options(request: Request, lat: float, lng: float, db: Session):
    context = _require_vendor_context(request, db)
    if not context:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not _has_vendor_role(db, str(context["person"].id), context["vendor_user"].role):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    from app.web.admin import network as admin_network

    return await admin_network.plan_options(request, lat, lng, db)


async def vendor_fiber_map_route(request: Request, lat: float, lng: float, cabinet_id: str, db: Session):
    context = _require_vendor_context(request, db)
    if not context:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not _has_vendor_role(db, str(context["person"].id), context["vendor_user"].role):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    from app.web.admin import network as admin_network

    return await admin_network.plan_route(request, lat, lng, cabinet_id, db)
