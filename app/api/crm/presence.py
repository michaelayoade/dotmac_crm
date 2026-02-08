from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.crm.presence import AgentPresenceRead, AgentPresenceUpdate
from app.services import crm as crm_service

router = APIRouter(prefix="/crm/agents", tags=["crm-agent-presence"])


@router.get("/{agent_id}/presence", response_model=AgentPresenceRead)
def get_agent_presence(
    agent_id: str,
    stale_after_minutes: int = Query(default=5, ge=1, le=1440),
    db: Session = Depends(get_db),
):
    presence = crm_service.agent_presence.get_or_create(db, agent_id)
    effective_status = crm_service.agent_presence.effective_status(
        presence, stale_after_minutes=stale_after_minutes
    )
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
    effective_status = crm_service.agent_presence.effective_status(
        presence, stale_after_minutes=stale_after_minutes
    )
    data = AgentPresenceRead.model_validate(presence).model_dump()
    data["effective_status"] = effective_status
    return data


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
