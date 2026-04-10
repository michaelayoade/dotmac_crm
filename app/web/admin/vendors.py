"""Admin vendor portal web routes."""

import json
import os
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import SessionLocal
from app.logging import get_logger
from app.models.auth import AuthProvider, UserCredential
from app.models.person import Person, PersonStatus
from app.models.projects import Project
from app.models.rbac import PersonRole, Role
from app.models.vendor import (
    InstallationProject,
    InstallationProjectNote,
    ProjectQuoteStatus,
    ProposedRouteRevision,
    ProposedRouteRevisionStatus,
    Vendor,
    VendorPurchaseInvoiceStatus,
    VendorUser,
)
from app.schemas.auth import UserCredentialCreate
from app.schemas.person import PersonCreate
from app.schemas.rbac import PersonRoleCreate
from app.schemas.vendor import InstallationProjectNoteCreate, VendorCreate, VendorUpdate
from app.services import auth as auth_service
from app.services import person as person_service
from app.services import rbac as rbac_service
from app.services import vendor as vendor_service
from app.services.agent_mentions import list_active_users_for_mentions, notify_agent_mentions
from app.services.audit_helpers import recent_activity_for_paths
from app.services.auth_dependencies import require_permission
from app.services.auth_flow import hash_password
from app.services.common import coerce_uuid
from app.services.storage import storage
from app.web.templates import Jinja2Templates

templates = Jinja2Templates(directory="templates")
logger = get_logger(__name__)


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
    from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats

    return {
        "request": request,
        "active_page": active_page,
        "active_menu": "vendors",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
    }


def _current_person_id(request: Request) -> str | None:
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request) or {}
    person_id = current_user.get("person_id")
    if not person_id:
        return None
    return str(person_id)


def _is_admin_user(request: Request) -> bool:
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request) or {}
    roles = current_user.get("roles") if isinstance(current_user, dict) else []
    if not isinstance(roles, list):
        return False
    role_names = {str(role).strip().lower() for role in roles if role}
    return "admin" in role_names or "superadmin" in role_names


def _safe_quote_redirect_target(redirect_to: str | None) -> str | None:
    if not redirect_to:
        return None
    target = str(redirect_to).strip()
    if not target or "://" in target or target.startswith("//"):
        return None
    if not target.startswith("/admin/vendors/quotes"):
        return None
    return target


def _safe_purchase_invoice_redirect_target(redirect_to: str | None) -> str | None:
    if not redirect_to:
        return None
    target = str(redirect_to).strip()
    if not target or "://" in target or target.startswith("//"):
        return None
    if not target.startswith("/admin/vendors/purchase-invoices"):
        return None
    return target


def _safe_as_built_redirect_target(redirect_to: str | None) -> str | None:
    if not redirect_to:
        return None
    target = str(redirect_to).strip()
    if not target or "://" in target or target.startswith("//"):
        return None
    if not target.startswith("/admin/vendors/as-built"):
        return None
    return target


def _append_query_param(url: str, key: str, value: str) -> str:
    parts = urlsplit(url)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    query_pairs.append((key, unquote(value)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_pairs), parts.fragment))


async def _collect_attachment_uploads(
    request: Request,
    attachments: list[UploadFile] | None,
) -> list[UploadFile]:
    uploads: list[UploadFile] = []
    if attachments:
        uploads.extend(attachments)
    try:
        form = await request.form()
        uploads.extend([item for item in form.getlist("attachments") if isinstance(item, UploadFile)])
    except Exception:
        logger.debug("vendor_request_form_attachments_unavailable", exc_info=True)
    deduped: list[UploadFile] = []
    seen: set[tuple[str, int]] = set()
    for item in uploads:
        name = getattr(item, "filename", "") or ""
        file_obj = getattr(item, "file", None)
        marker = (name, id(file_obj) if file_obj is not None else id(item))
        if marker in seen:
            continue
        seen.add(marker)
        if name:
            deduped.append(item)
    return deduped


def _person_label(person: Person) -> str:
    label = (person.display_name or "").strip()
    if label:
        return label
    name = f"{(person.first_name or '').strip()} {(person.last_name or '').strip()}".strip()
    if name:
        return name
    return person.email


def _build_quote_comments(
    db: Session,
    quote_ids: set[str],
    installation_project_ids: set[object],
) -> dict[str, list[dict[str, object]]]:
    comments_by_quote: dict[str, list[dict[str, object]]] = {quote_id: [] for quote_id in quote_ids}
    if not quote_ids or not installation_project_ids:
        return comments_by_quote

    notes = (
        db.query(InstallationProjectNote)
        .filter(InstallationProjectNote.project_id.in_(installation_project_ids))
        .filter(InstallationProjectNote.is_internal.is_(True))
        .order_by(InstallationProjectNote.created_at.desc())
        .all()
    )
    if not notes:
        return comments_by_quote

    author_ids = {note.author_person_id for note in notes if note.author_person_id}
    author_labels: dict[object, str] = {}
    if author_ids:
        people = db.query(Person).filter(Person.id.in_(author_ids)).all()
        author_labels = {person.id: _person_label(person) for person in people}

    for note in notes:
        parsed = vendor_service.parse_quote_comment_body(note.body or "")
        comment_quote_id = str(parsed.get("quote_id") or "").lower()
        if not comment_quote_id or comment_quote_id not in comments_by_quote:
            continue
        comments_by_quote[comment_quote_id].append(
            {
                "id": str(note.id),
                "body": str(parsed.get("body") or "").strip(),
                "action": str(parsed.get("action") or "").strip().lower() or None,
                "created_at": note.created_at.isoformat() if note.created_at else None,
                "author": author_labels.get(note.author_person_id) if note.author_person_id else None,
                "attachments": (
                    [item for item in note.attachments if isinstance(item, dict)]
                    if isinstance(note.attachments, list)
                    else ([note.attachments] if isinstance(note.attachments, dict) else [])
                ),
            }
        )

    return comments_by_quote


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
    order_by: str = Query("name"),
    order_dir: str = Query("asc"),
    db: Session = Depends(get_db),
):
    if order_by not in {"created_at", "name"}:
        order_by = "name"
    if order_dir not in {"asc", "desc"}:
        order_dir = "asc"
    current_status = (status or "active").lower()
    is_active = True
    if current_status == "inactive":
        is_active = False
    vendors = vendor_service.vendors.list(
        db=db,
        is_active=is_active,
        order_by=order_by,
        order_dir=order_dir,
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
            "order_by": order_by,
            "order_dir": order_dir,
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


@router.get(
    "/quotes",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:read"))],
)
def vendor_quotes_list(
    request: Request,
    db: Session = Depends(get_db),
    quote_action: str | None = None,
    route_action: str | None = None,
    quote_error_detail: str | None = None,
    route_error_detail: str | None = None,
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
    quote_ids = [quote.id for quote in quotes]
    quote_id_set = {str(quote_id).lower() for quote_id in quote_ids}
    route_revisions_by_quote: dict[object, list[ProposedRouteRevision]] = {quote_id: [] for quote_id in quote_ids}
    route_duplicate_warnings_by_revision: dict[str, list[dict[str, object]]] = {}
    route_duplicate_exact_by_revision: dict[str, bool] = {}
    if quote_ids:
        revisions = (
            db.query(ProposedRouteRevision)
            .filter(ProposedRouteRevision.quote_id.in_(quote_ids))
            .order_by(ProposedRouteRevision.quote_id.asc(), ProposedRouteRevision.revision_number.desc())
            .all()
        )
        for revision in revisions:
            route_revisions_by_quote.setdefault(revision.quote_id, []).append(revision)
            if revision.status == ProposedRouteRevisionStatus.submitted:
                warnings = vendor_service.proposed_route_revisions.find_duplicate_segments(
                    db,
                    revision_id=str(revision.id),
                    limit=5,
                )
                route_duplicate_warnings_by_revision[str(revision.id)] = warnings
                route_duplicate_exact_by_revision[str(revision.id)] = any(
                    str(item.get("match_type")) == "exact" for item in warnings
                )
    quote_comments_by_quote = _build_quote_comments(
        db,
        quote_ids=quote_id_set,
        installation_project_ids=installation_project_ids,
    )
    context = _base_context(request, db, active_page="vendor-quotes")
    context.update(
        {
            "quotes": quotes,
            "project_labels": project_labels,
            "vendor_labels": vendor_labels,
            "quote_action": quote_action,
            "route_action": route_action,
            "quote_error_detail": quote_error_detail,
            "route_error_detail": route_error_detail,
            "route_revisions_by_quote": route_revisions_by_quote,
            "route_duplicate_warnings_by_revision": route_duplicate_warnings_by_revision,
            "route_duplicate_exact_by_revision": route_duplicate_exact_by_revision,
            "quote_comments_by_quote": quote_comments_by_quote,
        }
    )
    return templates.TemplateResponse("admin/vendors/quotes/review.html", context)


@router.get(
    "/quotes/{quote_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:read"))],
)
def vendor_quote_detail(
    quote_id: str,
    request: Request,
    quote_action: str | None = None,
    quote_error_detail: str | None = None,
    quote_comment_error: str | None = None,
    db: Session = Depends(get_db),
):
    quote = vendor_service.project_quotes.get(db, quote_id)
    line_items = vendor_service.quote_line_items.list(
        db,
        quote_id=quote_id,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    route_revisions = vendor_service.proposed_route_revisions.list(
        db,
        quote_id=quote_id,
        status=None,
        order_by="revision_number",
        order_dir="desc",
        limit=100,
        offset=0,
    )

    project_label = str(quote.project_id)
    if quote.project_id:
        project_row = (
            db.query(Project.name, Project.code)
            .join(InstallationProject, InstallationProject.project_id == Project.id)
            .filter(InstallationProject.id == quote.project_id)
            .first()
        )
        if project_row:
            project_label = f"{project_row.name} ({project_row.code})" if project_row.code else project_row.name

    vendor_label = str(quote.vendor_id)
    if quote.vendor_id:
        vendor_row = db.query(Vendor.name).filter(Vendor.id == quote.vendor_id).first()
        if vendor_row and vendor_row.name:
            vendor_label = vendor_row.name
    quote_comments = _build_quote_comments(
        db,
        quote_ids={str(quote.id).lower()},
        installation_project_ids={quote.project_id},
    ).get(str(quote.id).lower(), [])
    mention_agents = list_active_users_for_mentions(db)

    reviewer_label: str | None = None
    if quote.reviewed_by:
        reviewer_label = (quote.reviewed_by.display_name or "").strip()
        if not reviewer_label:
            first = (quote.reviewed_by.first_name or "").strip()
            last = (quote.reviewed_by.last_name or "").strip()
            reviewer_label = f"{first} {last}".strip()
        if not reviewer_label:
            reviewer_label = quote.reviewed_by.email

    context = _base_context(request, db, active_page="vendor-quotes")
    context.update(
        {
            "quote": quote,
            "project_label": project_label,
            "vendor_label": vendor_label,
            "line_items": line_items,
            "route_revisions": route_revisions,
            "reviewer_label": reviewer_label,
            "quote_action": quote_action,
            "quote_error_detail": quote_error_detail,
            "quote_comment_error": quote_comment_error,
            "quote_comments": quote_comments,
            "mention_agents": mention_agents,
        }
    )
    return templates.TemplateResponse("admin/vendors/quotes/detail.html", context)


@router.get(
    "/purchase-invoices",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:read"))],
)
def vendor_purchase_invoices_list(
    request: Request,
    db: Session = Depends(get_db),
    invoice_action: str | None = None,
    invoice_error_detail: str | None = None,
):
    invoices = vendor_service.vendor_purchase_invoices.list(
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
    vendor_ids = {invoice.vendor_id for invoice in invoices if invoice.vendor_id}
    installation_project_ids = {invoice.project_id for invoice in invoices if invoice.project_id}
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
    context = _base_context(request, db, active_page="vendor-purchase-invoices")
    context.update(
        {
            "invoices": invoices,
            "project_labels": project_labels,
            "vendor_labels": vendor_labels,
            "invoice_action": invoice_action,
            "invoice_error_detail": invoice_error_detail,
        }
    )
    return templates.TemplateResponse("admin/vendors/purchase_invoices/review.html", context)


@router.get(
    "/purchase-invoices/{invoice_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:read"))],
)
def vendor_purchase_invoice_detail(
    invoice_id: str,
    request: Request,
    invoice_action: str | None = None,
    invoice_error_detail: str | None = None,
    db: Session = Depends(get_db),
):
    invoice = vendor_service.vendor_purchase_invoices.get(db, invoice_id)
    line_items = vendor_service.vendor_purchase_invoice_line_items.list(
        db,
        invoice_id=invoice_id,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )

    project_label = str(invoice.project_id)
    if invoice.project_id:
        project_row = (
            db.query(Project.name, Project.code)
            .join(InstallationProject, InstallationProject.project_id == Project.id)
            .filter(InstallationProject.id == invoice.project_id)
            .first()
        )
        if project_row:
            project_label = f"{project_row.name} ({project_row.code})" if project_row.code else project_row.name

    vendor_label = str(invoice.vendor_id)
    if invoice.vendor_id:
        vendor_row = db.query(Vendor.name).filter(Vendor.id == invoice.vendor_id).first()
        if vendor_row and vendor_row.name:
            vendor_label = vendor_row.name

    reviewer_label: str | None = None
    if invoice.reviewed_by:
        reviewer_label = (invoice.reviewed_by.display_name or "").strip()
        if not reviewer_label:
            first = (invoice.reviewed_by.first_name or "").strip()
            last = (invoice.reviewed_by.last_name or "").strip()
            reviewer_label = f"{first} {last}".strip()
        if not reviewer_label:
            reviewer_label = invoice.reviewed_by.email

    context = _base_context(request, db, active_page="vendor-purchase-invoices")
    context.update(
        {
            "invoice": invoice,
            "project_label": project_label,
            "vendor_label": vendor_label,
            "line_items": line_items,
            "reviewer_label": reviewer_label,
            "invoice_action": invoice_action,
            "invoice_error_detail": invoice_error_detail,
        }
    )
    return templates.TemplateResponse("admin/vendors/purchase_invoices/detail.html", context)


@router.get(
    "/purchase-invoices/{invoice_id}/attachment",
    dependencies=[Depends(require_permission("vendors:quotes:read"))],
)
def vendor_purchase_invoice_attachment_download(
    invoice_id: str,
    db: Session = Depends(get_db),
):
    invoice = vendor_service.vendor_purchase_invoices.get(db, invoice_id)
    storage_key = (invoice.attachment_storage_key or "").strip()
    if not storage_key:
        raise HTTPException(status_code=404, detail="Attachment not found")

    try:
        data = storage.get(storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Attachment not found") from exc

    file_name = (invoice.attachment_file_name or "purchase-invoice-attachment").strip() or "purchase-invoice-attachment"
    media_type = (invoice.attachment_mime_type or "application/octet-stream").strip() or "application/octet-stream"
    headers = {"Content-Disposition": f'attachment; filename="{file_name}"'}
    return Response(content=data, media_type=media_type, headers=headers)


@router.post(
    "/quotes/{quote_id}/comments",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:update"))],
)
async def vendor_quote_add_comment(
    quote_id: str,
    request: Request,
    body: str | None = Form(None),
    mentions: str | None = Form(None),
    attachments: list[UploadFile] = File(default_factory=list),
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.services import ticket_attachments as ticket_attachment_service

    success_redirect = _safe_quote_redirect_target(redirect_to) or f"/admin/vendors/quotes/{quote_id}"
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing commenter identity.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_comment_error", detail),
            status_code=303,
        )

    quote = vendor_service.project_quotes.get(db, quote_id)
    comment_body = (body or "").strip()
    if not comment_body:
        detail = urlquote("Comment body is required.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_comment_error", detail),
            status_code=303,
        )

    upload_list = await _collect_attachment_uploads(request, attachments)
    prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(upload_list)
    try:
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
        note_body = vendor_service.build_quote_review_comment(str(quote.id), comment_body, "comment")
        vendor_service.installation_project_notes.create(
            db,
            payload=InstallationProjectNoteCreate(
                project_id=quote.project_id,
                author_person_id=coerce_uuid(reviewer_person_id),
                body=note_body,
                is_internal=True,
                attachments=saved_attachments or None,
            ),
        )
    except Exception:
        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        raise

    if mentions:
        try:
            parsed = json.loads(mentions)
            mentioned_agent_ids = parsed if isinstance(parsed, list) else []
            preview = comment_body if len(comment_body) <= 140 else f"{comment_body[:137].rstrip()}..."
            notify_agent_mentions(
                db,
                mentioned_agent_ids=list(mentioned_agent_ids),
                actor_person_id=reviewer_person_id,
                payload={
                    "kind": "mention",
                    "title": "Mentioned in vendor quote",
                    "subtitle": f"Quote {str(quote.id)[:8]}",
                    "preview": preview,
                    "target_url": f"/admin/vendors/quotes/{quote.id}",
                    "quote_id": str(quote.id),
                },
            )
        except Exception:
            logger.debug("vendor_quote_comment_mentions_failed quote_id=%s", quote.id, exc_info=True)

    return RedirectResponse(url=_append_query_param(success_redirect, "quote_action", "commented"), status_code=303)


@router.post(
    "/quotes/{quote_id}/comments/{comment_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:update"))],
)
async def vendor_quote_edit_comment(
    quote_id: str,
    comment_id: str,
    request: Request,
    body: str | None = Form(None),
    mentions: str | None = Form(None),
    attachments: list[UploadFile] = File(default_factory=list),
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.services import ticket_attachments as ticket_attachment_service

    success_redirect = _safe_quote_redirect_target(redirect_to) or f"/admin/vendors/quotes/{quote_id}"
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing editor identity.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_comment_error", detail),
            status_code=303,
        )
    comment_body = (body or "").strip()
    if not comment_body:
        detail = urlquote("Comment body is required.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_comment_error", detail),
            status_code=303,
        )

    quote = vendor_service.project_quotes.get(db, quote_id)
    note = db.get(InstallationProjectNote, coerce_uuid(comment_id))
    if not note or str(note.project_id) != str(quote.project_id):
        detail = urlquote("Comment not found.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_comment_error", detail),
            status_code=303,
        )

    parsed_existing = vendor_service.parse_quote_comment_body(note.body or "")
    if str(parsed_existing.get("quote_id") or "").lower() != str(quote.id).lower():
        detail = urlquote("Comment does not belong to this quote.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_comment_error", detail),
            status_code=303,
        )
    if str(parsed_existing.get("action") or "").lower() not in {"", "comment"}:
        detail = urlquote("Only quote comments can be edited.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_comment_error", detail),
            status_code=303,
        )

    upload_list = await _collect_attachment_uploads(request, attachments)
    prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(upload_list)
    try:
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
        if isinstance(note.attachments, list):
            existing_attachments = [item for item in note.attachments if isinstance(item, dict)]
        elif isinstance(note.attachments, dict):
            existing_attachments = [note.attachments]
        else:
            existing_attachments = []
        note.attachments = existing_attachments + (saved_attachments or [])
        note.body = vendor_service.build_quote_review_comment(str(quote.id), comment_body, "comment")
        db.commit()
    except Exception:
        db.rollback()
        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        raise

    if mentions:
        try:
            parsed = json.loads(mentions)
            mentioned_agent_ids = parsed if isinstance(parsed, list) else []
            preview = comment_body if len(comment_body) <= 140 else f"{comment_body[:137].rstrip()}..."
            notify_agent_mentions(
                db,
                mentioned_agent_ids=list(mentioned_agent_ids),
                actor_person_id=reviewer_person_id,
                payload={
                    "kind": "mention",
                    "title": "Mentioned in vendor quote",
                    "subtitle": f"Quote {str(quote.id)[:8]}",
                    "preview": preview,
                    "target_url": f"/admin/vendors/quotes/{quote.id}",
                    "quote_id": str(quote.id),
                },
            )
        except Exception:
            logger.debug("vendor_quote_reply_mentions_failed quote_id=%s", quote.id, exc_info=True)
    return RedirectResponse(url=_append_query_param(success_redirect, "quote_action", "commented"), status_code=303)


@router.post(
    "/quotes/{quote_id}/approve",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:update"))],
)
def vendor_quote_approve(
    quote_id: str,
    request: Request,
    review_notes: str | None = Form(None),
    override_threshold: str | None = Form(None),
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
):
    success_redirect = _safe_quote_redirect_target(redirect_to) or "/admin/vendors/quotes"
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing reviewer identity.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_error_detail", detail),
            status_code=303,
        )

    quote = vendor_service.project_quotes.get(db, quote_id)
    if quote.status not in {ProjectQuoteStatus.submitted, ProjectQuoteStatus.under_review}:
        detail = urlquote("Only submitted quotes can be approved.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_error_detail", detail),
            status_code=303,
        )

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
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_error_detail", detail),
            status_code=303,
        )

    return RedirectResponse(url=_append_query_param(success_redirect, "quote_action", "approved"), status_code=303)


@router.post(
    "/quotes/{quote_id}/reject",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:update"))],
)
def vendor_quote_reject(
    quote_id: str,
    request: Request,
    review_notes: str | None = Form(None),
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
):
    success_redirect = _safe_quote_redirect_target(redirect_to) or "/admin/vendors/quotes"
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing reviewer identity.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_error_detail", detail),
            status_code=303,
        )

    quote = vendor_service.project_quotes.get(db, quote_id)
    if quote.status not in {ProjectQuoteStatus.submitted, ProjectQuoteStatus.under_review}:
        detail = urlquote("Only submitted quotes can be rejected.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_error_detail", detail),
            status_code=303,
        )

    try:
        vendor_service.project_quotes.reject(
            db,
            quote_id=quote_id,
            reviewer_person_id=reviewer_person_id,
            review_notes=(review_notes or "").strip() or None,
        )
    except HTTPException as exc:
        detail = urlquote(str(exc.detail or "Failed to reject quote."), safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "quote_error_detail", detail),
            status_code=303,
        )

    return RedirectResponse(url=_append_query_param(success_redirect, "quote_action", "rejected"), status_code=303)


@router.post(
    "/purchase-invoices/{invoice_id}/approve",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:update"))],
)
def vendor_purchase_invoice_approve(
    invoice_id: str,
    request: Request,
    review_notes: str | None = Form(None),
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
):
    success_redirect = _safe_purchase_invoice_redirect_target(redirect_to) or "/admin/vendors/purchase-invoices"
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing reviewer identity.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "invoice_error_detail", detail),
            status_code=303,
        )

    invoice = vendor_service.vendor_purchase_invoices.get(db, invoice_id)
    if invoice.status not in {VendorPurchaseInvoiceStatus.submitted, VendorPurchaseInvoiceStatus.under_review}:
        detail = urlquote("Only submitted purchase invoices can be approved.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "invoice_error_detail", detail),
            status_code=303,
        )

    try:
        approved_invoice = vendor_service.vendor_purchase_invoices.approve(
            db,
            invoice_id=invoice_id,
            reviewer_person_id=reviewer_person_id,
            review_notes=(review_notes or "").strip() or None,
        )
    except HTTPException as exc:
        detail = urlquote(str(exc.detail or "Failed to approve purchase invoice."), safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "invoice_error_detail", detail),
            status_code=303,
        )

    if not (approved_invoice.erp_purchase_order_id or "").strip():
        detail = urlquote(
            "Purchase invoice approved, but ERP sync was not queued because no ERP PO is linked to the project.",
            safe="",
        )
        redirect_url = _append_query_param(success_redirect, "invoice_action", "approved")
        redirect_url = _append_query_param(redirect_url, "invoice_error_detail", detail)
        return RedirectResponse(url=redirect_url, status_code=303)

    try:
        from app.tasks.integrations import sync_purchase_invoice_to_erp

        sync_purchase_invoice_to_erp.apply_async(args=[invoice_id], countdown=2, priority=5)
    except Exception as exc:
        detail = urlquote(f"Purchase invoice approved, but ERP sync could not be queued: {exc}", safe="")
        redirect_url = _append_query_param(success_redirect, "invoice_action", "approved")
        redirect_url = _append_query_param(redirect_url, "invoice_error_detail", detail)
        return RedirectResponse(url=redirect_url, status_code=303)

    return RedirectResponse(url=_append_query_param(success_redirect, "invoice_action", "approved"), status_code=303)


@router.post(
    "/purchase-invoices/{invoice_id}/reject",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:update"))],
)
def vendor_purchase_invoice_reject(
    invoice_id: str,
    request: Request,
    review_notes: str | None = Form(None),
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
):
    success_redirect = _safe_purchase_invoice_redirect_target(redirect_to) or "/admin/vendors/purchase-invoices"
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing reviewer identity.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "invoice_error_detail", detail),
            status_code=303,
        )

    invoice = vendor_service.vendor_purchase_invoices.get(db, invoice_id)
    if invoice.status not in {VendorPurchaseInvoiceStatus.submitted, VendorPurchaseInvoiceStatus.under_review}:
        detail = urlquote("Only submitted purchase invoices can be rejected.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "invoice_error_detail", detail),
            status_code=303,
        )

    try:
        vendor_service.vendor_purchase_invoices.reject(
            db,
            invoice_id=invoice_id,
            reviewer_person_id=reviewer_person_id,
            review_notes=(review_notes or "").strip() or None,
        )
    except HTTPException as exc:
        detail = urlquote(str(exc.detail or "Failed to reject purchase invoice."), safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "invoice_error_detail", detail),
            status_code=303,
        )

    return RedirectResponse(url=_append_query_param(success_redirect, "invoice_action", "rejected"), status_code=303)


@router.post(
    "/quotes/{quote_id}/route-revisions/{revision_id}/approve",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:update"))],
)
def vendor_route_revision_approve(
    quote_id: str,
    revision_id: str,
    request: Request,
    review_notes: str | None = Form(None),
    confirm_exact_duplicate: str | None = Form(None),
    db: Session = Depends(get_db),
):
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing reviewer identity.", safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?route_error_detail={detail}", status_code=303)

    revision = vendor_service.proposed_route_revisions.get(db, revision_id)
    if str(revision.quote_id) != str(quote_id):
        detail = urlquote("Route revision does not belong to quote.", safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?route_error_detail={detail}", status_code=303)

    try:
        duplicate_warnings = vendor_service.proposed_route_revisions.find_duplicate_segments(
            db,
            revision_id=revision_id,
            limit=5,
        )
        has_exact_duplicate = any(str(item.get("match_type")) == "exact" for item in duplicate_warnings)
        confirm_exact = str(confirm_exact_duplicate or "").strip().lower() in {"1", "true", "yes", "on"}
        if has_exact_duplicate and not confirm_exact:
            detail = urlquote("This route matches an existing route exactly. Approve anyway?", safe="")
            return RedirectResponse(url=f"/admin/vendors/quotes?route_error_detail={detail}", status_code=303)
        vendor_service.proposed_route_revisions.approve(
            db,
            revision_id=revision_id,
            reviewer_person_id=reviewer_person_id,
            review_notes=(review_notes or "").strip() or None,
        )
    except HTTPException as exc:
        detail = urlquote(str(exc.detail or "Failed to approve route revision."), safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?route_error_detail={detail}", status_code=303)

    return RedirectResponse(url="/admin/vendors/quotes?route_action=approved", status_code=303)


@router.post(
    "/quotes/{quote_id}/route-revisions/{revision_id}/reject",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:update"))],
)
def vendor_route_revision_reject(
    quote_id: str,
    revision_id: str,
    request: Request,
    review_notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing reviewer identity.", safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?route_error_detail={detail}", status_code=303)

    revision = vendor_service.proposed_route_revisions.get(db, revision_id)
    if str(revision.quote_id) != str(quote_id):
        detail = urlquote("Route revision does not belong to quote.", safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?route_error_detail={detail}", status_code=303)

    try:
        vendor_service.proposed_route_revisions.reject(
            db,
            revision_id=revision_id,
            reviewer_person_id=reviewer_person_id,
            review_notes=(review_notes or "").strip() or None,
        )
    except HTTPException as exc:
        detail = urlquote(str(exc.detail or "Failed to reject route revision."), safe="")
        return RedirectResponse(url=f"/admin/vendors/quotes?route_error_detail={detail}", status_code=303)

    return RedirectResponse(url="/admin/vendors/quotes?route_action=rejected", status_code=303)


@router.get(
    "/quotes/{quote_id}/route-revisions/{revision_id}/view",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:read"))],
)
def vendor_route_revision_view(
    quote_id: str,
    revision_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    revision = vendor_service.proposed_route_revisions.get(db, revision_id)
    if str(revision.quote_id) != str(quote_id):
        return RedirectResponse(
            url="/admin/vendors/quotes?route_error_detail=Route+revision+does+not+belong+to+quote", status_code=303
        )

    route_geojson_str = (
        db.query(func.ST_AsGeoJSON(ProposedRouteRevision.route_geom))
        .filter(ProposedRouteRevision.id == coerce_uuid(revision_id))
        .scalar()
    )
    route_geojson = None
    if route_geojson_str:
        import json

        route_geojson = json.loads(route_geojson_str)

    from app.services.fiber_plant import fiber_plant

    geojson_data = fiber_plant.get_geojson(db)

    context = _base_context(request, db, active_page="vendor-quotes")
    context.update(
        {
            "quote_id": quote_id,
            "revision": revision,
            "route_geojson": route_geojson,
            "geojson_data": geojson_data,
        }
    )
    return templates.TemplateResponse("admin/vendors/quotes/route-view.html", context)


@router.get("/as-built", response_class=HTMLResponse)
def vendor_as_built_list(request: Request, db: Session = Depends(get_db)):
    context = _base_context(request, db, active_page="vendor-as-built")
    return templates.TemplateResponse("admin/vendors/as-built/review.html", context)


@router.get(
    "/as-built/{as_built_id}",
    response_class=HTMLResponse,
)
def vendor_as_built_detail(
    as_built_id: str,
    request: Request,
    as_built_action: str | None = None,
    as_built_error_detail: str | None = None,
    db: Session = Depends(get_db),
):
    as_built = vendor_service.as_built_routes.get(db=db, as_built_id=as_built_id)
    context = _base_context(request, db, active_page="vendor-as-built")
    context.update(
        {
            "as_built": as_built,
            "as_built_action": as_built_action,
            "as_built_error_detail": as_built_error_detail,
        }
    )
    return templates.TemplateResponse("admin/vendors/as-built/detail.html", context)


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


@router.post(
    "/as-built/{as_built_id}/approve",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:update"))],
)
def vendor_as_built_approve(
    as_built_id: str,
    request: Request,
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
):
    success_redirect = _safe_as_built_redirect_target(redirect_to) or f"/admin/vendors/as-built/{as_built_id}"
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing reviewer identity.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "as_built_error_detail", detail),
            status_code=303,
        )
    try:
        vendor_service.as_built_routes.accept_and_convert(db, as_built_id=as_built_id, reviewer_id=reviewer_person_id)
    except HTTPException as exc:
        detail = urlquote(str(exc.detail or "Failed to approve as-built submission."), safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "as_built_error_detail", detail),
            status_code=303,
        )
    return RedirectResponse(url=_append_query_param(success_redirect, "as_built_action", "approved"), status_code=303)


@router.post(
    "/as-built/{as_built_id}/reject",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:update"))],
)
def vendor_as_built_reject(
    as_built_id: str,
    request: Request,
    review_notes: str | None = Form(None),
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
):
    success_redirect = _safe_as_built_redirect_target(redirect_to) or f"/admin/vendors/as-built/{as_built_id}"
    reviewer_person_id = _current_person_id(request)
    if not reviewer_person_id:
        detail = urlquote("Missing reviewer identity.", safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "as_built_error_detail", detail),
            status_code=303,
        )
    try:
        vendor_service.as_built_routes.reject(
            db,
            as_built_id=as_built_id,
            reviewer_id=reviewer_person_id,
            review_notes=review_notes,
        )
    except HTTPException as exc:
        detail = urlquote(str(exc.detail or "Failed to reject as-built submission."), safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "as_built_error_detail", detail),
            status_code=303,
        )
    return RedirectResponse(url=_append_query_param(success_redirect, "as_built_action", "rejected"), status_code=303)


@router.post(
    "/as-built/{as_built_id}/delete",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("vendors:quotes:update"))],
)
def vendor_as_built_delete(
    as_built_id: str,
    request: Request,
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
):
    success_redirect = "/admin/vendors/as-built"
    try:
        vendor_service.as_built_routes.delete(db, as_built_id=as_built_id)
    except HTTPException as exc:
        detail = urlquote(str(exc.detail or "Failed to delete as-built submission."), safe="")
        return RedirectResponse(
            url=_append_query_param(success_redirect, "as_built_error_detail", detail),
            status_code=303,
        )
    return RedirectResponse(url=_append_query_param(success_redirect, "as_built_action", "deleted"), status_code=303)


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
