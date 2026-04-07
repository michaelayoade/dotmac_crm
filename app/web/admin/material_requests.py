"""Admin material request management web routes."""

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.models.auth import UserCredential
from app.models.inventory import InventoryLocation
from app.models.material_request import MaterialRequestPriority
from app.models.person import Person
from app.models.projects import Project
from app.models.tickets import Ticket
from app.schemas.material_request import (
    MaterialRequestCreate,
    MaterialRequestItemCreate,
    MaterialRequestUpdate,
)
from app.services.audit_helpers import log_audit_event
from app.services.auth_dependencies import require_permission
from app.services.common import coerce_uuid
from app.services.material_requests import material_requests
from app.web.templates import Jinja2Templates

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/operations/material-requests", tags=["web-admin-material-requests"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _base_ctx(request: Request, db: Session, **kwargs) -> dict:
    from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats

    return {
        "request": request,
        "active_page": "material-requests",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "csrf_token": get_csrf_token(request),
        **kwargs,
    }


def _resolve_ticket_id(db: Session, value: str | None):
    raw = (value or "").strip()
    if not raw:
        return None
    ticket = db.query(Ticket).filter(Ticket.number == raw).first()
    if ticket:
        return ticket.id
    return coerce_uuid(raw)


def _resolve_project_id(db: Session, value: str | None):
    raw = (value or "").strip()
    if not raw:
        return None
    project = db.query(Project).filter(Project.number == raw).first()
    if project:
        return project.id
    return coerce_uuid(raw)


def _resolve_warehouse_id(value: str | None):
    raw = (value or "").strip()
    if not raw:
        return None
    return coerce_uuid(raw)


def _resolve_date(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _warehouse_choices(db: Session) -> list[InventoryLocation]:
    return (
        db.query(InventoryLocation)
        .filter(InventoryLocation.is_active.is_(True))
        .order_by(InventoryLocation.name.asc())
        .all()
    )


def _collector_choices(db: Session) -> list[Person]:
    has_active_credential = (
        db.query(UserCredential.id)
        .filter(
            UserCredential.person_id == Person.id,
            UserCredential.is_active.is_(True),
        )
        .exists()
    )
    return (
        db.query(Person)
        .filter(Person.is_active.is_(True))
        .filter(has_active_credential)
        .order_by(Person.first_name.asc(), Person.last_name.asc(), Person.email.asc())
        .limit(500)
        .all()
    )


@router.get("", response_class=HTMLResponse)
def material_request_list(
    request: Request,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    ticket_id: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    selected_status = (status or "").strip().lower() or None
    if selected_status == "all":
        selected_status = None

    selected_date_from = _resolve_date(date_from)
    selected_date_to = _resolve_date(date_to)
    if not (selected_date_from and selected_date_to):
        selected_date_from = None
        selected_date_to = None

    items = material_requests.list(
        db,
        status=selected_status,
        created_from=selected_date_from,
        created_to=selected_date_to,
        ticket_id=ticket_id,
        project_id=project_id,
        order_by="created_at",
        order_dir="desc",
        limit=100,
        offset=0,
    )

    context = _base_ctx(
        request,
        db,
        items=items,
        filter_status=selected_status or "",
        filter_date_from=date_from or "",
        filter_date_to=date_to or "",
    )
    return templates.TemplateResponse("admin/material_requests/index.html", context)


@router.get("/new", response_class=HTMLResponse)
def material_request_new(
    request: Request,
    ticket_id: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    context = _base_ctx(
        request,
        db,
        mr=None,
        ticket_id=ticket_id,
        project_id=project_id,
        priorities=[p.value for p in MaterialRequestPriority],
        warehouses=_warehouse_choices(db),
    )
    return templates.TemplateResponse("admin/material_requests/form.html", context)


@router.post("/new")
def material_request_create(
    request: Request,
    ticket_id: str | None = Form(None),
    project_id: str | None = Form(None),
    notes: str | None = Form(None),
    priority: str = Form("medium"),
    source_location_id: str | None = Form(None),
    destination_location_id: str | None = Form(None),
    item_id: list[str] = Form(default=[]),
    quantity: list[int] = Form(default=[]),
    item_notes: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    person_id = current_user.get("person_id") if current_user else None

    if not person_id:
        return RedirectResponse(url="/admin/operations/material-requests", status_code=303)

    items: list[MaterialRequestItemCreate] = []
    if item_id:
        for idx, item_value in enumerate(item_id):
            if not item_value:
                continue
            qty = quantity[idx] if idx < len(quantity) else 1
            note = item_notes[idx] if idx < len(item_notes) else None
            if qty is None or qty < 1:
                continue
            items.append(
                MaterialRequestItemCreate(
                    item_id=coerce_uuid(item_value),
                    quantity=qty,
                    notes=note,
                )
            )

    payload = MaterialRequestCreate(
        ticket_id=_resolve_ticket_id(db, ticket_id),
        project_id=_resolve_project_id(db, project_id),
        requested_by_person_id=person_id,
        priority=MaterialRequestPriority(priority) if priority else MaterialRequestPriority.medium,
        notes=notes,
        source_location_id=_resolve_warehouse_id(source_location_id),
        destination_location_id=_resolve_warehouse_id(destination_location_id),
        items=items or None,
    )
    mr = material_requests.create(db, payload)

    log_audit_event(
        db=db,
        request=request,
        action="create",
        entity_type="material_request",
        entity_id=str(mr.id),
        actor_id=str(person_id),
        metadata={"number": mr.number, "status": mr.status.value},
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr.id}", status_code=303)


@router.get("/{mr_id}/edit", response_class=HTMLResponse)
def material_request_edit(request: Request, mr_id: str, db: Session = Depends(get_db)):
    mr = material_requests.get(db, mr_id)
    if mr.status and mr.status.value != "draft":
        return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)
    context = _base_ctx(
        request,
        db,
        mr=mr,
        priorities=[p.value for p in MaterialRequestPriority],
        ticket_id=mr.ticket.number if mr.ticket and mr.ticket.number else mr.ticket_id,
        project_id=mr.project.number if mr.project and mr.project.number else mr.project_id,
        warehouses=_warehouse_choices(db),
    )
    return templates.TemplateResponse("admin/material_requests/form.html", context)


@router.post("/{mr_id}/edit")
def material_request_update(
    request: Request,
    mr_id: str,
    ticket_id: str | None = Form(None),
    project_id: str | None = Form(None),
    notes: str | None = Form(None),
    priority: str | None = Form(None),
    source_location_id: str | None = Form(None),
    destination_location_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    mr = material_requests.get(db, mr_id)
    if mr.status and mr.status.value != "draft":
        return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)

    payload = MaterialRequestUpdate(
        ticket_id=_resolve_ticket_id(db, ticket_id),
        project_id=_resolve_project_id(db, project_id),
        priority=MaterialRequestPriority(priority) if priority else None,
        notes=notes,
        source_location_id=_resolve_warehouse_id(source_location_id),
        destination_location_id=_resolve_warehouse_id(destination_location_id),
    )
    material_requests.update(db, mr_id, payload)

    log_audit_event(
        db=db,
        request=request,
        action="update",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(current_user.get("person_id")) if current_user else None,
        metadata={"status": mr.status.value if mr.status else "draft"},
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.get("/{mr_id}", response_class=HTMLResponse)
def material_request_detail(request: Request, mr_id: str, db: Session = Depends(get_db)):
    mr = material_requests.get(db, mr_id)
    context = _base_ctx(
        request,
        db,
        mr=mr,
        warehouses=_warehouse_choices(db),
        collectors=_collector_choices(db),
    )
    return templates.TemplateResponse("admin/material_requests/detail.html", context)


@router.post("/{mr_id}/submit")
def material_request_submit(request: Request, mr_id: str, db: Session = Depends(get_db)):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    material_requests.submit(db, mr_id)

    log_audit_event(
        db=db,
        request=request,
        action="submit",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(current_user.get("person_id")) if current_user else None,
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/approve", dependencies=[Depends(require_permission("inventory:write"))])
def material_request_approve(
    request: Request,
    mr_id: str,
    source_location_id: str | None = Form(None),
    destination_location_id: str | None = Form(None),
    collected_by_person_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    person_id = current_user.get("person_id") if current_user else None

    material_requests.approve(
        db,
        mr_id,
        str(person_id) if person_id else "",
        source_location_id=source_location_id,
        destination_location_id=destination_location_id,
        collected_by_person_id=collected_by_person_id,
    )

    log_audit_event(
        db=db,
        request=request,
        action="approve",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(person_id) if person_id else None,
        metadata={"collected_by_person_id": collected_by_person_id} if collected_by_person_id else None,
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/reject")
def material_request_reject(request: Request, mr_id: str, reason: str = Form(""), db: Session = Depends(get_db)):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    person_id = current_user.get("person_id") if current_user else None

    material_requests.reject(db, mr_id, str(person_id) if person_id else "", reason)

    log_audit_event(
        db=db,
        request=request,
        action="reject",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(person_id) if person_id else None,
        metadata={"reason": reason},
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/cancel")
def material_request_cancel(request: Request, mr_id: str, db: Session = Depends(get_db)):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    material_requests.cancel(db, mr_id)

    log_audit_event(
        db=db,
        request=request,
        action="cancel",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(current_user.get("person_id")) if current_user else None,
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/items/add")
def material_request_add_item(
    request: Request,
    mr_id: str,
    item_id: str = Form(...),
    quantity: int = Form(...),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    payload = MaterialRequestItemCreate(item_id=coerce_uuid(item_id), quantity=quantity, notes=notes)
    material_requests.add_item(db, mr_id, payload)
    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/items/{item_id}/remove")
def material_request_remove_item(request: Request, mr_id: str, item_id: str, db: Session = Depends(get_db)):
    material_requests.remove_item(db, mr_id, item_id)
    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)
