"""Admin subscriber web routes."""
import math
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.subscriber import SubscriberStatus
from app.services.subscriber import subscriber as subscriber_service
from app.services.person import people as person_service

router = APIRouter(prefix="/subscribers", tags=["admin-subscribers"])
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
def subscriber_list(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
):
    """List subscribers."""
    from app.web.admin import get_current_user
    from app.web.admin import get_sidebar_stats
    user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)

    status_filter = None
    if status:
        try:
            status_filter = SubscriberStatus(status)
        except ValueError:
            pass

    offset = (page - 1) * per_page

    subscribers = subscriber_service.list(
        db,
        search=search,
        status=status_filter,
        limit=per_page,
        offset=offset,
    )

    total = subscriber_service.count(
        db,
        search=search,
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
                "status": status,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
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
            "status": status,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "statuses": [s.value for s in SubscriberStatus],
        },
    )


@router.get("/new", response_class=HTMLResponse)
def subscriber_new(
    request: Request,
    db: Session = Depends(get_db),
):
    """Show subscriber creation form."""
    from app.web.admin import get_current_user
    from app.web.admin import get_sidebar_stats
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
    from app.web.admin import get_current_user
    from app.web.admin import get_sidebar_stats
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
        },
    )


@router.get("/{subscriber_id}/edit", response_class=HTMLResponse)
def subscriber_edit(
    request: Request,
    subscriber_id: UUID,
    db: Session = Depends(get_db),
):
    """Show subscriber edit form."""
    from app.web.admin import get_current_user
    from app.web.admin import get_sidebar_stats
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
