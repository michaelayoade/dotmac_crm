"""Admin service team management web routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.models.auth import UserCredential
from app.models.person import Person
from app.models.service_team import ServiceTeamMemberRole, ServiceTeamType
from app.schemas.service_team import ServiceTeamCreate, ServiceTeamMemberCreate, ServiceTeamUpdate
from app.services.audit_helpers import log_audit_event
from app.services.common import coerce_uuid
from app.services.service_teams import service_team_members, service_teams

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/system/teams", tags=["web-admin-service-teams"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _base_ctx(request: Request, db: Session, **kwargs) -> dict:
    from app.web.admin import get_current_user, get_sidebar_stats

    return {
        "request": request,
        "active_page": "service-teams",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "csrf_token": get_csrf_token(request),
        **kwargs,
    }


@router.get("", response_class=HTMLResponse)
def service_team_list(request: Request, search: str | None = None, db: Session = Depends(get_db)):
    teams = service_teams.list(db, order_by="name", order_dir="asc", limit=100, offset=0)
    designation_groups = service_teams.list_designation_region_groups(db, search=search, limit=500, offset=0)
    search = (search or "").strip()
    if search:
        search_lower = search.lower()
        teams = [
            t
            for t in teams
            if search_lower in (t.name or "").lower()
            or search_lower in (t.region or "").lower()
            or search_lower in (t.team_type.value if t.team_type else "").lower()
        ]
    context = _base_ctx(request, db, teams=teams, designation_groups=designation_groups, search=search)
    return templates.TemplateResponse("admin/system/service_teams/index.html", context)


@router.get("/new", response_class=HTMLResponse)
def service_team_new(request: Request, db: Session = Depends(get_db)):
    context = _base_ctx(
        request,
        db,
        team=None,
        team_types=[t.value for t in ServiceTeamType],
    )
    return templates.TemplateResponse("admin/system/service_teams/form.html", context)


@router.post("/new")
def service_team_create(
    request: Request,
    name: str = Form(...),
    team_type: str = Form(...),
    region: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    payload = ServiceTeamCreate(
        name=name,
        team_type=ServiceTeamType(team_type),
        region=region or None,
    )
    team = service_teams.create(db, payload)

    log_audit_event(
        db=db,
        request=request,
        action="create",
        entity_type="service_team",
        entity_id=str(team.id),
        actor_id=str(current_user.get("person_id")) if current_user else None,
        metadata={"name": team.name},
    )

    return RedirectResponse(url=f"/admin/system/teams/{team.id}", status_code=303)


@router.get("/{team_id}", response_class=HTMLResponse)
def service_team_detail(request: Request, team_id: str, db: Session = Depends(get_db)):
    team = service_teams.get(db, team_id)
    members = service_team_members.list_members(db, team_id)
    active_members = {str(member.person_id) for member in members if member.is_active}
    people = (
        db.query(Person)
        .join(
            UserCredential,
            and_(
                UserCredential.person_id == Person.id,
                UserCredential.is_active.is_(True),
            ),
        )
        .filter(Person.is_active.is_(True))
        .order_by(Person.first_name.asc(), Person.last_name.asc(), Person.email.asc())
        .all()
    )
    people_options = []
    for person in people:
        person_id = str(person.id)
        if person_id in active_members:
            continue
        label = (
            person.display_name
            or f"{(person.first_name or '').strip()} {(person.last_name or '').strip()}".strip()
            or person.email
            or "User"
        )
        people_options.append({"id": person_id, "label": label, "email": person.email})
    context = _base_ctx(
        request,
        db,
        team=team,
        members=members,
        member_roles=[r.value for r in ServiceTeamMemberRole],
        people_options=people_options,
    )
    return templates.TemplateResponse("admin/system/service_teams/detail.html", context)


@router.get("/{team_id}/edit", response_class=HTMLResponse)
def service_team_edit(request: Request, team_id: str, db: Session = Depends(get_db)):
    team = service_teams.get(db, team_id)
    context = _base_ctx(
        request,
        db,
        team=team,
        team_types=[t.value for t in ServiceTeamType],
    )
    return templates.TemplateResponse("admin/system/service_teams/form.html", context)


@router.post("/{team_id}/edit")
def service_team_update(
    request: Request,
    team_id: str,
    name: str = Form(...),
    team_type: str = Form(...),
    region: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    payload = ServiceTeamUpdate(
        name=name,
        team_type=ServiceTeamType(team_type),
        region=region or None,
    )
    service_teams.update(db, team_id, payload)

    log_audit_event(
        db=db,
        request=request,
        action="update",
        entity_type="service_team",
        entity_id=team_id,
        actor_id=str(current_user.get("person_id")) if current_user else None,
    )

    return RedirectResponse(url=f"/admin/system/teams/{team_id}", status_code=303)


@router.post("/{team_id}/members/add")
def service_team_add_member(
    request: Request,
    team_id: str,
    person_id: str = Form(...),
    role: str = Form("member"),
    db: Session = Depends(get_db),
):
    payload = ServiceTeamMemberCreate(
        person_id=coerce_uuid(person_id),
        role=ServiceTeamMemberRole(role),
    )
    service_team_members.add_member(db, team_id, payload)
    return RedirectResponse(url=f"/admin/system/teams/{team_id}", status_code=303)


@router.post("/{team_id}/members/{member_id}/remove")
def service_team_remove_member(request: Request, team_id: str, member_id: str, db: Session = Depends(get_db)):
    service_team_members.remove_member(db, team_id, member_id)
    return RedirectResponse(url=f"/admin/system/teams/{team_id}", status_code=303)
