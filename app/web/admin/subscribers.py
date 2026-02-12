"""Admin subscriber web routes."""

import contextlib
import math
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.subscriber import AccountType, Organization, Subscriber, SubscriberStatus
from app.services.subscriber import subscriber as subscriber_service

router = APIRouter(prefix="/subscribers", tags=["admin-subscribers"])
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
def subscriber_list(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = None,
    external_system: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
):
    """List subscribers."""
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
        limit=per_page,
        offset=offset,
    )

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
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "statuses": [s.value for s in SubscriberStatus],
            "active_page": "subscribers",
        },
    )


@router.get("/resellers", response_class=HTMLResponse)
def reseller_organizations_list(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
):
    """List organizations configured as resellers."""
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
            "active_page": "subscribers",
        },
    )


@router.get("/resellers/{organization_id}", response_class=HTMLResponse)
def reseller_organization_subscribers(
    request: Request,
    organization_id: UUID,
    db: Session = Depends(get_db),
    page: int = 1,
    per_page: int = 20,
):
    """Show subscribers under a reseller organization hierarchy."""
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
    return RedirectResponse(url="/admin/subscribers", status_code=303)
