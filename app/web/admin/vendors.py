"""Admin vendor portal web routes."""

import os
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import SessionLocal
from app.models.auth import AuthProvider, UserCredential
from app.models.person import Person, PersonStatus
from app.models.projects import Project
from app.models.rbac import PersonRole, Role
from app.models.vendor import InstallationProject, ProjectQuoteStatus, Vendor, VendorUser
from app.schemas.auth import UserCredentialCreate
from app.schemas.person import PersonCreate
from app.schemas.rbac import PersonRoleCreate
from app.schemas.vendor import VendorCreate, VendorUpdate
from app.services import auth as auth_service
from app.services import person as person_service
from app.services import rbac as rbac_service
from app.services import vendor as vendor_service
from app.services.audit_helpers import recent_activity_for_paths
from app.services.auth_flow import hash_password
from app.services.common import coerce_uuid

templates = Jinja2Templates(directory="templates")


def _form_str(value: object | None) -> str:
    return value if isinstance(value, str) else ""


def _form_str_opt(value: object | None) -> str | None:
    value_str = _form_str(value).strip()
    if not value_str:
        return None
    if value_str.lower() in {"none", "null"}:
        return None
    return value_str


router = APIRouter(prefix="/vendors", tags=["web-admin-vendors"])
_DEFAULT_VENDOR_ROLE = "vendors"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _base_context(request: Request, db: Session, active_page: str):
    from app.web.admin import get_current_user, get_sidebar_stats

    return {
        "request": request,
        "active_page": active_page,
        "active_menu": "vendors",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
    }


def _current_person_id(request: Request) -> str | None:
    from app.web.admin import get_current_user

    current_user = get_current_user(request) or {}
    person_id = current_user.get("person_id")
    if not person_id:
        return None
    return str(person_id)


def _is_admin_user(request: Request) -> bool:
    from app.web.admin import get_current_user

    current_user = get_current_user(request) or {}
    roles = current_user.get("roles") if isinstance(current_user, dict) else []
    if not isinstance(roles, list):
        return False
    role_names = {str(role).strip().lower() for role in roles if role}
    return "admin" in role_names or "superadmin" in role_names


def _create_person_credential(
    db: Session,
    first_name: str,
    last_name: str,
    email: str,
    username: str,
    password: str,
):
    person_payload = PersonCreate(
        first_name=first_name,
        last_name=last_name,
        display_name=f"{first_name} {last_name}".strip(),
        email=email,
        status="active",
        is_active=True,
    )
    person = person_service.people.create(db=db, payload=person_payload)
    credential_payload = UserCredentialCreate(
        person_id=person.id,
        provider=AuthProvider.local,
        username=username,
        password_hash=hash_password(password),
    )
    auth_service.user_credentials.create(db=db, payload=credential_payload)
    return person


def _assign_role_by_name(db: Session, person_id: str, role_name: str) -> None:
    if not role_name:
        return
    role = db.query(Role).filter(Role.name.ilike(role_name)).first()
    if not role:
        return
    existing = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == coerce_uuid(person_id))
        .filter(PersonRole.role_id == role.id)
        .first()
    )
    if existing:
        return
    rbac_service.person_roles.create(
        db,
        PersonRoleCreate(person_id=coerce_uuid(person_id), role_id=role.id),
    )


@router.get("", response_class=HTMLResponse)
def vendors_list(
    request: Request,
    status: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    current_status = (status or "active").lower()
    is_active = True
    if current_status == "inactive":
        is_active = False
    vendors = vendor_service.vendors.list(
        db=db,
        is_active=is_active,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    # Apply client-side search filter
    search_term = (search or "").strip().lower()
    if search_term:
        vendors = [
            v
            for v in vendors
            if search_term in (v.name or "").lower()
            or search_term in (v.contact_email or "").lower()
            or search_term in (v.contact_phone or "").lower()
        ]
    # Stats (unfiltered counts)
    all_active = vendor_service.vendors.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=10000,
        offset=0,
    )
    all_inactive = vendor_service.vendors.list(
        db=db,
        is_active=False,
        order_by="name",
        order_dir="asc",
        limit=10000,
        offset=0,
    )
    vendor_stats = {
        "total": len(all_active) + len(all_inactive),
        "active": len(all_active),
        "inactive": len(all_inactive),
    }
    recent_activities = recent_activity_for_paths(db, ["/admin/vendors"])
    context = _base_context(request, db, active_page="vendors")
    context.update(
        {
            "vendors": vendors,
            "current_status": current_status,
            "search": search or "",
            "vendor_stats": vendor_stats,
            "recent_activities": recent_activities,
        }
    )
    return templates.TemplateResponse("admin/vendors/index.html", context)


@router.get("/new", response_class=HTMLResponse)
def vendor_new(request: Request, db: Session = Depends(get_db)):
    context = _base_context(request, db, active_page="vendors")
    roles = rbac_service.roles.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    context.update({"vendor": None, "action_url": "/admin/vendors", "roles": roles})
    return templates.TemplateResponse("admin/vendors/vendor_form.html", context)


@router.get("/{vendor_id}/edit", response_class=HTMLResponse)
def vendor_edit(vendor_id: str, request: Request, db: Session = Depends(get_db)):
    vendor = vendor_service.vendors.get(db=db, vendor_id=vendor_id)
    context = _base_context(request, db, active_page="vendors")
    context.update(
        {
            "vendor": vendor,
            "action_url": f"/admin/vendors/{vendor.id}",
        }
    )
    return templates.TemplateResponse("admin/vendors/vendor_form.html", context)


@router.post("", response_class=HTMLResponse)
async def vendor_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    create_user = bool(form.get("create_user")) or any(
        form.get(field)
        for field in (
            "user_first_name",
            "user_last_name",
            "user_email",
            "user_username",
            "user_password",
        )
    )
    is_active = bool(form.get("is_active"))
    payload: dict[str, str | None] = {
        "name": _form_str(form.get("name")).strip(),
        "code": _form_str_opt(form.get("code")),
        "contact_name": _form_str_opt(form.get("contact_name")),
        "contact_email": _form_str_opt(form.get("contact_email")),
        "contact_phone": _form_str_opt(form.get("contact_phone")),
        "license_number": _form_str_opt(form.get("license_number")),
        "service_area": _form_str_opt(form.get("service_area")),
        "notes": _form_str_opt(form.get("notes")),
        "erp_id": _form_str_opt(form.get("erp_id")),
    }
    user_payload: dict[str, str | None] | None = None
    if create_user:
        user_payload = {
            "first_name": _form_str(form.get("user_first_name")).strip(),
            "last_name": _form_str(form.get("user_last_name")).strip(),
            "email": _form_str(form.get("user_email")).strip(),
            "username": _form_str(form.get("user_username")).strip(),
            "password": _form_str(form.get("user_password")).strip(),
            "role": _form_str_opt(form.get("user_role")),
        }
        missing = [key for key, value in user_payload.items() if key != "role" and not value]
        if missing:
            context = _base_context(request, db, active_page="vendors")
            roles = rbac_service.roles.list(
                db=db,
                is_active=True,
                order_by="name",
                order_dir="asc",
                limit=500,
                offset=0,
            )
            context.update(
                {
                    "vendor": payload,
                    "action_url": "/admin/vendors",
                    "roles": roles,
                    "error": "Provide all user fields to create a login.",
                }
            )
            return templates.TemplateResponse("admin/vendors/vendor_form.html", context, status_code=400)
    try:
        code = payload.get("code") if isinstance(payload.get("code"), str) else None
        contact_name = payload.get("contact_name") if isinstance(payload.get("contact_name"), str) else None
        contact_email = payload.get("contact_email") if isinstance(payload.get("contact_email"), str) else None
        contact_phone = payload.get("contact_phone") if isinstance(payload.get("contact_phone"), str) else None
        license_number = payload.get("license_number") if isinstance(payload.get("license_number"), str) else None
        service_area = payload.get("service_area") if isinstance(payload.get("service_area"), str) else None
        notes = payload.get("notes") if isinstance(payload.get("notes"), str) else None
        erp_id = payload.get("erp_id") if isinstance(payload.get("erp_id"), str) else None
        data = VendorCreate(
            name=str(payload.get("name") or "").strip(),
            code=code,
            contact_name=contact_name,
            contact_email=contact_email,
            contact_phone=contact_phone,
            license_number=license_number,
            service_area=service_area,
            notes=notes,
            is_active=is_active,
            erp_id=erp_id,
        )
    except ValidationError as exc:
        context = _base_context(request, db, active_page="vendors")
        roles = rbac_service.roles.list(
            db=db,
            is_active=True,
            order_by="name",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        context.update(
            {
                "vendor": payload,
                "action_url": "/admin/vendors",
                "roles": roles,
                "error": exc.errors()[0].get("msg", "Invalid vendor details."),
            }
        )
        return templates.TemplateResponse("admin/vendors/vendor_form.html", context, status_code=400)
    vendor = vendor_service.vendors.create(db=db, payload=data)
    if user_payload:
        try:
            role_name = user_payload["role"] or _DEFAULT_VENDOR_ROLE
            first_name = user_payload["first_name"] or ""
            last_name = user_payload["last_name"] or ""
            email = user_payload["email"] or ""
            username = user_payload["username"] or ""
            password = user_payload["password"] or ""
            person = db.query(Person).filter(Person.email == email).first()
            if person:
                person.is_active = True
                person.status = PersonStatus.active
                if not person.first_name:
                    person.first_name = first_name
                if not person.last_name:
                    person.last_name = last_name
                if not person.display_name:
                    person.display_name = f"{first_name} {last_name}".strip()
                credential = (
                    db.query(UserCredential)
                    .filter(UserCredential.person_id == person.id)
                    .filter(UserCredential.provider == AuthProvider.local)
                    .first()
                )
                if credential:
                    credential.username = username
                    credential.password_hash = hash_password(password)
                    credential.is_active = True
                    credential.failed_login_attempts = 0
                    credential.locked_until = None
                    credential.must_change_password = False
                else:
                    credential_payload = UserCredentialCreate(
                        person_id=person.id,
                        provider=AuthProvider.local,
                        username=username,
                        password_hash=hash_password(password),
                    )
                    auth_service.user_credentials.create(db=db, payload=credential_payload)
            else:
                person = _create_person_credential(
                    db=db,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    username=username,
                    password=password,
                )
            _assign_role_by_name(db, str(person.id), role_name)
            link = VendorUser(
                vendor_id=vendor.id,
                person_id=person.id,
                role=role_name,
                is_active=True,
            )
            db.add(link)
            db.commit()
        except Exception as exc:
            context = _base_context(request, db, active_page="vendors")
            roles = rbac_service.roles.list(
                db=db,
                is_active=True,
                order_by="name",
                order_dir="asc",
                limit=500,
                offset=0,
            )
            context.update(
                {
                    "vendor": payload,
                    "action_url": "/admin/vendors",
                    "roles": roles,
                    "error": str(exc) or "Unable to create login user.",
                }
            )
            return templates.TemplateResponse("admin/vendors/vendor_form.html", context, status_code=400)
    return RedirectResponse(url="/admin/vendors", status_code=303)


@router.post("/{vendor_id}", response_class=HTMLResponse)
async def vendor_update(vendor_id: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    is_active = bool(form.get("is_active"))
    payload: dict[str, str | None] = {
        "name": _form_str(form.get("name")).strip(),
        "code": _form_str_opt(form.get("code")),
        "contact_name": _form_str_opt(form.get("contact_name")),
        "contact_email": _form_str_opt(form.get("contact_email")),
        "contact_phone": _form_str_opt(form.get("contact_phone")),
        "license_number": _form_str_opt(form.get("license_number")),
        "service_area": _form_str_opt(form.get("service_area")),
        "notes": _form_str_opt(form.get("notes")),
        "erp_id": _form_str_opt(form.get("erp_id")),
    }
    try:
        update_code = payload.get("code") if isinstance(payload.get("code"), str) else None
        update_contact_name = payload.get("contact_name") if isinstance(payload.get("contact_name"), str) else None
        update_contact_email = payload.get("contact_email") if isinstance(payload.get("contact_email"), str) else None
        update_contact_phone = payload.get("contact_phone") if isinstance(payload.get("contact_phone"), str) else None
        update_license_number = (
            payload.get("license_number") if isinstance(payload.get("license_number"), str) else None
        )
        update_service_area = payload.get("service_area") if isinstance(payload.get("service_area"), str) else None
        update_notes = payload.get("notes") if isinstance(payload.get("notes"), str) else None
        update_erp_id = payload.get("erp_id") if isinstance(payload.get("erp_id"), str) else None
        data = VendorUpdate(
            name=payload.get("name") if isinstance(payload.get("name"), str) else None,
            code=update_code,
            contact_name=update_contact_name,
            contact_email=update_contact_email,
            contact_phone=update_contact_phone,
            license_number=update_license_number,
            service_area=update_service_area,
            notes=update_notes,
            is_active=is_active,
            erp_id=update_erp_id,
        )
    except ValidationError as exc:
        context = _base_context(request, db, active_page="vendors")
        payload.update({"id": vendor_id})
        context.update(
            {
                "vendor": payload,
                "action_url": f"/admin/vendors/{vendor_id}",
                "error": exc.errors()[0].get("msg", "Invalid vendor details."),
            }
        )
        return templates.TemplateResponse("admin/vendors/vendor_form.html", context, status_code=400)
    try:
        vendor_service.vendors.update(db=db, vendor_id=vendor_id, payload=data)
    except Exception as exc:
        context = _base_context(request, db, active_page="vendors")
        payload.update({"id": vendor_id})
        context.update(
            {
                "vendor": payload,
                "action_url": f"/admin/vendors/{vendor_id}",
                "error": str(exc) or "Unable to update vendor.",
            }
        )
        return templates.TemplateResponse("admin/vendors/vendor_form.html", context, status_code=400)
    return RedirectResponse(url="/admin/vendors", status_code=303)


@router.post("/{vendor_id}/delete", response_class=HTMLResponse)
def vendor_delete(vendor_id: str, db: Session = Depends(get_db)):
    vendor = vendor_service.vendors.get(db=db, vendor_id=vendor_id)
    if vendor.is_active:
        raise HTTPException(status_code=409, detail="Deactivate vendor before deleting.")
    try:
        db.query(VendorUser).filter(VendorUser.vendor_id == vendor.id).delete(synchronize_session=False)
        db.delete(vendor)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Vendor has linked records; remove them before deleting.",
        )
    return RedirectResponse(url="/admin/vendors", status_code=303)


@router.get("/projects", response_class=HTMLResponse)
def vendor_projects_list(request: Request, db: Session = Depends(get_db)):
    projects = vendor_service.installation_projects.list(
        db=db,
        status=None,
        vendor_id=None,
        subscriber_id=None,
        project_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    vendors = vendor_service.vendors.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    vendor_names = {str(vendor.id): vendor.name for vendor in vendors}
    context = _base_context(request, db, active_page="vendor-projects")
    context.update({"projects": projects, "vendor_names": vendor_names})
    return templates.TemplateResponse("admin/vendors/projects/index.html", context)


@router.get("/quotes", response_class=HTMLResponse)
def vendor_quotes_list(
    request: Request,
    db: Session = Depends(get_db),
    quote_action: str | None = None,
    quote_error_detail: str | None = None,
):
    quotes = vendor_service.project_quotes.list(
        db=db,
        project_id=None,
        vendor_id=None,
        status=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    vendor_ids = {quote.vendor_id for quote in quotes if quote.vendor_id}
    installation_project_ids = {quote.project_id for quote in quotes if quote.project_id}
    vendor_labels: dict[object, str] = {}
    project_labels: dict[object, str] = {}
    if vendor_ids:
        vendor_rows = db.query(Vendor.id, Vendor.name).filter(Vendor.id.in_(vendor_ids)).all()
        vendor_labels = {vendor_id: name for vendor_id, name in vendor_rows}
    if installation_project_ids:
        project_rows = (
            db.query(InstallationProject.id, Project.name, Project.code)
            .join(Project, InstallationProject.project_id == Project.id)
            .filter(InstallationProject.id.in_(installation_project_ids))
            .all()
        )
        project_labels = {
            installation_project_id: f"{project_name} ({project_code})" if project_code else project_name
            for installation_project_id, project_name, project_code in project_rows
        }
    context = _base_context(request, db, active_page="vendor-quotes")
    context.update(
        {
            "quotes": quotes,
            "project_labels": project_labels,
            "vendor_labels": vendor_labels,
            "quote_action": quote_action,
            "quote_error_detail": quote_error_detail,
        }
    )
    return templates.TemplateResponse("admin/vendors/quotes/review.html", context)


@router.post("/quotes/{quote_id}/approve", response_class=HTMLResponse)
def vendor_quote_approve(
    quote_id: str,
    request: Request,
    review_notes: str | None = Form(None),
    override_threshold: str | None = Form(None),
    db: Session = Depends(get_db),
):
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing reviewer identity.", safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?quote_error_detail={detail}", status_code=303)

    quote = vendor_service.project_quotes.get(db, quote_id)
    if quote.status not in {ProjectQuoteStatus.submitted, ProjectQuoteStatus.under_review}:
        detail = urlquote("Only submitted quotes can be approved.", safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?quote_error_detail={detail}", status_code=303)

    try:
        override_approval_threshold = bool(override_threshold) or _is_admin_user(request)
        vendor_service.project_quotes.approve(
            db,
            quote_id=quote_id,
            reviewer_person_id=reviewer_person_id,
            review_notes=(review_notes or "").strip() or None,
            override=override_approval_threshold,
        )
    except HTTPException as exc:
        detail = urlquote(str(exc.detail or "Failed to approve quote."), safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?quote_error_detail={detail}", status_code=303)

    return RedirectResponse(url="/admin/vendors/quotes?quote_action=approved", status_code=303)


@router.post("/quotes/{quote_id}/reject", response_class=HTMLResponse)
def vendor_quote_reject(
    quote_id: str,
    request: Request,
    review_notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing reviewer identity.", safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?quote_error_detail={detail}", status_code=303)

    quote = vendor_service.project_quotes.get(db, quote_id)
    if quote.status not in {ProjectQuoteStatus.submitted, ProjectQuoteStatus.under_review}:
        detail = urlquote("Only submitted quotes can be rejected.", safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?quote_error_detail={detail}", status_code=303)

    try:
        vendor_service.project_quotes.reject(
            db,
            quote_id=quote_id,
            reviewer_person_id=reviewer_person_id,
            review_notes=(review_notes or "").strip() or None,
        )
    except HTTPException as exc:
        detail = urlquote(str(exc.detail or "Failed to reject quote."), safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?quote_error_detail={detail}", status_code=303)

    return RedirectResponse(url="/admin/vendors/quotes?quote_action=rejected", status_code=303)


@router.get("/as-built", response_class=HTMLResponse)
def vendor_as_built_list(request: Request, db: Session = Depends(get_db)):
    as_built_routes = vendor_service.as_built_routes.list(
        db=db,
        project_id=None,
        status=None,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    context = _base_context(request, db, active_page="vendor-as-built")
    context.update({"as_built_routes": as_built_routes})
    return templates.TemplateResponse("admin/vendors/as-built/review.html", context)


@router.get("/as-built/{as_built_id}/report")
def vendor_as_built_report(as_built_id: str, db: Session = Depends(get_db)):
    as_built = vendor_service.as_built_routes.get(db=db, as_built_id=as_built_id)
    if not as_built or not as_built.report_file_path:
        return HTMLResponse(content="Report not found", status_code=404)
    if not os.path.exists(as_built.report_file_path):
        return HTMLResponse(content="Report file missing", status_code=404)
    filename = as_built.report_file_name or "as-built-report.pdf"
    media_type = "application/pdf" if filename.endswith(".pdf") else "text/html"
    return FileResponse(
        path=as_built.report_file_path,
        filename=filename,
        media_type=media_type,
    )


@router.get("/{vendor_id}", response_class=HTMLResponse)
def vendor_detail(vendor_id: str, request: Request, db: Session = Depends(get_db)):
    vendor = (
        db.query(Vendor)
        .options(selectinload(Vendor.users).selectinload(VendorUser.person))
        .filter(Vendor.id == coerce_uuid(vendor_id))
        .first()
    )
    if not vendor:
        return RedirectResponse(url="/admin/vendors", status_code=303)
    people = person_service.people.list(
        db=db,
        email=None,
        status=None,
        party_status=None,
        organization_id=None,
        is_active=True,
        order_by="last_name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    roles = rbac_service.roles.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    context = _base_context(request, db, active_page="vendors")
    context.update(
        {
            "vendor": vendor,
            "vendor_users": vendor.users,
            "people": people,
            "roles": roles,
        }
    )
    return templates.TemplateResponse("admin/vendors/detail.html", context)


@router.post("/{vendor_id}/users/link", response_class=HTMLResponse)
async def vendor_user_link(vendor_id: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    person_id = _form_str(form.get("person_id")).strip()
    role = _form_str(form.get("role")).strip() or _DEFAULT_VENDOR_ROLE
    if not person_id:
        return RedirectResponse(url=f"/admin/vendors/{vendor_id}", status_code=303)
    existing = (
        db.query(VendorUser)
        .filter(VendorUser.vendor_id == coerce_uuid(vendor_id))
        .filter(VendorUser.person_id == coerce_uuid(person_id))
        .first()
    )
    if existing:
        return RedirectResponse(url=f"/admin/vendors/{vendor_id}", status_code=303)
    _assign_role_by_name(db, person_id, role)
    link = VendorUser(
        vendor_id=coerce_uuid(vendor_id),
        person_id=coerce_uuid(person_id),
        role=role,
        is_active=True,
    )
    db.add(link)
    db.commit()
    return RedirectResponse(url=f"/admin/vendors/{vendor_id}", status_code=303)


@router.post("/{vendor_id}/users/create", response_class=HTMLResponse)
async def vendor_user_create(vendor_id: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    fields = {
        "first_name": _form_str(form.get("first_name")).strip(),
        "last_name": _form_str(form.get("last_name")).strip(),
        "email": _form_str(form.get("email")).strip(),
        "username": _form_str(form.get("username")).strip(),
        "password": _form_str(form.get("password")).strip(),
        "role": _form_str(form.get("role")).strip() or _DEFAULT_VENDOR_ROLE,
    }
    if not all([fields["first_name"], fields["last_name"], fields["email"], fields["username"], fields["password"]]):
        context = _base_context(request, db, active_page="vendors")
        vendor = db.get(Vendor, coerce_uuid(vendor_id))
        people = person_service.people.list(
            db=db,
            email=None,
            status=None,
            party_status=None,
            organization_id=None,
            is_active=True,
            order_by="last_name",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        roles = rbac_service.roles.list(
            db=db,
            is_active=True,
            order_by="name",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        context.update(
            {
                "vendor": vendor,
                "vendor_users": vendor.users if vendor else [],
                "people": people,
                "roles": roles,
                "error": "All user fields are required to create a login.",
            }
        )
        return templates.TemplateResponse("admin/vendors/detail.html", context, status_code=400)
    try:
        person = _create_person_credential(
            db=db,
            first_name=fields["first_name"],
            last_name=fields["last_name"],
            email=fields["email"],
            username=fields["username"],
            password=fields["password"],
        )
        _assign_role_by_name(db, str(person.id), fields["role"])
        link = VendorUser(
            vendor_id=coerce_uuid(vendor_id),
            person_id=person.id,
            role=fields["role"],
            is_active=True,
        )
        db.add(link)
        db.commit()
    except Exception as exc:
        context = _base_context(request, db, active_page="vendors")
        vendor = db.get(Vendor, coerce_uuid(vendor_id))
        people = person_service.people.list(
            db=db,
            email=None,
            status=None,
            party_status=None,
            organization_id=None,
            is_active=True,
            order_by="last_name",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        roles = rbac_service.roles.list(
            db=db,
            is_active=True,
            order_by="name",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        context.update(
            {
                "vendor": vendor,
                "vendor_users": vendor.users if vendor else [],
                "people": people,
                "roles": roles,
                "error": str(exc) or "Unable to create vendor user.",
            }
        )
        return templates.TemplateResponse("admin/vendors/detail.html", context, status_code=400)
    return RedirectResponse(url=f"/admin/vendors/{vendor_id}", status_code=303)
