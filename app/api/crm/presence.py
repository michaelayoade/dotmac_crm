from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_user_auth
from app.schemas.common import ListResponse
from app.schemas.crm.presence import (
    AgentLiveLocationRead,
    AgentLocationUpdate,
    AgentPresenceRead,
    AgentPresenceUpdate,
)
from app.services import crm as crm_service
from app.services.crm.inbox.agents import get_current_agent_id

router = APIRouter(prefix="/crm/agents", tags=["crm-agent-presence"])


def _can_view_live_map(auth: dict) -> bool:
    roles = {str(role).strip().lower() for role in (auth.get("roles") or [])}
    if "admin" in roles or "manager" in roles:
        return True
    scopes = {str(scope).strip().lower() for scope in (auth.get("scopes") or [])}
    return "crm:location:read" in scopes


def _can_write_agent_location(auth: dict, db: Session, agent_id: str) -> bool:
    roles = {str(role).strip().lower() for role in (auth.get("roles") or [])}
    if "admin" in roles or "manager" in roles:
        return True
    person_id = (auth.get("person_id") or "").strip()
    current_agent_id = get_current_agent_id(db, person_id)
    return bool(current_agent_id and str(current_agent_id) == str(agent_id))


@router.get("/{agent_id}/presence", response_model=AgentPresenceRead)
def get_agent_presence(
    agent_id: str,
    stale_after_minutes: int = Query(default=5, ge=1, le=1440),
    db: Session = Depends(get_db),
):
    presence = crm_service.agent_presence.get_or_create(db, agent_id)
    effective_status = crm_service.agent_presence.effective_status(presence, stale_after_minutes=stale_after_minutes)
    data = AgentPresenceRead.model_validate(presence).model_dump()
    data["effective_status"] = effective_status
    return data


@router.post("/{agent_id}/presence/location", response_model=AgentPresenceRead, status_code=status.HTTP_200_OK)
def upsert_agent_location_presence(
    agent_id: str,
    payload: AgentLocationUpdate,
    stale_after_minutes: int = Query(default=5, ge=1, le=1440),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_user_auth),
):
    if not _can_write_agent_location(auth, db, agent_id):
        raise HTTPException(status_code=403, detail="Not authorized to update this agent location")
    presence = crm_service.agent_presence.upsert_location(
        db,
        agent_id=agent_id,
        sharing_enabled=payload.sharing_enabled,
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy_m=payload.accuracy_m,
        captured_at=payload.captured_at,
        status=payload.status,
        source="browser",
    )
    effective_status = crm_service.agent_presence.effective_status(presence, stale_after_minutes=stale_after_minutes)
    data = AgentPresenceRead.model_validate(presence).model_dump()
    data["effective_status"] = effective_status
    return data


@router.post("/{agent_id}/presence", response_model=AgentPresenceRead, status_code=status.HTTP_200_OK)
def upsert_agent_presence(
    agent_id: str,
    payload: AgentPresenceUpdate,
    stale_after_minutes: int = Query(default=5, ge=1, le=1440),
    db: Session = Depends(get_db),
):
    presence = crm_service.agent_presence.upsert(db, agent_id, status=payload.status)
    effective_status = crm_service.agent_presence.effective_status(presence, stale_after_minutes=stale_after_minutes)
    data = AgentPresenceRead.model_validate(presence).model_dump()
    data["effective_status"] = effective_status
    return data


@router.get("/presence/live-map", response_model=ListResponse[AgentLiveLocationRead])
def list_live_map_locations(
    stale_after_seconds: int = Query(default=120, ge=30, le=3600),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_user_auth),
):
    if not _can_view_live_map(auth):
        raise HTTPException(status_code=403, detail="Not authorized to view live locations")
    items = crm_service.agent_presence.list_live_locations(
        db,
        stale_after_seconds=stale_after_seconds,
        limit=limit,
    )
    return {"items": items, "count": len(items), "limit": limit, "offset": 0}


@router.get("/presence", response_model=ListResponse[AgentPresenceRead])
def list_agent_presence(
    status: str | None = None,
    stale_after_minutes: int = Query(default=5, ge=1, le=1440),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = crm_service.agent_presence.list(
        db,
        status=status,
        limit=limit,
        offset=offset,
    )
    result_items = []
    for presence in items:
        effective_status = crm_service.agent_presence.effective_status(
            presence, stale_after_minutes=stale_after_minutes
        )
        data = AgentPresenceRead.model_validate(presence).model_dump()
        data["effective_status"] = effective_status
        result_items.append(data)
    return {"items": result_items, "count": len(result_items), "limit": limit, "offset": offset}
