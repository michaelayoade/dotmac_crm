"""Admin subscriber web routes."""

import contextlib
import csv
import io
import math
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.subscriber import AccountType, Organization, Subscriber, SubscriberStatus
from app.services import reseller as reseller_service
from app.services import reseller_admin as reseller_admin_service
from app.services.audit_helpers import log_audit_event
from app.services.subscriber import subscriber as subscriber_service
from app.web.auth.rbac import require_web_role

router = APIRouter(prefix="/subscribers", tags=["admin-subscribers"])
templates = Jinja2Templates(directory="templates")


def _format_balance_display(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, int | float):
        return f"{float(value):.2f}"
    text = str(value).strip()
    if not text:
        return "—"
    try:
        return f"{float(text):.2f}"
    except (TypeError, ValueError):
        return text


def _decorate_balance_display(subscribers: list[Subscriber]) -> None:
    for subscriber in subscribers:
        subscriber.balance_display = _format_balance_display(getattr(subscriber, "balance", None))  # type: ignore[attr-defined]


def _parse_export_days(value: str | None) -> int:
    try:
        parsed = int((value or "").strip())
    except (TypeError, ValueError):
        return 30
    return max(1, min(parsed, 3650))


def _clean_export_value(value: object | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text in {"-", "—", "N/A"}:
        return ""
    return text


def _source_label(external_system: str | None) -> str:
    value = (external_system or "").strip().lower()
    if value == "splynx":
        return "Splynx"
    if value == "ucrm":
        return "UCRM"
    if value == "whmcs":
        return "WHMCS"
    if value == "manual":
        return "Manual"
    return "Manual"


def _contact_detail_values(subscriber: Subscriber) -> tuple[str, str]:
    person = getattr(subscriber, "person", None)
    if not person:
        return "", ""
    return (person.email or "").strip(), (person.phone or "").strip()


def _csv_response(data: list[dict[str, str]], filename: str) -> StreamingResponse:
    output = io.StringIO()
    if data:
        writer = csv.DictWriter(output, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)
    else:
        output.write("No data available\n")
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("", response_class=HTMLResponse)
def subscriber_list(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = None,
    external_system: str | None = None,
    status: str | None = None,
    order_by: str = Query("created_at"),
    order_dir: str = Query("desc"),
    page: int = 1,
    per_page: int = 20,
    export_days: str | None = Query("30"),
):
    """List subscribers."""
    if order_by not in {"created_at", "updated_at", "subscriber_number", "status"}:
        order_by = "created_at"
    if order_dir not in {"asc", "desc"}:
        order_dir = "desc"
    from app.web.admin import get_current_user, get_sidebar_stats

    user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)

    allowed_per_page = {10, 20, 25, 50, 100, 200}
    if per_page not in allowed_per_page:
        per_page = 20

    status_filter = None
    if status:
        with contextlib.suppress(ValueError):
            status_filter = SubscriberStatus(status)

    offset = (page - 1) * per_page

    subscribers = subscriber_service.list(
        db,
        search=search,
        external_system=external_system,
        status=status_filter,
        order_by=order_by,
        order_dir=order_dir,
        limit=per_page,
        offset=offset,
    )
    _decorate_balance_display(subscribers)

    total = subscriber_service.count(
        db,
        search=search,
        external_system=external_system,
        status=status_filter,
    )

    total_pages = math.ceil(total / per_page) if total > 0 else 1
    stats = subscriber_service.get_stats(db)

    # Check if this is an HTMX request for just the table
    if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "subscribers-table":
        return templates.TemplateResponse(
            "admin/subscribers/_table.html",
            {
                "request": request,
                "subscribers": subscribers,
                "search": search,
                "external_system": external_system,
                "status": status,
                "order_by": order_by,
                "order_dir": order_dir,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
                "active_page": "subscribers",
            },
        )

    return templates.TemplateResponse(
        "admin/subscribers/index.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": sidebar_stats,
            "subscribers": subscribers,
            "stats": stats,
            "search": search,
            "external_system": external_system,
            "status": status,
            "order_by": order_by,
            "order_dir": order_dir,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "statuses": [s.value for s in SubscriberStatus],
            "active_page": "subscribers",
            "export_days": str(_parse_export_days(export_days)),
        },
    )


@router.get("/export")
def subscriber_export_csv(
    db: Session = Depends(get_db),
    search: str | None = None,
    external_system: str | None = None,
    status: str | None = None,
    order_by: str = Query("created_at"),
    order_dir: str = Query("desc"),
    export_days: str | None = Query("30"),
):
    if order_by not in {"created_at", "updated_at", "subscriber_number", "status"}:
        order_by = "created_at"
    if order_dir not in {"asc", "desc"}:
        order_dir = "desc"

    status_filter = None
    if status:
        with contextlib.suppress(ValueError):
            status_filter = SubscriberStatus(status)

    parsed_days = _parse_export_days(export_days)
    cutoff = datetime.now(UTC) - timedelta(days=parsed_days)

    subscribers = subscriber_service.list(
        db,
        search=search,
        external_system=external_system,
        status=status_filter,
        order_by=order_by,
        order_dir=order_dir,
        limit=10000,
        offset=0,
    )
    subscribers = [
        subscriber for subscriber in subscribers if subscriber.created_at and subscriber.created_at >= cutoff
    ]

    rows: list[dict[str, str]] = []
    for subscriber in subscribers:
        contact_email, contact_number = _contact_detail_values(subscriber)
        rows.append(
            {
                "Name": _clean_export_value(subscriber.display_name),
                "Contact Detail - Email": _clean_export_value(contact_email),
                "Contact Detail - Number": _clean_export_value(contact_number),
                "External ID": _clean_export_value(subscriber.external_id),
                "Source": _clean_export_value(_source_label(subscriber.external_system)),
                "Status": _clean_export_value(subscriber.status.value.title() if subscriber.status else ""),
                "Created": _clean_export_value(
                    subscriber.created_at.strftime("%Y-%m-%d") if subscriber.created_at else ""
                ),
            }
        )

    filename = f"subscribers_{parsed_days}d_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return _csv_response(rows, filename)


@router.get("/resellers", response_class=HTMLResponse)
def reseller_organizations_list(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
):
    """List organizations configured as resellers."""
    from app.csrf import get_csrf_token
    from app.web.admin import get_current_user, get_sidebar_stats

    user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    offset = (page - 1) * per_page

    query = db.query(Organization).filter(
        Organization.account_type == AccountType.reseller,
        Organization.is_active.is_(True),
    )
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Organization.name.ilike(like),
                Organization.legal_name.ilike(like),
                Organization.domain.ilike(like),
            )
        )

    total = query.with_entities(func.count(Organization.id)).scalar() or 0
    resellers = query.order_by(Organization.name.asc()).offset(offset).limit(per_page).all()
    total_pages = math.ceil(total / per_page) if total > 0 else 1
    matched_reseller_ids = query.with_entities(Organization.id).subquery()

    child_total = (
        db.query(func.count(Organization.id))
        .filter(Organization.parent_id.in_(select(matched_reseller_ids.c.id)))
        .scalar()
        or 0
    )
    direct_subscriber_total = (
        db.query(func.count(Subscriber.id))
        .filter(
            Subscriber.organization_id.in_(select(matched_reseller_ids.c.id)),
            Subscriber.is_active.is_(True),
        )
        .scalar()
        or 0
    )
    scoped_org_ids = (
        db.query(Organization.id)
        .filter(
            or_(
                Organization.id.in_(select(matched_reseller_ids.c.id)),
                Organization.parent_id.in_(select(matched_reseller_ids.c.id)),
            )
        )
        .subquery()
    )
    hierarchy_subscriber_total = (
        db.query(func.count(Subscriber.id))
        .filter(
            Subscriber.organization_id.in_(select(scoped_org_ids.c.id)),
            Subscriber.is_active.is_(True),
        )
        .scalar()
        or 0
    )

    reseller_ids = [org.id for org in resellers]
    subscriber_counts: dict[UUID, int] = {}
    child_counts: dict[UUID, int] = {}
    if reseller_ids:
        child_rows = (
            db.query(Organization.parent_id, func.count(Organization.id))
            .filter(Organization.parent_id.in_(reseller_ids))
            .group_by(Organization.parent_id)
            .all()
        )
        child_counts = {parent_id: int(count) for parent_id, count in child_rows if parent_id}

        # Count subscribers attached directly to reseller orgs.
        sub_rows = (
            db.query(Organization.id, func.count())
            .outerjoin(
                Subscriber,
                Subscriber.organization_id == Organization.id,
            )
            .filter(Organization.id.in_(reseller_ids))
            .group_by(Organization.id)
            .all()
        )
        subscriber_counts = {org_id: int(count) for org_id, count in sub_rows}

    return templates.TemplateResponse(
        "admin/subscribers/resellers.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": sidebar_stats,
            "resellers": resellers,
            "search": search or "",
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "child_counts": child_counts,
            "subscriber_counts": subscriber_counts,
            "summary": {
                "resellers": total,
                "child_orgs": int(child_total),
                "direct_subscribers": int(direct_subscriber_total),
                "hierarchy_subscribers": int(hierarchy_subscriber_total),
            },
            "csrf_token": get_csrf_token(request),
            "active_page": "subscribers",
        },
    )


@router.get("/resellers/new", response_class=HTMLResponse)
def reseller_new(
    request: Request,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_web_role("admin")),
):
    """Admin: create a reseller org + login."""
    from app.csrf import get_csrf_token
    from app.web.admin import get_current_user, get_sidebar_stats

    user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    return templates.TemplateResponse(
        "admin/subscribers/reseller_new.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": sidebar_stats,
            "csrf_token": get_csrf_token(request),
            "active_page": "subscribers",
        },
    )


@router.get("/resellers/promote", response_class=HTMLResponse)
def reseller_promote_picker(
    request: Request,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_web_role("admin")),
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
):
    """Admin: choose an existing org to promote to reseller."""
    from app.csrf import get_csrf_token
    from app.web.admin import get_current_user, get_sidebar_stats

    user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    offset = (page - 1) * per_page

    query = (
        db.query(Organization)
        .filter(Organization.is_active.is_(True))
        .filter(Organization.account_type != AccountType.reseller)
    )
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Organization.name.ilike(like),
                Organization.legal_name.ilike(like),
                Organization.domain.ilike(like),
            )
        )

    total = query.with_entities(func.count(Organization.id)).scalar() or 0
    total_pages = math.ceil(total / per_page) if total > 0 else 1
    orgs = query.order_by(Organization.name.asc()).offset(offset).limit(per_page).all()

    return templates.TemplateResponse(
        "admin/subscribers/reseller_promote.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": sidebar_stats,
            "csrf_token": get_csrf_token(request),
            "orgs": orgs,
            "search": search or "",
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "active_page": "subscribers",
        },
    )


@router.post("/resellers/new", response_class=HTMLResponse)
def reseller_create(
    request: Request,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_web_role("admin")),
    organization_name: str = Form(...),
    organization_domain: str | None = Form(None),
    user_first_name: str = Form(...),
    user_last_name: str = Form(...),
    user_email: str = Form(...),
    user_phone: str | None = Form(None),
    password: str | None = Form(None),
    reset_password_if_exists: bool = Form(False),
):
    """Admin: create reseller org and (re)use person by email."""
    org, person = reseller_service.admin_create_reseller(
        db,
        organization_name=organization_name,
        organization_domain=organization_domain,
        user_first_name=user_first_name,
        user_last_name=user_last_name,
        user_email=user_email,
        user_phone=user_phone,
        password=password,
        reset_password_if_exists=bool(reset_password_if_exists),
    )
    # Audit the explicit reseller creation.
    log_audit_event(
        db,
        request,
        action="reseller_create",
        entity_type="/admin/subscribers/resellers",
        entity_id=str(org.id),
        actor_id=getattr(request.state, "actor_id", None),
        metadata={"organization_name": org.name, "person_id": str(person.id), "person_email": person.email},
        status_code=201,
        is_success=True,
    )
    return RedirectResponse(url="/admin/subscribers/resellers", status_code=303)


@router.post("/resellers/{organization_id}/promote", response_class=HTMLResponse)
def reseller_promote(
    request: Request,
    organization_id: UUID,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_web_role("admin")),
):
    """Admin: promote an existing org to reseller and grant reseller roles."""
    org, grantee_ids = reseller_admin_service.promote_organization_to_reseller(
        db,
        organization_id=organization_id,
        actor_person_id=getattr(getattr(request.state, "user", None), "id", None),
    )
    log_audit_event(
        db,
        request,
        action="reseller_promote",
        entity_type="/admin/subscribers/resellers",
        entity_id=str(org.id),
        actor_id=getattr(request.state, "actor_id", None),
        metadata={"granted_to_person_ids": [str(pid) for pid in grantee_ids]},
        status_code=200,
        is_success=True,
    )
    return RedirectResponse(url="/admin/subscribers/resellers", status_code=303)


@router.post("/resellers/{organization_id}/demote", response_class=HTMLResponse)
def reseller_demote(
    request: Request,
    organization_id: UUID,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_web_role("admin")),
):
    """Admin: demote a reseller org (blocks portal and removes reseller roles where safe)."""
    org, removed_from = reseller_admin_service.demote_organization_from_reseller(
        db,
        organization_id=organization_id,
        actor_person_id=getattr(getattr(request.state, "user", None), "id", None),
    )
    log_audit_event(
        db,
        request,
        action="reseller_demote",
        entity_type="/admin/subscribers/resellers",
        entity_id=str(org.id),
        actor_id=getattr(request.state, "actor_id", None),
        metadata={"removed_from_person_ids": [str(pid) for pid in removed_from]},
        status_code=200,
        is_success=True,
    )
    return RedirectResponse(url="/admin/subscribers/resellers", status_code=303)


@router.get("/resellers/{organization_id}", response_class=HTMLResponse)
def reseller_organization_subscribers(
    request: Request,
    organization_id: UUID,
    db: Session = Depends(get_db),
    page: int = 1,
    per_page: int = 20,
):
    """Show subscribers under a reseller organization hierarchy."""
    from app.csrf import get_csrf_token
    from app.web.admin import get_current_user, get_sidebar_stats

    user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)

    reseller_org = db.get(Organization, organization_id)
    if not reseller_org or reseller_org.account_type != AccountType.reseller:
        return RedirectResponse(url="/admin/subscribers/resellers", status_code=303)

    all_subscribers = subscriber_service.list_for_reseller(db, organization_id)
    total = len(all_subscribers)
    offset = (page - 1) * per_page
    subscribers = all_subscribers[offset : offset + per_page]
    total_pages = math.ceil(total / per_page) if total > 0 else 1

    return templates.TemplateResponse(
        "admin/subscribers/reseller_detail.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": sidebar_stats,
            "reseller_org": reseller_org,
            "subscribers": subscribers,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "csrf_token": get_csrf_token(request),
            "active_page": "subscribers",
        },
    )


@router.get("/new", response_class=HTMLResponse)
def subscriber_new(
    request: Request,
    db: Session = Depends(get_db),
):
    """Show subscriber creation form."""
    from app.web.admin import get_current_user, get_sidebar_stats

    user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)

    return templates.TemplateResponse(
        "admin/subscribers/form.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": sidebar_stats,
            "subscriber": None,
            "statuses": [s.value for s in SubscriberStatus],
            "is_edit": False,
            "active_page": "subscribers",
        },
    )


@router.post("", response_class=HTMLResponse)
def subscriber_create(
    request: Request,
    db: Session = Depends(get_db),
    subscriber_number: str = Form(None),
    account_number: str = Form(None),
    external_id: str = Form(None),
    external_system: str = Form(None),
    status: str = Form("active"),
    service_name: str = Form(None),
    service_plan: str = Form(None),
    service_speed: str = Form(None),
    service_address_line1: str = Form(None),
    service_city: str = Form(None),
    service_region: str = Form(None),
    service_postal_code: str = Form(None),
    notes: str = Form(None),
):
    """Create a new subscriber."""
    data = {
        "subscriber_number": subscriber_number or None,
        "account_number": account_number or None,
        "external_id": external_id or None,
        "external_system": external_system or None,
        "status": SubscriberStatus(status),
        "service_name": service_name or None,
        "service_plan": service_plan or None,
        "service_speed": service_speed or None,
        "service_address_line1": service_address_line1 or None,
        "service_city": service_city or None,
        "service_region": service_region or None,
        "service_postal_code": service_postal_code or None,
        "notes": notes or None,
    }

    subscriber = subscriber_service.create(db, data)
    return RedirectResponse(
        url=f"/admin/subscribers/{subscriber.id}",
        status_code=303,
    )


@router.get("/{subscriber_id}", response_class=HTMLResponse)
def subscriber_detail(
    request: Request,
    subscriber_id: UUID,
    db: Session = Depends(get_db),
):
    """Show subscriber detail page."""
    from app.web.admin import get_current_user, get_sidebar_stats

    user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)

    subscriber = subscriber_service.get(db, subscriber_id)
    if not subscriber:
        return RedirectResponse(url="/admin/subscribers", status_code=303)

    return templates.TemplateResponse(
        "admin/subscribers/detail.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": sidebar_stats,
            "subscriber": subscriber,
            "active_page": "subscribers",
        },
    )


@router.get("/{subscriber_id}/edit", response_class=HTMLResponse)
def subscriber_edit(
    request: Request,
    subscriber_id: UUID,
    db: Session = Depends(get_db),
):
    """Show subscriber edit form."""
    from app.web.admin import get_current_user, get_sidebar_stats

    user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)

    subscriber = subscriber_service.get(db, subscriber_id)
    if not subscriber:
        return RedirectResponse(url="/admin/subscribers", status_code=303)

    return templates.TemplateResponse(
        "admin/subscribers/form.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": sidebar_stats,
            "subscriber": subscriber,
            "statuses": [s.value for s in SubscriberStatus],
            "is_edit": True,
            "active_page": "subscribers",
        },
    )


@router.post("/{subscriber_id}", response_class=HTMLResponse)
def subscriber_update(
    request: Request,
    subscriber_id: UUID,
    db: Session = Depends(get_db),
    notes: str = Form(None),
    status: str = Form(None),
):
    """Update subscriber (limited fields - most data from sync)."""
    subscriber = subscriber_service.get(db, subscriber_id)
    if not subscriber:
        return RedirectResponse(url="/admin/subscribers", status_code=303)

    data: dict[str, object] = {}
    if notes is not None:
        data["notes"] = notes or None
    if status:
        data["status"] = SubscriberStatus(status)

    subscriber_service.update(db, subscriber, data)
    return RedirectResponse(
        url=f"/admin/subscribers/{subscriber_id}",
        status_code=303,
    )


@router.post("/{subscriber_id}/link-person", response_class=HTMLResponse)
def subscriber_link_person(
    request: Request,
    subscriber_id: UUID,
    db: Session = Depends(get_db),
    person_id: UUID = Form(...),
):
    """Link subscriber to a person contact."""
    subscriber = subscriber_service.get(db, subscriber_id)
    if not subscriber:
        return RedirectResponse(url="/admin/subscribers", status_code=303)

    subscriber_service.link_to_person(db, subscriber, person_id)
    return RedirectResponse(
        url=f"/admin/subscribers/{subscriber_id}",
        status_code=303,
    )


def _subscriber_action_redirect(request: Request, url: str) -> Response:
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url=url, status_code=303)


@router.post("/{subscriber_id}/delete", response_class=HTMLResponse)
def subscriber_delete(
    request: Request,
    subscriber_id: UUID,
    db: Session = Depends(get_db),
):
    """Delete (deactivate) a subscriber."""
    subscriber = subscriber_service.get(db, subscriber_id)
    if subscriber:
        subscriber_service.delete(db, subscriber)
    return _subscriber_action_redirect(request, "/admin/subscribers")


@router.post("/{subscriber_id}/deactivate", response_class=HTMLResponse)
def subscriber_deactivate(
    request: Request,
    subscriber_id: UUID,
    db: Session = Depends(get_db),
):
    """Deactivate a subscriber (alias for delete soft-action)."""
    return subscriber_delete(request, subscriber_id, db)
