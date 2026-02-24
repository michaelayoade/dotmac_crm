from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.services import reseller_portal as reseller_portal_service
from app.web.reseller.dependencies import require_reseller_portal_context

router = APIRouter(
    prefix="/reseller",
    tags=["web-reseller"],
)
templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _current_user_dict(auth: dict) -> dict:
    person = auth.get("person")
    if not person:
        return {"id": "", "name": "Unknown", "email": "", "initials": "??"}
    name = f"{person.first_name} {person.last_name}".strip()
    parts = [p for p in name.split() if p]
    initials = "??"
    if len(parts) >= 2:
        initials = (parts[0][0] + parts[-1][0]).upper()
    elif parts:
        initials = parts[0][:2].upper()
    return {"id": str(person.id), "name": name, "email": person.email or "", "initials": initials}


@router.get("", response_class=HTMLResponse)
def reseller_root():
    return RedirectResponse(url="/reseller/dashboard", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
def reseller_dashboard(
    request: Request,
    ctx: dict = Depends(require_reseller_portal_context),
    db: Session = Depends(get_db),
):
    reseller_org = ctx["reseller_org"]
    child_orgs = reseller_portal_service.list_child_organizations(db, actor_person_id=ctx["person"].id)
    return templates.TemplateResponse(
        "reseller/dashboard.html",
        {
            "request": request,
            "active_page": "dashboard",
            "current_user": _current_user_dict(ctx),
            "reseller_org": reseller_org,
            "child_orgs": child_orgs,
        },
    )


@router.get("/accounts", response_class=HTMLResponse)
def reseller_accounts(
    request: Request,
    ctx: dict = Depends(require_reseller_portal_context),
    db: Session = Depends(get_db),
):
    reseller_org = ctx["reseller_org"]
    child_orgs = reseller_portal_service.list_child_organizations(db, actor_person_id=ctx["person"].id)
    return templates.TemplateResponse(
        "reseller/accounts.html",
        {
            "request": request,
            "active_page": "accounts",
            "current_user": _current_user_dict(ctx),
            "reseller_org": reseller_org,
            "child_orgs": child_orgs,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/accounts", response_class=HTMLResponse)
def reseller_accounts_create(
    request: Request,
    name: str = Form(...),
    domain: str | None = Form(None),
    ctx: dict = Depends(require_reseller_portal_context),
    db: Session = Depends(get_db),
):
    person = ctx["person"]
    reseller_portal_service.create_child_organization(
        db,
        actor_person_id=person.id,
        name=name,
        domain=domain,
    )
    return RedirectResponse(url="/reseller/accounts", status_code=303)


@router.get("/fiber-map", response_class=HTMLResponse)
def reseller_fiber_map(
    request: Request,
    ctx: dict = Depends(require_reseller_portal_context),
    db: Session = Depends(get_db),
):
    reseller_org = ctx["reseller_org"]
    return templates.TemplateResponse(
        "reseller/fiber_map.html",
        {
            "request": request,
            "active_page": "fiber-map",
            "current_user": _current_user_dict(ctx),
            "reseller_org": reseller_org,
        },
    )


@router.get("/contacts", response_class=HTMLResponse)
def reseller_contacts(
    request: Request,
    ctx: dict = Depends(require_reseller_portal_context),
    db: Session = Depends(get_db),
):
    person = ctx["person"]
    reseller_org = ctx["reseller_org"]
    contacts = reseller_portal_service.list_contacts_for_actor(db, actor_person_id=person.id)
    organizations = reseller_portal_service.list_scope_organizations(db, actor_person_id=person.id)
    return templates.TemplateResponse(
        "reseller/contacts.html",
        {
            "request": request,
            "active_page": "contacts",
            "current_user": _current_user_dict(ctx),
            "reseller_org": reseller_org,
            "contacts": contacts,
            "organizations": organizations,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/contacts", response_class=HTMLResponse)
def reseller_contacts_create(
    request: Request,
    organization_id: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: str | None = Form(None),
    ctx: dict = Depends(require_reseller_portal_context),
    db: Session = Depends(get_db),
):
    person = ctx["person"]
    reseller_portal_service.create_contact(
        db,
        actor_person_id=person.id,
        organization_id=organization_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
    )
    return RedirectResponse(url="/reseller/contacts", status_code=303)


@router.get("/subscribers", response_class=HTMLResponse)
def reseller_subscribers(
    request: Request,
    ctx: dict = Depends(require_reseller_portal_context),
    db: Session = Depends(get_db),
):
    person = ctx["person"]
    reseller_org = ctx["reseller_org"]
    subscribers = reseller_portal_service.list_subscribers_for_actor(db, actor_person_id=person.id)
    organizations = reseller_portal_service.list_scope_organizations(db, actor_person_id=person.id)
    return templates.TemplateResponse(
        "reseller/subscribers.html",
        {
            "request": request,
            "active_page": "subscribers",
            "current_user": _current_user_dict(ctx),
            "reseller_org": reseller_org,
            "subscribers": subscribers,
            "organizations": organizations,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/subscribers", response_class=HTMLResponse)
def reseller_subscribers_create(
    request: Request,
    organization_id: str = Form(...),
    subscriber_number: str = Form(...),
    status: str = Form("active"),
    service_name: str | None = Form(None),
    ctx: dict = Depends(require_reseller_portal_context),
    db: Session = Depends(get_db),
):
    person = ctx["person"]
    reseller_portal_service.create_subscriber(
        db,
        actor_person_id=person.id,
        organization_id=organization_id,
        subscriber_number=subscriber_number,
        status=status,
        service_name=service_name,
    )
    return RedirectResponse(url="/reseller/subscribers", status_code=303)
