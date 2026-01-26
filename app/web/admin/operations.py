"""Admin operations web routes - service orders, dispatch, etc."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.catalog import Subscription
from app.models.notification import NotificationChannel, NotificationStatus
from app.models.person import Person
from app.models.provisioning import ServiceOrder
from app.models.sales_order import SalesOrderPaymentStatus, SalesOrderStatus
from app.models.person import ChannelType as PersonChannelType, Person, PersonChannel
from app.models.subscriber import AccountRole, SubscriberAccount
from app.services import provisioning as provisioning_service
from app.services import sales_orders as sales_orders_service
from app.services import subscriber as subscriber_service
from app.services import workforce as workforce_service
from app.services import dispatch as dispatch_service
from app.services import email as email_service
from app.services import notification as notification_service
from app.services import workflow as workflow_service
from app.services import audit as audit_service
from app.services.audit_helpers import (
    build_changes_metadata,
    extract_changes,
    format_changes,
    log_audit_event,
    recent_activity_for_paths,
)

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/operations", tags=["web-admin-operations"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _parse_datetime(value: str | None, field: str) -> datetime:
    if not value:
        raise ValueError(f"{field} is required")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_decimal(value: str | None, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {field}") from exc


def _notify_technician_assignment(db: Session, work_order, person_id: str) -> None:
    from app.schemas.notification import NotificationCreate

    person = db.get(Person, person_id)
    if not person:
        return

    app_url = email_service._get_app_url(db)
    work_order_url = f"{app_url}/admin/operations/work-orders/{work_order.id}"
    subject = f"New work order assigned: {work_order.title}"
    push_subject = "Work order assigned"

    scheduled = None
    if work_order.scheduled_start:
        scheduled = work_order.scheduled_start.strftime("%b %d, %Y %H:%M")

    body_lines = [
        f"Hello {person.first_name},",
        "",
        f"You have been assigned a work order: {work_order.title}.",
        f"Type: {work_order.work_type.value if work_order.work_type else 'N/A'}",
        f"Priority: {work_order.priority.value if work_order.priority else 'normal'}",
    ]
    if scheduled:
        body_lines.append(f"Scheduled: {scheduled}")
    body_lines.extend(["", f"View details: {work_order_url}"])
    body_text = "\n".join(body_lines)

    body_html = (
        f"<p>Hello {person.first_name},</p>"
        f"<p>You have been assigned a work order: <strong>{work_order.title}</strong>.</p>"
        f"<ul>"
        f"<li>Type: {work_order.work_type.value if work_order.work_type else 'N/A'}</li>"
        f"<li>Priority: {work_order.priority.value if work_order.priority else 'normal'}</li>"
        + (f"<li>Scheduled: {scheduled}</li>" if scheduled else "")
        + f"</ul>"
        f"<p><a href=\"{work_order_url}\">View work order</a></p>"
    )

    if person.email:
        email_service.send_email(
            db,
            person.email,
            subject,
            body_html,
            body_text,
        )

    notification_service.notifications.create(
        db,
        NotificationCreate(
            channel=NotificationChannel.push,
            recipient=str(person.id),
            subject=push_subject,
            body=f"You are assigned to {work_order.title}.",
            status=NotificationStatus.delivered,
        ),
    )


def _resolve_customer_contact(db: Session, work_order):
    account_id = work_order.account_id
    requested_contact = None
    subscription = None
    if not account_id and work_order.service_order_id:
        service_order = db.get(ServiceOrder, work_order.service_order_id)
        if service_order:
            account_id = service_order.account_id
            requested_contact = service_order.requested_by_contact
            subscription = service_order.subscription
    if not account_id and work_order.subscription_id:
        subscription = db.get(Subscription, work_order.subscription_id)
        if subscription:
            account_id = subscription.account_id
    account = db.get(SubscriberAccount, account_id) if account_id else None

    email = None
    person = None
    if requested_contact:
        person = requested_contact if isinstance(requested_contact, Person) else None
    if not person and account:
        role = (
            db.query(AccountRole)
            .filter(AccountRole.account_id == account.id)
            .order_by(AccountRole.is_primary.desc())
            .first()
        )
        if role and role.person:
            person = role.person
    if person:
        email = person.email
        if not email:
            channel = (
                db.query(PersonChannel)
                .filter(PersonChannel.person_id == person.id)
                .filter(PersonChannel.channel_type == PersonChannelType.email)
                .order_by(PersonChannel.is_primary.desc())
                .first()
            )
            if channel:
                email = channel.address

    if account and account.subscriber:
        person = account.subscriber.person
    if not person and subscription and subscription.account and subscription.account.subscriber:
        person = subscription.account.subscriber.person
    if not email and person:
        email = person.email

    return {
        "email": email,
        "person": person,
    }


def _notify_customer_assignment(db: Session, work_order, technician_person_id: str) -> None:
    from app.schemas.notification import NotificationCreate

    customer = _resolve_customer_contact(db, work_order)
    if not customer:
        return
    email = customer.get("email")
    person = customer.get("person")

    technician = db.get(Person, technician_person_id)
    technician_name = None
    if technician:
        technician_name = f"{technician.first_name} {technician.last_name}".strip()

    subject = "Technician assigned to your service request"
    body_lines = [
        "Hello,",
        "",
        f"A technician has been assigned to your work order: {work_order.title}.",
    ]
    if technician_name:
        body_lines.append(f"Technician: {technician_name}")
    if work_order.scheduled_start:
        body_lines.append(f"Scheduled: {work_order.scheduled_start.strftime('%b %d, %Y %H:%M')}")
    body_lines.append("")
    body_lines.append("We will notify you of any changes.")
    body_text = "\n".join(body_lines)

    body_html = (
        "<p>Hello,</p>"
        f"<p>A technician has been assigned to your work order: <strong>{work_order.title}</strong>.</p>"
        + (f"<p><strong>Technician:</strong> {technician_name}</p>" if technician_name else "")
        + (
            f"<p><strong>Scheduled:</strong> {work_order.scheduled_start.strftime('%b %d, %Y %H:%M')}</p>"
            if work_order.scheduled_start
            else ""
        )
        + "<p>We will notify you of any changes.</p>"
    )

    if email:
        email_service.send_email(
            db,
            email,
            subject,
            body_html,
            body_text,
        )

    if person:
        notification_service.notifications.create(
            db,
            NotificationCreate(
                channel=NotificationChannel.push,
                recipient=str(person.id),
                subject="Technician assigned",
                body=f"A technician is assigned to your work order: {work_order.title}.",
                status=NotificationStatus.delivered,
            ),
        )


@router.get("/sales-orders", response_class=HTMLResponse)
def sales_orders(request: Request, db: Session = Depends(get_db)):
    orders = sales_orders_service.sales_orders.list(
        db=db,
        person_id=None,
        account_id=None,
        quote_id=None,
        status=None,
        payment_status=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=100,
        offset=0,
    )

    def _status_value(obj):
        status = getattr(obj, "status", None)
        return status.value if status else ""

    def _payment_value(obj):
        status = getattr(obj, "payment_status", None)
        return status.value if status else ""

    stats = {
        "total": len(orders),
        "draft": sum(1 for o in orders if _status_value(o) == SalesOrderStatus.draft.value),
        "confirmed": sum(
            1 for o in orders if _status_value(o) == SalesOrderStatus.confirmed.value
        ),
        "paid": sum(1 for o in orders if _status_value(o) == SalesOrderStatus.paid.value),
        "fulfilled": sum(
            1 for o in orders if _status_value(o) == SalesOrderStatus.fulfilled.value
        ),
        "pending_payment": sum(
            1
            for o in orders
            if _payment_value(o) == SalesOrderPaymentStatus.pending.value
        ),
    }

    from app.web.admin import get_sidebar_stats, get_current_user

    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    return templates.TemplateResponse(
        "admin/operations/sales-orders.html",
        {
            "request": request,
            "orders": orders,
            "stats": stats,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "active_page": "sales-orders",
        },
    )


@router.get("/sales-orders/{order_id}", response_class=HTMLResponse)
def sales_order_detail(request: Request, order_id: str, db: Session = Depends(get_db)):
    order = sales_orders_service.sales_orders.get(db=db, sales_order_id=order_id)

    from app.web.admin import get_sidebar_stats, get_current_user

    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    return templates.TemplateResponse(
        "admin/operations/sales_order_detail.html",
        {
            "request": request,
            "order": order,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "active_page": "sales-orders",
        },
    )


def _get_account_label(account) -> str | None:
    """Build label for account typeahead pre-population."""
    if not account:
        return None
    if account.subscriber and account.subscriber.person:
        if account.subscriber.person.organization:
            base = account.subscriber.person.organization.name
        else:
            base = f"{account.subscriber.person.first_name} {account.subscriber.person.last_name}"
    else:
        base = "Account"
    if account.account_number:
        return f"{base} ({account.account_number})"
    return base


def _get_subscription_label(subscription, account_label: str | None = None) -> str | None:
    if not subscription:
        return None
    offer_name = subscription.offer.name if getattr(subscription, "offer", None) else "Subscription"
    if account_label:
        return f"{offer_name} - {account_label}"
    return offer_name


@router.get("/sales-orders/{order_id}/edit", response_class=HTMLResponse)
def sales_order_edit(request: Request, order_id: str, db: Session = Depends(get_db)):
    order = sales_orders_service.sales_orders.get(db=db, sales_order_id=order_id)

    # Get account label for typeahead
    account_label = _get_account_label(order.account) if order and order.account else None

    from app.web.admin import get_sidebar_stats, get_current_user

    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    return templates.TemplateResponse(
        "admin/operations/sales_order_form.html",
        {
            "request": request,
            "order": order,
            "account_label": account_label,
            "statuses": [status.value for status in SalesOrderStatus],
            "payment_statuses": [status.value for status in SalesOrderPaymentStatus],
            "action_url": f"/admin/operations/sales-orders/{order_id}/edit",
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "active_page": "sales-orders",
        },
    )


@router.post("/sales-orders/{order_id}/edit", response_class=HTMLResponse)
def sales_order_update(
    request: Request,
    order_id: str,
    status: str | None = Form(None),
    payment_status: str | None = Form(None),
    account_id: str | None = Form(None),
    total: str | None = Form(None),
    amount_paid: str | None = Form(None),
    paid_at: str | None = Form(None),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    error = None
    order = sales_orders_service.sales_orders.get(db=db, sales_order_id=order_id)
    try:
        from app.schemas.sales_order import SalesOrderUpdate

        before = order
        normalized_status = (status or "").strip() or None
        normalized_payment = (payment_status or "").strip() or None
        resolved_paid_at = _parse_optional_datetime(paid_at)
        resolved_total = _parse_decimal(total, "total")
        resolved_amount_paid = _parse_decimal(amount_paid, "amount_paid")
        if normalized_payment == SalesOrderPaymentStatus.paid.value and not resolved_paid_at:
            resolved_paid_at = datetime.now(timezone.utc)
        if normalized_payment == SalesOrderPaymentStatus.paid.value and normalized_status in (None, "draft", "confirmed"):
            normalized_status = SalesOrderStatus.paid.value
        if normalized_payment == SalesOrderPaymentStatus.paid.value and resolved_amount_paid is None:
            resolved_amount_paid = resolved_total if resolved_total is not None else (order.total or Decimal("0.00"))

        payload = SalesOrderUpdate(
            status=normalized_status,
            payment_status=normalized_payment,
            account_id=account_id or None,
            total=resolved_total,
            amount_paid=resolved_amount_paid,
            paid_at=resolved_paid_at,
            notes=notes.strip() if notes else None,
        )
        order = sales_orders_service.sales_orders.update(
            db=db, sales_order_id=order_id, payload=payload
        )
        from app.web.admin import get_current_user
        current_user = get_current_user(request)
        metadata_payload = build_changes_metadata(before, order)
        log_audit_event(
            db=db,
            request=request,
            action="update",
            entity_type="sales_order",
            entity_id=str(order_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata=metadata_payload,
        )
        return RedirectResponse(
            url=f"/admin/operations/sales-orders/{order.id}", status_code=303
        )
    except (ValueError, ValidationError) as exc:
        error = str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    # Get account label for typeahead
    account_label = _get_account_label(order.account) if order and order.account else None

    from app.web.admin import get_sidebar_stats, get_current_user

    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    return templates.TemplateResponse(
        "admin/operations/sales_order_form.html",
        {
            "request": request,
            "order": order,
            "account_label": account_label,
            "statuses": [status.value for status in SalesOrderStatus],
            "payment_statuses": [status.value for status in SalesOrderPaymentStatus],
            "action_url": f"/admin/operations/sales-orders/{order_id}/edit",
            "error": error,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "active_page": "sales-orders",
        },
        status_code=400,
    )


@router.get("/service-orders", response_class=HTMLResponse)
def service_orders(request: Request, db: Session = Depends(get_db)):
    """Service orders management page."""
    # Get service orders
    orders = provisioning_service.service_orders.list(
        db=db,
        account_id=None,
        subscription_id=None,
        status=None,
        order_by="created_at",
        order_dir="desc",
        limit=100,
        offset=0,
    )

    def get_status(obj):
        status = getattr(obj, "status", "")
        return status.value if hasattr(status, "value") else str(status)

    # Calculate stats
    draft = sum(1 for o in orders if get_status(o) == "draft")
    submitted = sum(1 for o in orders if get_status(o) == "submitted")
    scheduled = sum(1 for o in orders if get_status(o) == "scheduled")
    provisioning = sum(1 for o in orders if get_status(o) == "provisioning")
    active = sum(1 for o in orders if get_status(o) == "active")

    stats = {
        "draft": draft,
        "submitted": submitted,
        "scheduled": scheduled,
        "provisioning": provisioning,
        "active": active,
    }

    # Get sidebar stats and current user
    from app.web.admin import get_sidebar_stats, get_current_user
    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    return templates.TemplateResponse(
        "admin/operations/service-orders.html",
        {
            "request": request,
            "orders": orders,
            "stats": stats,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
        },
    )


@router.get("/service-orders/new", response_class=HTMLResponse)
def service_order_new(
    request: Request,
    db: Session = Depends(get_db),
    account_id: str | None = Query(None),
    subscription_id: str | None = Query(None),
    project_type: str | None = Query(None),
    requested_by_contact_id: str | None = Query(None),
):
    from app.web.admin import get_sidebar_stats, get_current_user
    from app.services import catalog as catalog_service
    from app.services import subscriber as subscriber_service
    from app.models.projects import ProjectType
    from app.services.person import people as person_service

    # Get people
    contacts = person_service.list(
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
    project_types = [project_type.value for project_type in ProjectType]

    prefill = {
        "account_id": account_id,
        "subscription_id": subscription_id,
        "project_type": project_type,
        "requested_by_contact_id": requested_by_contact_id,
    }

    account_label = None
    subscription_label = None
    if subscription_id:
        try:
            subscription = catalog_service.subscriptions.get(db=db, subscription_id=subscription_id)
            account = subscriber_service.accounts.get(db=db, account_id=subscription.account_id)
            account_label = _get_account_label(account)
            subscription_label = _get_subscription_label(subscription, account_label)
            prefill["account_id"] = str(subscription.account_id)
        except Exception:
            pass
    elif account_id:
        try:
            from uuid import UUID
            account = subscriber_service.accounts.get(db=db, account_id=UUID(account_id))
            account_label = _get_account_label(account)
        except Exception:
            pass

    return templates.TemplateResponse(
        "admin/operations/service_order_form.html",
        {
            "request": request,
            "order": None,
            "project_types": project_types,
            "prefill": prefill,
            "action_url": "/admin/operations/service-orders",
            "active_page": "service-orders",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "account_label": account_label,
            "subscription_label": subscription_label,
        },
    )


@router.post("/service-orders", response_class=HTMLResponse)
def service_order_create(
    request: Request,
    account_id: str | None = Form(None),
    subscription_id: str | None = Form(None),
    requested_by_contact_id: str | None = Form(None),
    project_type: str | None = Form(None),
    status: str = Form("draft"),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Create a new service order."""
    from app.web.admin import get_sidebar_stats, get_current_user
    from app.schemas.provisioning import ServiceOrderCreate
    from app.models.provisioning import ServiceOrderStatus
    from app.models.projects import ProjectType
    from app.services import catalog as catalog_service
    from app.services import subscriber as subscriber_service
    from uuid import UUID
    from fastapi.responses import RedirectResponse
    from app.services.person import people as person_service

    try:
        status_map = {
            "draft": ServiceOrderStatus.draft,
            "submitted": ServiceOrderStatus.submitted,
            "scheduled": ServiceOrderStatus.scheduled,
            "provisioning": ServiceOrderStatus.provisioning,
            "active": ServiceOrderStatus.active,
            "canceled": ServiceOrderStatus.canceled,
            "failed": ServiceOrderStatus.failed,
        }

        subscription_id = subscription_id if subscription_id not in {"None", "none", "null"} else None
        requested_by_contact_id = (
            requested_by_contact_id
            if requested_by_contact_id not in {"None", "none", "null"}
            else None
        )

        if (not account_id or account_id in {"None", "none", "null"}) and subscription_id:
            subscription = catalog_service.subscriptions.get(db=db, subscription_id=subscription_id)
            account_id = str(subscription.account_id)
        if not account_id or account_id in {"None", "none", "null"}:
            raise ValueError("account_id is required")

        payload = ServiceOrderCreate(
            account_id=UUID(account_id),
            subscription_id=UUID(subscription_id) if subscription_id else None,
            requested_by_contact_id=UUID(requested_by_contact_id) if requested_by_contact_id else None,
            status=status_map.get(status, ServiceOrderStatus.draft),
            project_type=ProjectType(project_type) if project_type else None,
            notes=notes if notes else None,
        )
        order = provisioning_service.service_orders.create(db=db, payload=payload)
        current_user = get_current_user(request)
        log_audit_event(
            db=db,
            request=request,
            action="create",
            entity_type="service_order",
            entity_id=str(order.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"status": order.status.value if order.status else None},
        )
        return RedirectResponse(url=f"/admin/operations/service-orders/{order.id}", status_code=303)
    except Exception as e:
        # Re-fetch data for form
        contacts = person_service.list(
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
        project_types = [project_type.value for project_type in ProjectType]

        account_label = None
        subscription_label = None
        if subscription_id:
            try:
                subscription = catalog_service.subscriptions.get(db=db, subscription_id=subscription_id)
                account = subscriber_service.accounts.get(db=db, account_id=subscription.account_id)
                account_label = _get_account_label(account)
                subscription_label = _get_subscription_label(subscription, account_label)
            except Exception:
                pass
        elif account_id:
            try:
                account = subscriber_service.accounts.get(db=db, account_id=UUID(account_id))
                account_label = _get_account_label(account)
            except Exception:
                pass

        return templates.TemplateResponse(
            "admin/operations/service_order_form.html",
            {
                "request": request,
                "order": None,
                "project_types": project_types,
                "action_url": "/admin/operations/service-orders",
                "error": str(e),
                "active_page": "service-orders",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
                "account_label": account_label,
                "subscription_label": subscription_label,
            },
            status_code=400,
        )


@router.get("/service-orders/{order_id}", response_class=HTMLResponse)
def service_order_detail(request: Request, order_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        order = provisioning_service.service_orders.get(db=db, order_id=order_id)
    except Exception:
        from app.web.admin import get_sidebar_stats, get_current_user
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Service order not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    # Get appointments
    appointments = provisioning_service.install_appointments.list(
        db=db,
        service_order_id=order_id,
        status=None,
        order_by="scheduled_start",
        order_dir="asc",
        limit=50,
        offset=0,
    )

    # Get tasks
    tasks = provisioning_service.provisioning_tasks.list(
        db=db,
        service_order_id=order_id,
        status=None,
        order_by="created_at",
        order_dir="asc",
        limit=50,
        offset=0,
    )
    audit_events = audit_service.audit_events.list(
        db=db,
        actor_id=None,
        actor_type=None,
        action=None,
        entity_type="service_order",
        entity_id=str(order_id),
        request_id=None,
        is_success=None,
        status_code=None,
        is_active=None,
        order_by="occurred_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    actor_ids = {str(event.actor_id) for event in audit_events if getattr(event, "actor_id", None)}
    people = {}
    if actor_ids:
        people = {
            str(person.id): person
            for person in db.query(Person).filter(Person.id.in_(actor_ids)).all()
        }
    activities = []
    for event in audit_events:
        actor = people.get(str(event.actor_id)) if getattr(event, "actor_id", None) else None
        actor_name = f"{actor.first_name} {actor.last_name}".strip() if actor else "System"
        metadata = getattr(event, "metadata_", None) or {}
        changes = extract_changes(metadata, getattr(event, "action", None))
        change_summary = format_changes(changes, max_items=2)
        activities.append(
            {
                "title": (event.action or "Activity").replace("_", " ").title(),
                "description": f"{actor_name}" + (f" · {change_summary}" if change_summary else ""),
                "occurred_at": event.occurred_at,
            }
        )

    return templates.TemplateResponse(
        "admin/operations/service_order_detail.html",
        {
            "request": request,
            "order": order,
            "appointments": appointments,
            "tasks": tasks,
            "activities": activities,
            "active_page": "service-orders",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/service-orders/{order_id}/edit", response_class=HTMLResponse)
def service_order_edit(request: Request, order_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user
    from app.services import catalog as catalog_service
    from app.services import subscriber as subscriber_service
    from app.models.projects import ProjectType
    from app.services.person import people as person_service

    try:
        order = provisioning_service.service_orders.get(db=db, order_id=order_id)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Service order not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    contacts = person_service.list(
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
    project_types = [project_type.value for project_type in ProjectType]

    # Build account label for typeahead pre-population
    account_label = _get_account_label(order.account) if order and order.account else None
    subscription_label = _get_subscription_label(order.subscription, account_label) if order and order.subscription else None

    return templates.TemplateResponse(
        "admin/operations/service_order_form.html",
        {
            "request": request,
            "order": order,
            "project_types": project_types,
            "action_url": f"/admin/operations/service-orders/{order.id}/edit",
            "active_page": "service-orders",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "account_label": account_label,
            "subscription_label": subscription_label,
        },
    )


@router.get("/subscription-account")
def subscription_account_lookup(
    subscription_id: str,
    db: Session = Depends(get_db),
):
    from fastapi.responses import JSONResponse
    from app.services import catalog as catalog_service
    from app.services import subscriber as subscriber_service

    try:
        subscription = catalog_service.subscriptions.get(db=db, subscription_id=subscription_id)
        account = subscriber_service.accounts.get(db=db, account_id=str(subscription.account_id))
        return JSONResponse(
            {
                "account_id": str(subscription.account_id),
                "account_label": _get_account_label(account),
            }
        )
    except Exception:
        return JSONResponse({"account_id": None, "account_label": None}, status_code=404)


@router.post("/service-orders/{order_id}/edit", response_class=HTMLResponse)
def service_order_update(
    request: Request,
    order_id: str,
    account_id: str | None = Form(None),
    subscription_id: str | None = Form(None),
    requested_by_contact_id: str | None = Form(None),
    project_type: str | None = Form(None),
    status: str = Form("draft"),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user
    from app.schemas.provisioning import ServiceOrderUpdate
    from app.models.provisioning import ServiceOrderStatus
    from app.models.projects import ProjectType
    from app.services import catalog as catalog_service
    from app.services import subscriber as subscriber_service
    from uuid import UUID
    from app.services.person import people as person_service

    try:
        order = provisioning_service.service_orders.get(db=db, order_id=order_id)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Service order not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    try:
        before = order
        status_map = {
            "draft": ServiceOrderStatus.draft,
            "submitted": ServiceOrderStatus.submitted,
            "scheduled": ServiceOrderStatus.scheduled,
            "provisioning": ServiceOrderStatus.provisioning,
            "active": ServiceOrderStatus.active,
            "canceled": ServiceOrderStatus.canceled,
            "failed": ServiceOrderStatus.failed,
        }
        if (not account_id or account_id in {"None", "none", "null"}) and subscription_id:
            subscription = catalog_service.subscriptions.get(db=db, subscription_id=subscription_id)
            account_id = str(subscription.account_id)
        if not account_id or account_id in {"None", "none", "null"}:
            raise ValueError("account_id is required")

        subscription_id = subscription_id if subscription_id not in {"None", "none", "null"} else None
        requested_by_contact_id = (
            requested_by_contact_id
            if requested_by_contact_id not in {"None", "none", "null"}
            else None
        )

        payload = ServiceOrderUpdate(
            account_id=UUID(account_id),
            subscription_id=UUID(subscription_id) if subscription_id else None,
            requested_by_contact_id=UUID(requested_by_contact_id) if requested_by_contact_id else None,
            status=status_map.get(status, ServiceOrderStatus.draft),
            project_type=ProjectType(project_type) if project_type else None,
            notes=notes if notes else None,
        )
        provisioning_service.service_orders.update(
            db=db,
            order_id=order_id,
            payload=payload,
        )
        after = provisioning_service.service_orders.get(db=db, order_id=order_id)
        metadata_payload = build_changes_metadata(before, after)
        current_user = get_current_user(request)
        log_audit_event(
            db=db,
            request=request,
            action="update",
            entity_type="service_order",
            entity_id=str(order_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata=metadata_payload,
        )
        return RedirectResponse(
            url=f"/admin/operations/service-orders/{order.id}",
            status_code=303,
        )
    except Exception as e:
        contacts = person_service.list(
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
        project_types = [project_type.value for project_type in ProjectType]

        # Build account label for typeahead pre-population
        account_label = _get_account_label(order.account) if order and order.account else None
        subscription_label = _get_subscription_label(order.subscription, account_label) if order and order.subscription else None

        return templates.TemplateResponse(
            "admin/operations/service_order_form.html",
            {
                "request": request,
                "order": order,
                "project_types": project_types,
                "action_url": f"/admin/operations/service-orders/{order.id}/edit",
                "error": str(e),
                "active_page": "service-orders",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
                "account_label": account_label,
                "subscription_label": subscription_label,
            },
            status_code=400,
        )


@router.get("/service-orders/{order_id}/appointments/new", response_class=HTMLResponse)
def service_order_appointment_new(
    request: Request,
    order_id: str,
    db: Session = Depends(get_db),
):
    from app.models.provisioning import AppointmentStatus
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        order = provisioning_service.service_orders.get(db=db, order_id=order_id)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Service order not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )

    return templates.TemplateResponse(
        "admin/operations/appointment_form.html",
        {
            "request": request,
            "order": order,
            "appointment": None,
            "statuses": [status.value for status in AppointmentStatus],
            "technicians": technicians,
            "action_url": f"/admin/operations/service-orders/{order_id}/appointments",
            "active_page": "service-orders",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/service-orders/{order_id}/appointments/{appointment_id}", response_class=HTMLResponse)
def service_order_appointment_detail(
    request: Request,
    order_id: str,
    appointment_id: str,
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        order = provisioning_service.service_orders.get(db=db, order_id=order_id)
        appointment = provisioning_service.install_appointments.get(
            db=db, appointment_id=appointment_id
        )
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Install appointment not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    if str(appointment.service_order_id) != str(order.id):
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Install appointment not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    return templates.TemplateResponse(
        "admin/operations/appointment_detail.html",
        {
            "request": request,
            "order": order,
            "appointment": appointment,
            "active_page": "service-orders",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/service-orders/{order_id}/appointments/{appointment_id}/edit", response_class=HTMLResponse)
def service_order_appointment_edit(
    request: Request,
    order_id: str,
    appointment_id: str,
    db: Session = Depends(get_db),
):
    from app.models.provisioning import AppointmentStatus
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        order = provisioning_service.service_orders.get(db=db, order_id=order_id)
        appointment = provisioning_service.install_appointments.get(
            db=db, appointment_id=appointment_id
        )
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Install appointment not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    if str(appointment.service_order_id) != str(order.id):
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Install appointment not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )

    return templates.TemplateResponse(
        "admin/operations/appointment_form.html",
        {
            "request": request,
            "order": order,
            "appointment": appointment,
            "statuses": [status.value for status in AppointmentStatus],
            "technicians": technicians,
            "action_url": f"/admin/operations/service-orders/{order_id}/appointments/{appointment_id}/edit",
            "active_page": "service-orders",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/service-orders/{order_id}/appointments/{appointment_id}/edit", response_class=HTMLResponse)
def service_order_appointment_update(
    request: Request,
    order_id: str,
    appointment_id: str,
    scheduled_start: str = Form(...),
    scheduled_end: str = Form(...),
    technician: str | None = Form(None),
    status: str | None = Form(None),
    notes: str | None = Form(None),
    is_self_install: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.models.provisioning import AppointmentStatus
    from app.schemas.provisioning import InstallAppointmentUpdate
    from app.schemas.workforce import WorkOrderUpdate
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        order = provisioning_service.service_orders.get(db=db, order_id=order_id)
        appointment = provisioning_service.install_appointments.get(
            db=db, appointment_id=appointment_id
        )
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Install appointment not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    if str(appointment.service_order_id) != str(order.id):
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Install appointment not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    try:
        start_dt = _parse_datetime(scheduled_start, "scheduled_start")
        end_dt = _parse_datetime(scheduled_end, "scheduled_end")
        if end_dt <= start_dt:
            raise ValueError("scheduled_end must be after scheduled_start")
        appt_status = AppointmentStatus(status) if status else AppointmentStatus.proposed
        technician_label = None
        technician_person_id = None
        if technician:
            try:
                technician_person_id = UUID(technician)
            except ValueError:
                technician_label = technician.strip()
            else:
                person = db.get(Person, technician_person_id)
                if person:
                    technician_label = f"{person.first_name} {person.last_name}"
        data = InstallAppointmentUpdate(
            service_order_id=order.id,
            scheduled_start=start_dt,
            scheduled_end=end_dt,
            technician=technician_label,
            status=appt_status,
            notes=notes.strip() if notes else None,
            is_self_install=is_self_install == "true",
        )
        provisioning_service.install_appointments.update(
            db=db, appointment_id=appointment_id, payload=data
        )
        work_orders = workforce_service.work_orders.list(
            db=db,
            account_id=None,
            subscription_id=None,
            service_order_id=str(order.id),
            ticket_id=None,
            project_id=None,
            assigned_to_person_id=None,
            status=None,
            priority=None,
            work_type=None,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=200,
            offset=0,
        )
        linked = next(
            (
                wo
                for wo in work_orders
                if wo.metadata_
                and str(wo.metadata_.get("install_appointment_id")) == str(appointment.id)
            ),
            None,
        )
        if linked:
            work_order_update = WorkOrderUpdate(
                scheduled_start=start_dt,
                scheduled_end=end_dt,
                description=notes.strip() if notes else None,
                assigned_to_person_id=technician_person_id,
            )
            workforce_service.work_orders.update(
                db=db, work_order_id=str(linked.id), payload=work_order_update
            )
        return RedirectResponse(
            url=f"/admin/operations/service-orders/{order_id}",
            status_code=303,
        )
    except Exception as e:
        technicians = dispatch_service.technicians.list(
            db=db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="desc",
            limit=500,
            offset=0,
        )
        return templates.TemplateResponse(
            "admin/operations/appointment_form.html",
            {
                "request": request,
                "order": order,
                "appointment": appointment,
                "error": str(e),
                "form": {
                    "scheduled_start": scheduled_start or "",
                    "scheduled_end": scheduled_end or "",
                    "technician": technician or "",
                    "status": status or "",
                    "notes": notes or "",
                    "is_self_install": is_self_install == "true",
                },
                "statuses": [status.value for status in AppointmentStatus],
                "technicians": technicians,
                "action_url": f"/admin/operations/service-orders/{order_id}/appointments/{appointment_id}/edit",
                "active_page": "service-orders",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )


@router.delete("/service-orders/{order_id}/appointments/{appointment_id}", response_class=HTMLResponse)
@router.post("/service-orders/{order_id}/appointments/{appointment_id}/delete", response_class=HTMLResponse)
def service_order_appointment_delete(
    request: Request,
    order_id: str,
    appointment_id: str,
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        order = provisioning_service.service_orders.get(db=db, order_id=order_id)
        appointment = provisioning_service.install_appointments.get(
            db=db, appointment_id=appointment_id
        )
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Install appointment not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    if str(appointment.service_order_id) != str(order.id):
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Install appointment not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    work_orders = workforce_service.work_orders.list(
        db=db,
        account_id=None,
        subscription_id=None,
        service_order_id=str(order.id),
        ticket_id=None,
        project_id=None,
        assigned_to_person_id=None,
        status=None,
        priority=None,
        work_type=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    linked = next(
        (
            wo
            for wo in work_orders
            if wo.metadata_
            and str(wo.metadata_.get("install_appointment_id")) == str(appointment.id)
        ),
        None,
    )
    if linked:
        workforce_service.work_orders.delete(db=db, work_order_id=str(linked.id))

    provisioning_service.install_appointments.delete(
        db=db, appointment_id=appointment_id
    )
    return RedirectResponse(
        url=f"/admin/operations/service-orders/{order_id}",
        status_code=303,
    )

@router.post("/service-orders/{order_id}/appointments", response_class=HTMLResponse)
def service_order_appointment_create(
    request: Request,
    order_id: str,
    scheduled_start: str = Form(...),
    scheduled_end: str = Form(...),
    technician: str | None = Form(None),
    status: str | None = Form(None),
    notes: str | None = Form(None),
    is_self_install: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.models.provisioning import AppointmentStatus
    from app.models.workforce import WorkOrderStatus, WorkOrderType
    from app.schemas.provisioning import InstallAppointmentCreate
    from app.schemas.workforce import WorkOrderCreate
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        order = provisioning_service.service_orders.get(db=db, order_id=order_id)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {
                "request": request,
                "message": "Service order not found",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=404,
        )

    try:
        start_dt = _parse_datetime(scheduled_start, "scheduled_start")
        end_dt = _parse_datetime(scheduled_end, "scheduled_end")
        if end_dt <= start_dt:
            raise ValueError("scheduled_end must be after scheduled_start")
        appt_status = AppointmentStatus(status) if status else AppointmentStatus.proposed
        technician_label = None
        technician_person_id = None
        if technician:
            try:
                technician_person_id = UUID(technician)
            except ValueError:
                technician_label = technician.strip()
            else:
                person = db.get(Person, technician_person_id)
                if person:
                    technician_label = f"{person.first_name} {person.last_name}"
        data = InstallAppointmentCreate(
            service_order_id=order.id,
            scheduled_start=start_dt,
            scheduled_end=end_dt,
            technician=technician_label,
            status=appt_status,
            notes=notes.strip() if notes else None,
            is_self_install=is_self_install == "true",
        )
        appointment = provisioning_service.install_appointments.create(db=db, payload=data)
        work_order = WorkOrderCreate(
            title=f"Install - Service Order {str(order.id)[:8]}",
            description=notes.strip() if notes else None,
            status=WorkOrderStatus.scheduled,
            work_type=WorkOrderType.install,
            account_id=order.account_id,
            subscription_id=order.subscription_id,
            service_order_id=order.id,
            scheduled_start=start_dt,
            scheduled_end=end_dt,
            assigned_to_person_id=technician_person_id,
            metadata_={"install_appointment_id": str(appointment.id)},
        )
        workforce_service.work_orders.create(db=db, payload=work_order)
        return RedirectResponse(
            url=f"/admin/operations/service-orders/{order_id}",
            status_code=303,
        )
    except Exception as e:
        technicians = dispatch_service.technicians.list(
            db=db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="desc",
            limit=500,
            offset=0,
        )
        return templates.TemplateResponse(
            "admin/operations/appointment_form.html",
            {
                "request": request,
                "order": order,
                "error": str(e),
                "form": {
                    "scheduled_start": scheduled_start or "",
                    "scheduled_end": scheduled_end or "",
                    "technician": technician or "",
                    "status": status or "",
                    "notes": notes or "",
                    "is_self_install": is_self_install == "true",
                },
                "statuses": [status.value for status in AppointmentStatus],
                "technicians": technicians,
                "action_url": f"/admin/operations/service-orders/{order_id}/appointments",
                "active_page": "service-orders",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )


@router.post("/service-orders/{order_id}/submit", response_class=HTMLResponse)
def service_order_submit(request: Request, order_id: str, db: Session = Depends(get_db)):
    """Submit a service order."""
    from app.schemas.provisioning import ServiceOrderUpdate
    from app.models.provisioning import ServiceOrderStatus
    from fastapi.responses import RedirectResponse
    from app.web.admin import get_current_user

    before = provisioning_service.service_orders.get(db=db, order_id=order_id)
    provisioning_service.service_orders.update(
        db=db,
        order_id=order_id,
        payload=ServiceOrderUpdate(status=ServiceOrderStatus.submitted),
    )
    after = provisioning_service.service_orders.get(db=db, order_id=order_id)
    metadata_payload = build_changes_metadata(before, after)
    current_user = get_current_user(request)
    log_audit_event(
        db=db,
        request=request,
        action="status_change",
        entity_type="service_order",
        entity_id=str(order_id),
        actor_id=str(current_user.get("person_id")) if current_user else None,
        metadata=metadata_payload or {"from": before.status.value if before.status else None, "to": "submitted"},
    )
    return RedirectResponse(url=f"/admin/operations/service-orders/{order_id}", status_code=303)


@router.post("/service-orders/{order_id}/provision", response_class=HTMLResponse)
def service_order_provision(request: Request, order_id: str, db: Session = Depends(get_db)):
    """Start provisioning a service order."""
    from app.schemas.provisioning import ServiceOrderUpdate
    from app.models.provisioning import ServiceOrderStatus
    from fastapi.responses import RedirectResponse
    from app.web.admin import get_current_user

    before = provisioning_service.service_orders.get(db=db, order_id=order_id)
    provisioning_service.service_orders.update(
        db=db,
        order_id=order_id,
        payload=ServiceOrderUpdate(status=ServiceOrderStatus.provisioning),
    )
    after = provisioning_service.service_orders.get(db=db, order_id=order_id)
    metadata_payload = build_changes_metadata(before, after)
    current_user = get_current_user(request)
    log_audit_event(
        db=db,
        request=request,
        action="status_change",
        entity_type="service_order",
        entity_id=str(order_id),
        actor_id=str(current_user.get("person_id")) if current_user else None,
        metadata=metadata_payload or {"from": before.status.value if before.status else None, "to": "provisioning"},
    )
    return RedirectResponse(url=f"/admin/operations/service-orders/{order_id}", status_code=303)


@router.post("/service-orders/{order_id}/cancel", response_class=HTMLResponse)
def service_order_cancel(request: Request, order_id: str, db: Session = Depends(get_db)):
    """Cancel a service order."""
    from app.schemas.provisioning import ServiceOrderUpdate
    from app.models.provisioning import ServiceOrderStatus
    from fastapi.responses import RedirectResponse
    from app.web.admin import get_current_user

    before = provisioning_service.service_orders.get(db=db, order_id=order_id)
    provisioning_service.service_orders.update(
        db=db,
        order_id=order_id,
        payload=ServiceOrderUpdate(status=ServiceOrderStatus.canceled),
    )
    after = provisioning_service.service_orders.get(db=db, order_id=order_id)
    metadata_payload = build_changes_metadata(before, after)
    current_user = get_current_user(request)
    log_audit_event(
        db=db,
        request=request,
        action="status_change",
        entity_type="service_order",
        entity_id=str(order_id),
        actor_id=str(current_user.get("person_id")) if current_user else None,
        metadata=metadata_payload or {"from": before.status.value if before.status else None, "to": "canceled"},
    )
    return RedirectResponse(url=f"/admin/operations/service-orders/{order_id}", status_code=303)


@router.get("/installations", response_class=HTMLResponse)
def installations_list(
    request: Request,
    status: str | None = None,
    page: int = 1,
    per_page: int = 25,
    db: Session = Depends(get_db),
):
    """Install appointments page."""
    offset = (page - 1) * per_page
    appointments = provisioning_service.install_appointments.list(
        db=db,
        service_order_id=None,
        status=status if status else None,
        order_by="scheduled_start",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )
    all_appointments = provisioning_service.install_appointments.list(
        db=db,
        service_order_id=None,
        status=status if status else None,
        order_by="scheduled_start",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    total = len(all_appointments)
    total_pages = (total + per_page - 1) // per_page if total else 1

    from app.web.admin import get_sidebar_stats, get_current_user
    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    return templates.TemplateResponse(
        "admin/operations/installations.html",
        {
            "request": request,
            "appointments": appointments,
            "status": status,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
        },
    )


@router.get("/work-orders", response_class=HTMLResponse)
def work_orders_list(
    request: Request,
    status: str | None = None,
    priority: str | None = None,
    assigned: str | None = None,
    scheduled: str | None = None,
    page: int = 1,
    per_page: int = 25,
    db: Session = Depends(get_db),
):
    """Field service work orders page."""
    from datetime import date

    offset = (page - 1) * per_page
    all_work_orders = workforce_service.work_orders.list(
        db=db,
        account_id=None,
        subscription_id=None,
        service_order_id=None,
        ticket_id=None,
        project_id=None,
        assigned_to_person_id=None,
        status=status if status else None,
        priority=priority if priority else None,
        work_type=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    filtered = all_work_orders
    if assigned == "unassigned":
        filtered = [wo for wo in filtered if not wo.assigned_to_person_id]
    elif assigned == "assigned":
        filtered = [wo for wo in filtered if wo.assigned_to_person_id]
    if scheduled == "today":
        today = date.today()
        filtered = [
            wo
            for wo in filtered
            if wo.scheduled_start and wo.scheduled_start.date() == today
        ]
    total = len(filtered)
    total_pages = (total + per_page - 1) // per_page if total else 1
    work_orders = filtered[offset:offset + per_page]

    from app.models.workforce import WorkOrderPriority, WorkOrderStatus, WorkOrderType
    from app.web.admin import get_sidebar_stats, get_current_user
    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)
    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )

    return templates.TemplateResponse(
        "admin/operations/work-orders.html",
        {
            "request": request,
            "work_orders": work_orders,
            "technicians": technicians,
            "status_options": [status.value for status in WorkOrderStatus],
            "priority_options": [value.value for value in WorkOrderPriority],
            "type_options": [value.value for value in WorkOrderType],
            "status": status,
            "priority": priority,
            "assigned": assigned,
            "scheduled": scheduled,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
        },
    )


@router.get("/work-orders/new", response_class=HTMLResponse)
def work_order_new(request: Request, db: Session = Depends(get_db)):
    from app.models.workforce import WorkOrderPriority, WorkOrderStatus, WorkOrderType
    from app.web.admin import get_sidebar_stats, get_current_user

    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    form = {
        "title": "",
        "description": "",
        "status": WorkOrderStatus.draft.value,
        "priority": WorkOrderPriority.normal.value,
        "work_type": WorkOrderType.install.value,
        "assigned_to_person_id": "",
        "scheduled_start": "",
        "scheduled_end": "",
    }
    return templates.TemplateResponse(
        "admin/operations/work_order_form.html",
        {
            "request": request,
            "work_order": None,
            "technicians": technicians,
            "status_options": [value.value for value in WorkOrderStatus],
            "priority_options": [value.value for value in WorkOrderPriority],
            "type_options": [value.value for value in WorkOrderType],
            "form": form,
            "is_new": True,
            "form_action": "/admin/operations/work-orders",
            "cancel_url": "/admin/operations/work-orders",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "work-orders",
        },
    )


@router.post("/work-orders", response_class=HTMLResponse)
def work_order_create(
    request: Request,
    title: str = Form(...),
    description: str | None = Form(None),
    status: str | None = Form(None),
    priority: str | None = Form(None),
    work_type: str | None = Form(None),
    assigned_to_person_id: str | None = Form(None),
    scheduled_start: str | None = Form(None),
    scheduled_end: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.models.workforce import WorkOrderPriority, WorkOrderStatus, WorkOrderType
    from app.schemas.workforce import WorkOrderCreate
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        payload = WorkOrderCreate(
            title=title.strip(),
            description=description.strip() if description else None,
            status=WorkOrderStatus(status) if status else WorkOrderStatus.draft,
            priority=WorkOrderPriority(priority) if priority else WorkOrderPriority.normal,
            work_type=WorkOrderType(work_type) if work_type else WorkOrderType.install,
            assigned_to_person_id=assigned_to_person_id.strip() if assigned_to_person_id else None,
            scheduled_start=_parse_optional_datetime(scheduled_start),
            scheduled_end=_parse_optional_datetime(scheduled_end),
        )
        work_order = workforce_service.work_orders.create(db=db, payload=payload)
        current_user = get_current_user(request)
        log_audit_event(
            db=db,
            request=request,
            action="create",
            entity_type="work_order",
            entity_id=str(work_order.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"status": work_order.status.value if work_order.status else None},
        )
        if assigned_to_person_id:
            _notify_technician_assignment(db, work_order, assigned_to_person_id)
        return RedirectResponse(
            url=f"/admin/operations/work-orders/{work_order.id}",
            status_code=303,
        )
    except Exception as exc:
        technicians = dispatch_service.technicians.list(
            db=db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="desc",
            limit=200,
            offset=0,
        )
        form = {
            "title": title,
            "description": description or "",
            "status": status or WorkOrderStatus.draft.value,
            "priority": priority or WorkOrderPriority.normal.value,
            "work_type": work_type or WorkOrderType.install.value,
            "assigned_to_person_id": assigned_to_person_id or "",
            "scheduled_start": scheduled_start or "",
            "scheduled_end": scheduled_end or "",
        }
        return templates.TemplateResponse(
            "admin/operations/work_order_form.html",
            {
                "request": request,
                "work_order": None,
                "technicians": technicians,
                "status_options": [value.value for value in WorkOrderStatus],
                "priority_options": [value.value for value in WorkOrderPriority],
                "type_options": [value.value for value in WorkOrderType],
                "form": form,
                "is_new": True,
                "form_action": "/admin/operations/work-orders",
                "cancel_url": "/admin/operations/work-orders",
                "error": str(exc),
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
                "active_page": "work-orders",
            },
            status_code=400,
        )

@router.post("/work-orders/{work_order_id}/status", response_class=HTMLResponse)
def work_order_update_status(
    request: Request,
    work_order_id: str,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    from app.schemas.workflow import StatusTransitionRequest
    from app.web.admin import get_current_user

    before = workforce_service.work_orders.get(db=db, work_order_id=work_order_id)
    workflow_service.transition_work_order(
        db=db,
        work_order_id=work_order_id,
        payload=StatusTransitionRequest(to_status=status),
    )
    after = workforce_service.work_orders.get(db=db, work_order_id=work_order_id)
    metadata_payload = build_changes_metadata(before, after)
    current_user = get_current_user(request)
    log_audit_event(
        db=db,
        request=request,
        action="status_change",
        entity_type="work_order",
        entity_id=str(work_order_id),
        actor_id=str(current_user.get("person_id")) if current_user else None,
        metadata=metadata_payload or {"from": before.status.value if before.status else None, "to": status},
    )
    return RedirectResponse(url="/admin/operations/work-orders", status_code=303)


@router.post("/work-orders/{work_order_id}/assign", response_class=HTMLResponse)
def work_order_assign_technician(
    request: Request,
    work_order_id: str,
    assigned_to_person_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.models.workforce import WorkOrderStatus
    from app.schemas.workforce import WorkOrderAssignmentCreate, WorkOrderUpdate
    from app.schemas.workflow import StatusTransitionRequest
    from app.web.admin import get_current_user

    work_order = workforce_service.work_orders.get(db=db, work_order_id=work_order_id)
    previous_person_id = str(work_order.assigned_to_person_id) if work_order.assigned_to_person_id else None
    person_id = assigned_to_person_id.strip() if assigned_to_person_id else None
    if person_id:
        workforce_service.work_orders.update(
            db=db,
            work_order_id=work_order_id,
            payload=WorkOrderUpdate(assigned_to_person_id=person_id),
        )
        workforce_service.work_order_assignments.create(
            db=db,
            payload=WorkOrderAssignmentCreate(
                work_order_id=work_order.id,
                person_id=person_id,
                role="technician",
                is_primary=True,
            ),
        )
        if work_order.status in (WorkOrderStatus.draft, WorkOrderStatus.scheduled):
            workflow_service.transition_work_order(
                db=db,
                work_order_id=work_order_id,
                payload=StatusTransitionRequest(to_status=WorkOrderStatus.dispatched.value),
            )
        if person_id != previous_person_id:
            _notify_technician_assignment(db, work_order, person_id)
            _notify_customer_assignment(db, work_order, person_id)
    else:
        workforce_service.work_orders.update(
            db=db,
            work_order_id=work_order_id,
            payload=WorkOrderUpdate(assigned_to_person_id=None),
        )
    updated = workforce_service.work_orders.get(db=db, work_order_id=work_order_id)
    metadata_payload = build_changes_metadata(work_order, updated)
    current_user = get_current_user(request)
    log_audit_event(
        db=db,
        request=request,
        action="assign",
        entity_type="work_order",
        entity_id=str(work_order_id),
        actor_id=str(current_user.get("person_id")) if current_user else None,
        metadata=metadata_payload or {"from": previous_person_id, "to": person_id},
    )
    return RedirectResponse(url="/admin/operations/work-orders", status_code=303)


@router.post("/dispatch/tickets/{ticket_id}/assign", response_class=HTMLResponse)
def dispatch_assign_ticket(
    request: Request,
    ticket_id: str,
    assigned_to_person_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from uuid import UUID
    from app.schemas.tickets import TicketUpdate
    from app.services import tickets as tickets_service

    person_id = assigned_to_person_id.strip() if assigned_to_person_id else None
    payload = TicketUpdate(assigned_to_person_id=UUID(person_id)) if person_id else TicketUpdate(assigned_to_person_id=None)
    tickets_service.tickets.update(db=db, ticket_id=ticket_id, payload=payload)
    return RedirectResponse(url="/admin/operations/dispatch", status_code=303)


@router.get("/work-orders/{work_order_id}", response_class=HTMLResponse)
def work_order_detail(
    request: Request,
    work_order_id: str,
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user

    work_order = workforce_service.work_orders.get(db=db, work_order_id=work_order_id)
    audit_events = audit_service.audit_events.list(
        db=db,
        actor_id=None,
        actor_type=None,
        action=None,
        entity_type="work_order",
        entity_id=str(work_order_id),
        request_id=None,
        is_success=None,
        status_code=None,
        is_active=None,
        order_by="occurred_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    actor_ids = {str(event.actor_id) for event in audit_events if getattr(event, "actor_id", None)}
    people = {}
    if actor_ids:
        people = {
            str(person.id): person
            for person in db.query(Person).filter(Person.id.in_(actor_ids)).all()
        }
    activities = []
    for event in audit_events:
        actor = people.get(str(event.actor_id)) if getattr(event, "actor_id", None) else None
        actor_name = f"{actor.first_name} {actor.last_name}".strip() if actor else "System"
        metadata = getattr(event, "metadata_", None) or {}
        changes = extract_changes(metadata, getattr(event, "action", None))
        change_summary = format_changes(changes, max_items=2)
        activities.append(
            {
                "title": (event.action or "Activity").replace("_", " ").title(),
                "description": f"{actor_name}" + (f" · {change_summary}" if change_summary else ""),
                "occurred_at": event.occurred_at,
            }
        )
    return templates.TemplateResponse(
        "admin/operations/work_order_detail.html",
        {
            "request": request,
            "work_order": work_order,
            "activities": activities,
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "work-orders",
        },
    )


@router.get("/work-orders/{work_order_id}/edit", response_class=HTMLResponse)
def work_order_edit(
    request: Request,
    work_order_id: str,
    db: Session = Depends(get_db),
):
    from app.models.workforce import WorkOrderPriority, WorkOrderStatus, WorkOrderType
    from app.web.admin import get_sidebar_stats, get_current_user

    work_order = workforce_service.work_orders.get(db=db, work_order_id=work_order_id)
    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    return templates.TemplateResponse(
        "admin/operations/work_order_form.html",
        {
            "request": request,
            "work_order": work_order,
            "technicians": technicians,
            "status_options": [status.value for status in WorkOrderStatus],
            "priority_options": [value.value for value in WorkOrderPriority],
            "type_options": [value.value for value in WorkOrderType],
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "work-orders",
        },
    )


@router.post("/work-orders/{work_order_id}/edit", response_class=HTMLResponse)
def work_order_update(
    request: Request,
    work_order_id: str,
    title: str = Form(...),
    description: str | None = Form(None),
    status: str | None = Form(None),
    priority: str | None = Form(None),
    work_type: str | None = Form(None),
    assigned_to_person_id: str | None = Form(None),
    scheduled_start: str | None = Form(None),
    scheduled_end: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.schemas.workforce import WorkOrderUpdate
    from app.schemas.workflow import StatusTransitionRequest
    from app.web.admin import get_sidebar_stats, get_current_user

    work_order = workforce_service.work_orders.get(db=db, work_order_id=work_order_id)
    try:
        update = WorkOrderUpdate(
            title=title.strip(),
            description=description.strip() if description else None,
            priority=priority or None,
            work_type=work_type or None,
            assigned_to_person_id=assigned_to_person_id.strip() if assigned_to_person_id else None,
            scheduled_start=_parse_optional_datetime(scheduled_start),
            scheduled_end=_parse_optional_datetime(scheduled_end),
        )
        workforce_service.work_orders.update(db=db, work_order_id=work_order_id, payload=update)
        if status and status != work_order.status.value:
            workflow_service.transition_work_order(
                db=db,
                work_order_id=work_order_id,
                payload=StatusTransitionRequest(to_status=status),
            )
        updated = workforce_service.work_orders.get(db=db, work_order_id=work_order_id)
        metadata_payload = build_changes_metadata(work_order, updated)
        current_user = get_current_user(request)
        log_audit_event(
            db=db,
            request=request,
            action="update",
            entity_type="work_order",
            entity_id=str(work_order_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata=metadata_payload,
        )
        return RedirectResponse(url=f"/admin/operations/work-orders/{work_order_id}", status_code=303)
    except Exception as e:
        from app.models.workforce import WorkOrderPriority, WorkOrderStatus, WorkOrderType
        technicians = dispatch_service.technicians.list(
            db=db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="desc",
            limit=200,
            offset=0,
        )
        return templates.TemplateResponse(
            "admin/operations/work_order_form.html",
            {
                "request": request,
                "work_order": work_order,
                "technicians": technicians,
                "status_options": [value.value for value in WorkOrderStatus],
                "priority_options": [value.value for value in WorkOrderPriority],
                "type_options": [value.value for value in WorkOrderType],
                "error": str(e),
                "form": {
                    "title": title,
                    "description": description or "",
                    "status": status or "",
                    "priority": priority or "",
                    "work_type": work_type or "",
                    "assigned_to_person_id": assigned_to_person_id or "",
                    "scheduled_start": scheduled_start or "",
                    "scheduled_end": scheduled_end or "",
                },
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
                "active_page": "work-orders",
            },
            status_code=400,
        )


@router.delete("/work-orders/{work_order_id}", response_class=HTMLResponse)
@router.post("/work-orders/{work_order_id}/delete", response_class=HTMLResponse)
def work_order_delete(
    request: Request,
    work_order_id: str,
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user
    workforce_service.work_orders.delete(db=db, work_order_id=work_order_id)
    current_user = get_current_user(request)
    log_audit_event(
        db=db,
        request=request,
        action="delete",
        entity_type="work_order",
        entity_id=str(work_order_id),
        actor_id=str(current_user.get("person_id")) if current_user else None,
    )
    return RedirectResponse(url="/admin/operations/work-orders", status_code=303)


@router.get("/dispatch", response_class=HTMLResponse)
def dispatch_board(
    request: Request,
    db: Session = Depends(get_db),
):
    """Dispatch board - technician scheduling and work order assignment."""
    from datetime import date
    from app.services import tickets as tickets_service

    # Get technicians
    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=100,
        offset=0,
    )

    # Get unassigned work orders
    all_work_orders = workforce_service.work_orders.list(
        db=db,
        account_id=None,
        subscription_id=None,
        service_order_id=None,
        ticket_id=None,
        project_id=None,
        assigned_to_person_id=None,
        status=None,
        priority=None,
        work_type=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    all_tickets = tickets_service.tickets.list(
        db=db,
        account_id=None,
        subscription_id=None,
        status=None,
        priority=None,
        channel=None,
        search=None,
        created_by_person_id=None,
        assigned_to_person_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )

    def get_status(obj):
        status = getattr(obj, "status", "")
        return status.value if hasattr(status, "value") else str(status)

    # Filter unassigned
    unassigned_work_orders = [wo for wo in all_work_orders if not wo.assigned_to_person_id]
    unassigned_tickets = [ticket for ticket in all_tickets if not ticket.assigned_to_person_id]

    # Filter to assigned work orders (regardless of schedule date)
    today = date.today()
    assigned_jobs = [
        wo for wo in all_work_orders
        if wo.assigned_to_person_id
    ]

    # Stats
    stats = {
        "unassigned": len(unassigned_work_orders),
        "assigned_jobs": len(assigned_jobs),
        "technicians_active": len(technicians),
        "in_progress": sum(1 for wo in assigned_jobs if get_status(wo) == "in_progress"),
        "unassigned_tickets": len(unassigned_tickets),
    }

    from app.web.admin import get_sidebar_stats, get_current_user
    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    return templates.TemplateResponse(
        "admin/operations/dispatch.html",
        {
            "request": request,
            "technicians": technicians,
            "unassigned_work_orders": unassigned_work_orders,
            "unassigned_tickets": unassigned_tickets,
            "assigned_jobs": assigned_jobs,
            "stats": stats,
            "today": today,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "active_page": "dispatch",
            "recent_activities": recent_activity_for_paths(
                db,
                ["/admin/operations/dispatch", "/admin/operations/work-orders", "/admin/operations/technicians"],
            ),
        },
    )


@router.get("/technicians", response_class=HTMLResponse)
def technicians_list(
    request: Request,
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user
    from app.services import person as person_service

    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
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

    return templates.TemplateResponse(
        "admin/operations/technicians.html",
        {
            "request": request,
            "technicians": technicians,
            "people": people,
            "active_page": "technicians",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/technicians/{technician_id}", response_class=HTMLResponse)
def technician_detail(
    request: Request,
    technician_id: str,
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user
    from app.services import tickets as tickets_service
    from app.services import workforce as workforce_service

    try:
        technician = dispatch_service.technicians.get(db=db, technician_id=technician_id)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Technician not found"},
            status_code=404,
        )

    assigned_person_id = str(technician.person_id) if technician.person_id else None
    skills = dispatch_service.technician_skills.list(
        db=db,
        technician_id=technician_id,
        skill_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    tickets = []
    work_orders = []
    if assigned_person_id:
        tickets = tickets_service.tickets.list(
            db=db,
            account_id=None,
            subscription_id=None,
            status=None,
            priority=None,
            channel=None,
            search=None,
            created_by_person_id=None,
            assigned_to_person_id=assigned_person_id,
            is_active=True,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        work_orders = workforce_service.work_orders.list(
            db=db,
            account_id=None,
            subscription_id=None,
            service_order_id=None,
            ticket_id=None,
            project_id=None,
            assigned_to_person_id=assigned_person_id,
            status=None,
            priority=None,
            work_type=None,
            is_active=True,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )

    return templates.TemplateResponse(
        "admin/operations/technician_detail.html",
        {
            "request": request,
            "technician": technician,
            "skills": skills,
            "tickets": tickets,
            "work_orders": work_orders,
            "active_page": "technicians",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/technicians", response_class=HTMLResponse)
def technicians_create(
    request: Request,
    person_id: str = Form(...),
    title: str | None = Form(None),
    region: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.schemas.dispatch import TechnicianProfileCreate
    from app.web.admin import get_sidebar_stats, get_current_user
    from app.services import person as person_service

    try:
        existing = dispatch_service.technicians.list(
            db=db,
            person_id=person_id,
            region=None,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=1,
            offset=0,
        )
        if existing:
            raise ValueError("Technician profile already exists for that person")
        payload = TechnicianProfileCreate(
            person_id=person_id,
            title=title.strip() if title else None,
            region=region.strip() if region else None,
        )
        dispatch_service.technicians.create(db=db, payload=payload)
        return RedirectResponse(url="/admin/operations/technicians", status_code=303)
    except Exception as e:
        technicians = dispatch_service.technicians.list(
            db=db,
            person_id=None,
            region=None,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=500,
            offset=0,
        )
        people = person_service.people.list(
            db=db,
            email=None,
            status=None,
            is_active=True,
            order_by="last_name",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        return templates.TemplateResponse(
            "admin/operations/technicians.html",
            {
                "request": request,
                "technicians": technicians,
                "people": people,
                "error": str(e),
                "form": {
                    "person_id": person_id or "",
                    "title": title or "",
                    "region": region or "",
                },
                "active_page": "technicians",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )


@router.post("/technicians/{technician_id}/deactivate", response_class=HTMLResponse)
def technicians_deactivate(
    request: Request,
    technician_id: str,
    db: Session = Depends(get_db),
):
    dispatch_service.technicians.delete(db=db, technician_id=technician_id)
    return RedirectResponse(url="/admin/operations/technicians", status_code=303)


@router.post("/work-orders/{work_order_id}/send-eta", response_class=HTMLResponse)
def work_order_send_eta(
    request: Request,
    work_order_id: str,
    db: Session = Depends(get_db),
):
    """Send ETA notification to customer for a work order."""
    from app.services import eta_notifications

    work_order = workforce_service.work_orders.get(db=db, work_order_id=work_order_id)

    # Calculate ETA if not already set
    if not work_order.estimated_arrival_at:
        dispatch_service.calculate_eta(db, work_order_id)
        db.refresh(work_order)

    # Send notification
    eta_notifications.send_eta_notification(db, work_order_id)

    # Redirect back to work order detail or list
    referer = request.headers.get("referer", "")
    if f"/work-orders/{work_order_id}" in referer:
        return RedirectResponse(
            url=f"/admin/operations/work-orders/{work_order_id}?eta_sent=1",
            status_code=303,
        )
    return RedirectResponse(url="/admin/operations/work-orders?eta_sent=1", status_code=303)


@router.post("/work-orders/{work_order_id}/auto-assign", response_class=HTMLResponse)
def work_order_auto_assign(
    request: Request,
    work_order_id: str,
    db: Session = Depends(get_db),
):
    """Auto-assign a work order using enhanced skill matching and availability."""
    result = dispatch_service.auto_assign_work_order(db, work_order_id)

    # Refresh work order to get updated assignment
    work_order = workforce_service.work_orders.get(db=db, work_order_id=work_order_id)

    # Notify if assigned
    if work_order.assigned_to_person_id:
        _notify_technician_assignment(db, work_order, str(work_order.assigned_to_person_id))
        _notify_customer_assignment(db, work_order, str(work_order.assigned_to_person_id))

    # Redirect back
    referer = request.headers.get("referer", "")
    if f"/work-orders/{work_order_id}" in referer:
        return RedirectResponse(
            url=f"/admin/operations/work-orders/{work_order_id}",
            status_code=303,
        )
    return RedirectResponse(url="/admin/operations/work-orders", status_code=303)
