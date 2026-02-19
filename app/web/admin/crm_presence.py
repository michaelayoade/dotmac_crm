"""CRM presence and live-map web routes."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.crm.enums import AgentPresenceStatus
from app.services import crm as crm_service
from app.services.crm.inbox.agents import get_current_agent_id

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_current_roles(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        roles = auth.get("roles") or []
        if isinstance(roles, list):
            return [str(role) for role in roles]
    return []


def _get_current_scopes(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        scopes = auth.get("scopes") or []
        if isinstance(scopes, list):
            return [str(scope) for scope in scopes]
    return []


def _is_admin_request(request: Request) -> bool:
    roles = _get_current_roles(request)
    return any(role.strip().lower() == "admin" for role in roles)


def _is_manager_request(request: Request) -> bool:
    roles = _get_current_roles(request)
    return any(role.strip().lower() == "manager" for role in roles)


def _can_view_live_location_map(request: Request) -> bool:
    if _is_admin_request(request) or _is_manager_request(request):
        return True
    scopes = {scope.strip().lower() for scope in _get_current_scopes(request)}
    return "crm:location:read" in scopes


@router.post("/agents/presence", response_class=JSONResponse)
async def update_current_agent_presence(
    request: Request,
    db: Session = Depends(get_db),
):
    """Update presence for the current CRM agent (derived from logged-in user)."""
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    agent_id = get_current_agent_id(db, (current_user or {}).get("person_id"))
    if not agent_id:
        return Response(status_code=204)

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    status = payload.get("status") if isinstance(payload, dict) else None
    if status is not None and status not in {s.value for s in AgentPresenceStatus}:
        raise HTTPException(status_code=400, detail="Invalid status")

    crm_service.agent_presence.upsert(db, agent_id, status=status, source="auto")
    return JSONResponse({"ok": True})


@router.post("/agents/presence/location", response_class=JSONResponse)
async def update_current_agent_location_presence(
    request: Request,
    db: Session = Depends(get_db),
):
    """Update location heartbeat for current CRM agent."""
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    agent_id = get_current_agent_id(db, (current_user or {}).get("person_id"))
    if not agent_id:
        return Response(status_code=204)

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    status = payload.get("status")
    if status is not None and status not in {s.value for s in AgentPresenceStatus}:
        raise HTTPException(status_code=400, detail="Invalid status")

    sharing_enabled = bool(payload.get("sharing_enabled"))
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    accuracy_m = payload.get("accuracy_m")
    captured_at_raw = payload.get("captured_at")
    captured_at = None
    if isinstance(captured_at_raw, str) and captured_at_raw.strip():
        try:
            captured_at = datetime.fromisoformat(captured_at_raw.replace("Z", "+00:00"))
        except Exception:
            captured_at = None

    presence = crm_service.agent_presence.upsert_location(
        db,
        agent_id=agent_id,
        sharing_enabled=sharing_enabled,
        latitude=(float(latitude) if latitude is not None else None),
        longitude=(float(longitude) if longitude is not None else None),
        accuracy_m=(float(accuracy_m) if accuracy_m is not None else None),
        captured_at=captured_at,
        status=status,
        source="browser",
    )
    effective_status = crm_service.agent_presence.effective_status(presence)
    return JSONResponse(
        {
            "ok": True,
            "agent_id": agent_id,
            "effective_status": effective_status.value if effective_status else None,
            "location_sharing_enabled": bool(getattr(presence, "location_sharing_enabled", False)),
            "last_location_at": (
                presence.last_location_at.isoformat() if getattr(presence, "last_location_at", None) else None
            ),
        }
    )


@router.get("/agents/presence/self", response_class=JSONResponse)
async def get_current_agent_presence(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get presence for the current CRM agent (derived from logged-in user)."""
    from app.services.crm.shifts import current_shift_window, resolve_company_timezone
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    agent_id = get_current_agent_id(db, (current_user or {}).get("person_id"))
    if not agent_id:
        return JSONResponse({"ok": True, "agent_id": None, "status": None, "effective_status": None})

    presence = crm_service.agent_presence.get_or_create(db, agent_id)
    effective_status = crm_service.agent_presence.effective_status(presence)

    # Shift-aware work timer: Online + Away count as working time within current shift window.
    tz_name = resolve_company_timezone(db)
    shift = current_shift_window(tz_name=tz_name)
    seconds_by_status = crm_service.agent_presence.seconds_by_status(
        db,
        agent_id=agent_id,
        start_at=shift.start_utc,
        end_at=shift.end_utc,
    )
    work_seconds = float(seconds_by_status.get(AgentPresenceStatus.online.value, 0.0)) + float(
        seconds_by_status.get(AgentPresenceStatus.away.value, 0.0)
    )
    return JSONResponse(
        {
            "ok": True,
            "agent_id": agent_id,
            "status": presence.status.value if presence.status else None,
            "effective_status": effective_status.value if effective_status else None,
            "location_sharing_enabled": bool(getattr(presence, "location_sharing_enabled", False)),
            "last_latitude": getattr(presence, "last_latitude", None),
            "last_longitude": getattr(presence, "last_longitude", None),
            "last_location_accuracy_m": getattr(presence, "last_location_accuracy_m", None),
            "last_location_at": (
                presence.last_location_at.isoformat() if getattr(presence, "last_location_at", None) else None
            ),
            "manual_override_status": (
                presence.manual_override_status.value if getattr(presence, "manual_override_status", None) else None
            ),
            "manual_override_set_at": (
                presence.manual_override_set_at.isoformat()
                if getattr(presence, "manual_override_set_at", None)
                else None
            ),
            "shift": {
                "name": shift.name,
                "timezone": shift.tz,
                "start_at": shift.start_utc.isoformat(),
                "end_at": shift.end_utc.isoformat(),
                "work_seconds": int(work_seconds),
                "break_seconds": int(seconds_by_status.get(AgentPresenceStatus.on_break.value, 0.0)),
            },
        }
    )


@router.post("/agents/presence/override", response_class=JSONResponse)
async def set_current_agent_presence_override(
    request: Request,
    db: Session = Depends(get_db),
):
    """Manually override presence for current agent (on_break/offline) or clear override."""
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    agent_id = get_current_agent_id(db, (current_user or {}).get("person_id"))
    if not agent_id:
        return Response(status_code=204)

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    status = payload.get("status") if isinstance(payload, dict) else None
    status = (str(status).strip().lower() if status is not None else None) or None

    if status in {None, "", "clear", "auto", "online", "available"}:
        crm_service.agent_presence.clear_manual_override(db, agent_id)
        return JSONResponse({"ok": True, "manual_override": None})

    if status not in {"on_break", "offline"}:
        raise HTTPException(status_code=400, detail="Invalid override status")

    crm_service.agent_presence.set_manual_override(db, agent_id, status=status)
    return JSONResponse({"ok": True, "manual_override": status})


@router.get("/agents/presence/live-map", response_class=JSONResponse)
async def list_live_map_presence(
    request: Request,
    stale_after_seconds: int = Query(default=120, ge=30, le=3600),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List active location sharing agents for live map rendering."""
    if not _can_view_live_location_map(request):
        raise HTTPException(status_code=403, detail="Not authorized to view live locations")
    items = crm_service.agent_presence.list_live_locations(
        db,
        stale_after_seconds=stale_after_seconds,
        limit=limit,
    )
    return JSONResponse(jsonable_encoder({"items": items, "count": len(items), "limit": limit, "offset": 0}))


@router.get("/live-map", response_class=HTMLResponse)
async def crm_live_map(
    request: Request,
    db: Session = Depends(get_db),
):
    """Live location map for CRM agents that opted into browser sharing."""
    from app.web.admin import get_current_user, get_sidebar_stats

    if not _can_view_live_location_map(request):
        raise HTTPException(status_code=403, detail="Not authorized to view live locations")
    return templates.TemplateResponse(
        "admin/crm/live-map.html",
        {
            "request": request,
            "active_page": "crm-live-map",
            "active_menu": "crm",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )
