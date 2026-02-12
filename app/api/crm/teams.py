from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.crm.team import (
    AgentCreate,
    AgentRead,
    AgentTeamCreate,
    AgentTeamRead,
    AgentUpdate,
    RoutingRuleCreate,
    RoutingRuleRead,
    RoutingRuleUpdate,
    TeamChannelCreate,
    TeamChannelRead,
    TeamCreate,
    TeamRead,
    TeamUpdate,
)
from app.services import crm as crm_service

router = APIRouter(prefix="/crm/teams", tags=["crm-teams"])


@router.post("", response_model=TeamRead, status_code=status.HTTP_201_CREATED)
def create_team(payload: TeamCreate, db: Session = Depends(get_db)):
    return crm_service.teams.create(db, payload)


@router.get("/{team_id}", response_model=TeamRead)
def get_team(team_id: str, db: Session = Depends(get_db)):
    return crm_service.teams.get(db, team_id)


@router.get("", response_model=ListResponse[TeamRead])
def list_teams(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return crm_service.teams.list_response(db, is_active, order_by, order_dir, limit, offset)


@router.patch("/{team_id}", response_model=TeamRead)
def update_team(team_id: str, payload: TeamUpdate, db: Session = Depends(get_db)):
    return crm_service.teams.update(db, team_id, payload)


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(team_id: str, db: Session = Depends(get_db)):
    crm_service.teams.delete(db, team_id)


@router.post("/agents", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db)):
    return crm_service.agents.create(db, payload)


@router.get("/agents", response_model=ListResponse[AgentRead])
def list_agents(
    person_id: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return crm_service.agents.list_response(db, person_id, is_active, order_by, order_dir, limit, offset)


@router.patch("/agents/{agent_id}", response_model=AgentRead)
def update_agent(agent_id: str, payload: AgentUpdate, db: Session = Depends(get_db)):
    return crm_service.agents.update(db, agent_id, payload)


@router.post(
    "/agents/{agent_id}/teams",
    response_model=AgentTeamRead,
    status_code=status.HTTP_201_CREATED,
)
def create_agent_team(agent_id: str, payload: AgentTeamCreate, db: Session = Depends(get_db)):
    data = payload.model_copy(update={"agent_id": agent_id})
    return crm_service.agent_teams.create(db, data)


@router.post(
    "/{team_id}/channels",
    response_model=TeamChannelRead,
    status_code=status.HTTP_201_CREATED,
)
def create_team_channel(team_id: str, payload: TeamChannelCreate, db: Session = Depends(get_db)):
    data = payload.model_copy(update={"team_id": team_id})
    return crm_service.team_channels.create(db, data)


@router.get("/{team_id}/channels", response_model=ListResponse[TeamChannelRead])
def list_team_channels(
    team_id: str,
    channel_type: str | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return crm_service.team_channels.list_response(db, team_id, channel_type, order_by, order_dir, limit, offset)


@router.post(
    "/{team_id}/routing-rules",
    response_model=RoutingRuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_routing_rule(team_id: str, payload: RoutingRuleCreate, db: Session = Depends(get_db)):
    data = payload.model_copy(update={"team_id": team_id})
    return crm_service.routing_rules.create(db, data)


@router.get("/{team_id}/routing-rules", response_model=ListResponse[RoutingRuleRead])
def list_routing_rules(
    team_id: str,
    channel_type: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return crm_service.routing_rules.list_response(
        db, team_id, channel_type, is_active, order_by, order_dir, limit, offset
    )


@router.patch("/routing-rules/{rule_id}", response_model=RoutingRuleRead)
def update_routing_rule(rule_id: str, payload: RoutingRuleUpdate, db: Session = Depends(get_db)):
    return crm_service.routing_rules.update(db, rule_id, payload)
