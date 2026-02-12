from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.service_team import (
    ServiceTeamCreate,
    ServiceTeamMemberCreate,
    ServiceTeamMemberRead,
    ServiceTeamMemberUpdate,
    ServiceTeamRead,
    ServiceTeamUpdate,
)
from app.services.response import list_response
from app.services.service_teams import service_team_members, service_teams

router = APIRouter(prefix="/service-teams", tags=["service-teams"])


@router.post("", response_model=ServiceTeamRead, status_code=status.HTTP_201_CREATED)
def create_team(payload: ServiceTeamCreate, db: Session = Depends(get_db)):
    return service_teams.create(db, payload)


@router.get("/{team_id}", response_model=ServiceTeamRead)
def get_team(team_id: str, db: Session = Depends(get_db)):
    return service_teams.get(db, team_id)


@router.get("", response_model=ListResponse[ServiceTeamRead])
def list_teams(
    is_active: bool | None = None,
    search: str | None = None,
    team_type: str | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = service_teams.list(
        db,
        is_active=is_active,
        search=search,
        team_type=team_type,
        order_by=order_by,
        order_dir=order_dir,
        limit=limit,
        offset=offset,
    )
    return list_response(items, limit, offset)


@router.patch("/{team_id}", response_model=ServiceTeamRead)
def update_team(team_id: str, payload: ServiceTeamUpdate, db: Session = Depends(get_db)):
    return service_teams.update(db, team_id, payload)


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(team_id: str, db: Session = Depends(get_db)):
    service_teams.delete(db, team_id)


# ── Member management ────────────────────────────────────────────


@router.post("/{team_id}/members", response_model=ServiceTeamMemberRead, status_code=status.HTTP_201_CREATED)
def add_member(team_id: str, payload: ServiceTeamMemberCreate, db: Session = Depends(get_db)):
    return service_team_members.add_member(db, team_id, payload)


@router.get("/{team_id}/members", response_model=list[ServiceTeamMemberRead])
def list_members(
    team_id: str,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
):
    return service_team_members.list_members(db, team_id, is_active=is_active)


@router.patch("/{team_id}/members/{member_id}", response_model=ServiceTeamMemberRead)
def update_member(
    team_id: str,
    member_id: str,
    payload: ServiceTeamMemberUpdate,
    db: Session = Depends(get_db),
):
    return service_team_members.update_member(db, team_id, member_id, payload)


@router.delete("/{team_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(team_id: str, member_id: str, db: Session = Depends(get_db)):
    service_team_members.remove_member(db, team_id, member_id)
