"""Admin operations web routes."""

import contextlib
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape as html_escape
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.db import get_db
from app.models.auth import UserCredential
from app.models.dispatch import TechnicianProfile
from app.models.inventory import InventoryItem
from app.models.person import Person
from app.models.projects import ProjectType
from app.models.sales_order import SalesOrder, SalesOrderPaymentStatus, SalesOrderStatus
from app.models.vendor import InstallationProject
from app.models.workforce import WorkOrder, WorkOrderPriority, WorkOrderStatus, WorkOrderType
from app.schemas.dispatch import TechnicianProfileCreate, TechnicianProfileUpdate
from app.schemas.sales_order import SalesOrderCreate, SalesOrderLineCreate
from app.schemas.workforce import WorkOrderCreate, WorkOrderUpdate
from app.services import dispatch as dispatch_service
from app.services import sales_orders as sales_orders_service
from app.services import vendor as vendor_service
from app.services import workforce as workforce_service
from app.services.auth_dependencies import require_permission
from app.services.common import coerce_uuid
from app.web.admin import get_current_user, get_sidebar_stats

router = APIRouter(prefix="/operations", tags=["admin-operations"])
templates = Jinja2Templates(directory="templates")


def _parse_local_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _form_text(form, key: str) -> str:
    value = form.get(key)
    return value.strip() if isinstance(value, str) else ""


def _form_text_list(form, key: str) -> list[str]:
    return [item for item in form.getlist(key) if isinstance(item, str)]


def _decimal_from_form(value: str | None, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    raw = value.strip()
    if not raw:
        return default
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return default


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _parse_date_range(
    period_days: int | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    end_dt = now
    if start_date and end_date:
        try:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=UTC)
            end_dt = datetime.fromisoformat(end_date).replace(tzinfo=UTC).replace(hour=23, minute=59, second=59)
            return start_dt, end_dt
        except ValueError:
            pass  # Fall through to default period
    days = period_days or 30
    return now - timedelta(days=days), end_dt


# =============================================================================
# Sales Orders
# =============================================================================


@router.get("/sales-orders", response_class=HTMLResponse)
def sales_orders_list(
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = None,
    payment_status: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List sales orders."""
    user = get_current_user(request)

    offset = (page - 1) * per_page

    orders = sales_orders_service.sales_orders.list(
        db,
        person_id=None,
        quote_id=None,
        status=status,
        payment_status=payment_status,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )

    # Get stats using direct queries
    stats = {
        "total": db.query(func.count(SalesOrder.id)).filter(SalesOrder.is_active.is_(True)).scalar() or 0,
        "draft": db.query(func.count(SalesOrder.id))
        .filter(SalesOrder.status == SalesOrderStatus.draft, SalesOrder.is_active.is_(True))
        .scalar()
        or 0,
        "confirmed": db.query(func.count(SalesOrder.id))
        .filter(SalesOrder.status == SalesOrderStatus.confirmed, SalesOrder.is_active.is_(True))
        .scalar()
        or 0,
        "paid": db.query(func.count(SalesOrder.id))
        .filter(SalesOrder.payment_status == SalesOrderPaymentStatus.paid, SalesOrder.is_active.is_(True))
        .scalar()
        or 0,
        "pending_payment": db.query(func.count(SalesOrder.id))
        .filter(SalesOrder.payment_status == SalesOrderPaymentStatus.pending, SalesOrder.is_active.is_(True))
        .scalar()
        or 0,
    }

    # Count for pagination
    count_query = db.query(func.count(SalesOrder.id)).filter(SalesOrder.is_active.is_(True))
    if status:
        with contextlib.suppress(ValueError):
            count_query = count_query.filter(SalesOrder.status == SalesOrderStatus(status))
    if payment_status:
        with contextlib.suppress(ValueError):
            count_query = count_query.filter(SalesOrder.payment_status == SalesOrderPaymentStatus(payment_status))
    total = count_query.scalar() or 0
    total_pages = math.ceil(total / per_page) if total > 0 else 1

    return templates.TemplateResponse(
        "admin/operations/sales-orders.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "orders": orders,
            "stats": stats,
            "status": status,
            "payment_status": payment_status,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
        },
    )


@router.get("/sales-orders/new", response_class=HTMLResponse)
def sales_order_new(
    request: Request,
    db: Session = Depends(get_db),
):
    """Create sales order form."""
    user = get_current_user(request)
    inventory_items = (
        db.query(InventoryItem).filter(InventoryItem.is_active.is_(True)).order_by(InventoryItem.name.asc()).limit(500).all()
    )
    return templates.TemplateResponse(
        "admin/operations/sales_order_form.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "order": {
                "id": None,
                "order_number": "",
                "person_id": "",
                "account_id": "",
                "status": SalesOrderStatus.draft.value,
                "payment_status": SalesOrderPaymentStatus.pending.value,
                "total": "",
                "amount_paid": "",
                "paid_at": None,
                "notes": "",
            },
            "order_lines": [],
            "inventory_items": inventory_items,
            "person_label": "",
            "account_label": "",
            "statuses": [s.value for s in SalesOrderStatus],
            "payment_statuses": [s.value for s in SalesOrderPaymentStatus],
            "project_types": [item.value for item in ProjectType],
            "action_url": "/admin/operations/sales-orders/new",
            "is_create": True,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/sales-orders/new", response_class=HTMLResponse)
async def sales_order_create(
    request: Request,
    db: Session = Depends(get_db),
):
    """Create sales order from form input."""
    user = get_current_user(request)
    form = await request.form()

    person_id = _form_text(form, "person_id")
    status = _form_text(form, "status") or SalesOrderStatus.draft.value
    payment_status = _form_text(form, "payment_status") or SalesOrderPaymentStatus.pending.value
    project_type = _form_text(form, "project_type")
    allowed_project_types = {item.value for item in ProjectType}
    if project_type and project_type not in allowed_project_types:
        project_type = ""
    amount_paid_raw = _form_text(form, "amount_paid")
    paid_at_raw = _form_text(form, "paid_at")
    notes = _form_text(form, "notes")

    line_item_ids = _form_text_list(form, "line_item_inventory_item_id[]")
    line_descriptions = _form_text_list(form, "line_item_description[]")
    line_quantities = _form_text_list(form, "line_item_quantity[]")
    line_unit_prices = _form_text_list(form, "line_item_unit_price[]")

    lines_payload: list[dict[str, object]] = []
    line_count = max(len(line_item_ids), len(line_descriptions), len(line_quantities), len(line_unit_prices))
    for idx in range(line_count):
        inventory_item_id = (line_item_ids[idx] if idx < len(line_item_ids) else "") or ""
        description = (line_descriptions[idx] if idx < len(line_descriptions) else "").strip()
        quantity = _decimal_from_form(line_quantities[idx] if idx < len(line_quantities) else "", default=Decimal("1"))
        unit_price = _decimal_from_form(line_unit_prices[idx] if idx < len(line_unit_prices) else "", default=Decimal("0"))

        if quantity <= 0:
            quantity = Decimal("1")
        if unit_price < 0:
            unit_price = Decimal("0")

        if not description and inventory_item_id:
            with contextlib.suppress(Exception):
                item = db.get(InventoryItem, coerce_uuid(inventory_item_id))
                if item and item.name:
                    description = item.name

        if not description and not inventory_item_id:
            continue

        amount = _round_money(quantity * unit_price)
        lines_payload.append(
            {
                "inventory_item_id": inventory_item_id or None,
                "description": description or "Sales order item",
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": amount,
            }
        )

    subtotal = Decimal("0")
    for line in lines_payload:
        amount_value = line.get("amount")
        if isinstance(amount_value, Decimal):
            subtotal += amount_value
    subtotal = _round_money(subtotal)
    tax_total = _round_money(subtotal * Decimal("0.075"))
    total = _round_money(subtotal + tax_total)
    amount_paid = _decimal_from_form(amount_paid_raw, default=total)

    person_label = ""
    person_obj = None
    with contextlib.suppress(Exception):
        if person_id:
            person_obj = db.get(Person, coerce_uuid(person_id))
    if person_obj:
        person_label = person_obj.display_name or person_obj.email or ""
    inventory_items = (
        db.query(InventoryItem).filter(InventoryItem.is_active.is_(True)).order_by(InventoryItem.name.asc()).limit(500).all()
    )
    try:
        project_metadata = {"project_type": project_type} if project_type else None
        resolved_status = SalesOrderStatus(status) if status in SalesOrderStatus._value2member_map_ else SalesOrderStatus.draft
        resolved_payment_status = (
            SalesOrderPaymentStatus(payment_status)
            if payment_status in SalesOrderPaymentStatus._value2member_map_
            else SalesOrderPaymentStatus.pending
        )
        payload = SalesOrderCreate(
            person_id=coerce_uuid(person_id),
            status=resolved_status,
            payment_status=resolved_payment_status,
            subtotal=subtotal,
            tax_total=tax_total,
            total=total,
            amount_paid=amount_paid,
            paid_at=_parse_local_datetime(paid_at_raw),
            notes=notes,
            metadata_=project_metadata,
        )
        order = sales_orders_service.sales_orders.create(db, payload)
        for line in lines_payload:
            line_payload = SalesOrderLineCreate(
                sales_order_id=order.id,
                inventory_item_id=coerce_uuid(line["inventory_item_id"]) if line["inventory_item_id"] else None,
                description=str(line["description"]),
                quantity=Decimal(str(line["quantity"])),
                unit_price=Decimal(str(line["unit_price"])),
                amount=Decimal(str(line["amount"])),
            )
            sales_orders_service.sales_order_lines.create(db, line_payload)
        return RedirectResponse(url=f"/admin/operations/sales-orders/{order.id}", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse(
            "admin/operations/sales_order_form.html",
            {
                "request": request,
                "user": user,
                "current_user": user,
                "sidebar_stats": get_sidebar_stats(db),
                "order": {
                    "id": None,
                    "order_number": "",
                    "person_id": person_id or "",
                    "account_id": "",
                    "status": status,
                    "payment_status": payment_status,
                    "project_type": project_type,
                    "subtotal": subtotal,
                    "tax_total": tax_total,
                    "total": total,
                    "amount_paid": amount_paid,
                    "paid_at": _parse_local_datetime(paid_at_raw),
                    "notes": notes or "",
                },
                "order_lines": lines_payload,
                "inventory_items": inventory_items,
                "person_label": person_label,
                "account_label": "",
                "statuses": [s.value for s in SalesOrderStatus],
                "payment_statuses": [s.value for s in SalesOrderPaymentStatus],
                "project_types": [item.value for item in ProjectType],
                "action_url": "/admin/operations/sales-orders/new",
                "is_create": True,
                "csrf_token": get_csrf_token(request),
                "error": str(getattr(exc, "detail", None) or exc),
            },
            status_code=400,
        )


@router.get("/sales-orders/{order_id}", response_class=HTMLResponse)
def sales_order_detail(
    request: Request,
    order_id: UUID,
    db: Session = Depends(get_db),
):
    """Sales order detail page."""
    user = get_current_user(request)

    order = sales_orders_service.sales_orders.get(db, str(order_id))
    if not order:
        return RedirectResponse(url="/admin/operations/sales-orders", status_code=303)

    lines = sales_orders_service.sales_order_lines.list(
        db,
        sales_order_id=str(order_id),
        order_by="created_at",
        order_dir="asc",
        limit=100,
        offset=0,
    )
    auth = getattr(request.state, "auth", {}) or {}
    scopes = set(auth.get("scopes") or [])
    roles = set(auth.get("roles") or [])
    can_delete = "operations:sales_order:delete" in scopes or "admin" in roles

    return templates.TemplateResponse(
        "admin/operations/sales_order_detail.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "order": order,
            "lines": lines,
            "can_delete": can_delete,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/sales-orders/{order_id}/status", response_class=HTMLResponse)
async def sales_order_status_update(
    request: Request,
    order_id: UUID,
    db: Session = Depends(get_db),
):
    """Quick inline status update for a sales order."""
    form = await request.form()
    status_raw = form.get("status")
    status_value = status_raw.strip() if isinstance(status_raw, str) else ""
    try:
        order = sales_orders_service.sales_orders.get(db, str(order_id))
        sales_orders_service.sales_orders.update_from_input(
            db,
            str(order.id),
            status=status_value,
        )
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                content="",
                headers={"HX-Redirect": f"/admin/operations/sales-orders/{order_id}"},
            )
        return RedirectResponse(f"/admin/operations/sales-orders/{order_id}", status_code=303)
    except Exception as exc:
        error = html_escape(exc.detail if hasattr(exc, "detail") else str(exc))
        if request.headers.get("HX-Request"):
            return HTMLResponse(content=f'<p class="text-red-600 text-sm">{error}</p>', status_code=422)
        return RedirectResponse(f"/admin/operations/sales-orders/{order_id}", status_code=303)


@router.get("/sales-orders/{order_id}/edit", response_class=HTMLResponse)
def sales_order_edit(
    request: Request,
    order_id: UUID,
    db: Session = Depends(get_db),
):
    """Edit sales order form."""
    user = get_current_user(request)
    try:
        order = sales_orders_service.sales_orders.get(db, str(order_id))
    except HTTPException:
        return RedirectResponse(url="/admin/operations/sales-orders", status_code=303)
    lines = sales_orders_service.sales_order_lines.list(
        db,
        sales_order_id=str(order_id),
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    inventory_items = (
        db.query(InventoryItem).filter(InventoryItem.is_active.is_(True)).order_by(InventoryItem.name.asc()).limit(500).all()
    )

    return templates.TemplateResponse(
        "admin/operations/sales_order_form.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "order": order,
            "order_lines": lines,
            "inventory_items": inventory_items,
            "account_label": "",
            "statuses": [s.value for s in SalesOrderStatus],
            "payment_statuses": [s.value for s in SalesOrderPaymentStatus],
            "action_url": f"/admin/operations/sales-orders/{order.id}/edit",
            "is_create": False,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/sales-orders/{order_id}/edit", response_class=HTMLResponse)
def sales_order_update(
    request: Request,
    order_id: UUID,
    status: str | None = Form(None),
    payment_status: str | None = Form(None),
    total: str | None = Form(None),
    amount_paid: str | None = Form(None),
    paid_at: str | None = Form(None),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Update sales order."""
    user = get_current_user(request)
    try:
        order = sales_orders_service.sales_orders.get(db, str(order_id))
    except HTTPException:
        return RedirectResponse(url="/admin/operations/sales-orders", status_code=303)
    lines = sales_orders_service.sales_order_lines.list(
        db,
        sales_order_id=str(order_id),
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    inventory_items = (
        db.query(InventoryItem).filter(InventoryItem.is_active.is_(True)).order_by(InventoryItem.name.asc()).limit(500).all()
    )

    try:
        sales_orders_service.sales_orders.update_from_input(
            db,
            str(order.id),
            status=status,
            payment_status=payment_status,
            total=total,
            amount_paid=amount_paid,
            paid_at=paid_at,
            notes=notes,
        )
        return RedirectResponse(url=f"/admin/operations/sales-orders/{order.id}", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse(
            "admin/operations/sales_order_form.html",
            {
                "request": request,
                "user": user,
                "current_user": user,
                "sidebar_stats": get_sidebar_stats(db),
                "order": order,
                "order_lines": lines,
                "inventory_items": inventory_items,
                "account_label": "",
                "statuses": [s.value for s in SalesOrderStatus],
                "payment_statuses": [s.value for s in SalesOrderPaymentStatus],
                "action_url": f"/admin/operations/sales-orders/{order.id}/edit",
                "is_create": False,
                "csrf_token": get_csrf_token(request),
                "error": str(exc),
            },
            status_code=400,
        )


@router.post(
    "/sales-orders/{order_id}/delete",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("operations:sales_order:delete"))],
)
def sales_order_delete(
    request: Request,
    order_id: UUID,
    db: Session = Depends(get_db),
):
    """Soft-delete a sales order and return to the list."""
    try:
        sales_orders_service.sales_orders.delete(db, str(order_id))
        return RedirectResponse(
            url="/admin/operations/sales-orders?success=Sales%20order%20deleted",
            status_code=303,
        )
    except HTTPException as exc:
        detail = quote(str(exc.detail) or "Failed to delete sales order", safe="")
        return RedirectResponse(
            url=f"/admin/operations/sales-orders/{order_id}?error={detail}",
            status_code=303,
        )


# =============================================================================
# Work Orders
# =============================================================================


@router.get("/work-orders", response_class=HTMLResponse)
def work_orders_list(
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = None,
    priority: str | None = None,
    assigned: str | None = None,
    scheduled: str | None = None,
    period_days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List work orders."""
    user = get_current_user(request)

    offset = (page - 1) * per_page
    start_dt, end_dt = _parse_date_range(period_days, start_date, end_date)

    base_query = db.query(WorkOrder).filter(WorkOrder.is_active.is_(True))
    base_query = base_query.filter(WorkOrder.created_at >= start_dt, WorkOrder.created_at <= end_dt)

    # Get stats for the selected date scope (before status/priority filters)
    stats = {
        "total": base_query.count(),
        "draft": base_query.filter(WorkOrder.status == WorkOrderStatus.draft).count(),
        "scheduled": base_query.filter(WorkOrder.status == WorkOrderStatus.scheduled).count(),
        "in_progress": base_query.filter(WorkOrder.status == WorkOrderStatus.in_progress).count(),
        "completed": base_query.filter(WorkOrder.status == WorkOrderStatus.completed).count(),
    }

    filtered_query = base_query
    if status:
        with contextlib.suppress(ValueError):
            filtered_query = filtered_query.filter(WorkOrder.status == WorkOrderStatus(status))
    if priority:
        with contextlib.suppress(ValueError):
            filtered_query = filtered_query.filter(WorkOrder.priority == WorkOrderPriority(priority))

    total = filtered_query.count()
    total_pages = math.ceil(total / per_page) if total > 0 else 1
    orders = filtered_query.order_by(WorkOrder.created_at.desc()).limit(per_page).offset(offset).all()
    technicians = dispatch_service.technicians.list(
        db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=200,
        offset=0,
    )

    return templates.TemplateResponse(
        "admin/operations/work-orders.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "orders": orders,
            "work_orders": orders,
            "technicians": technicians,
            "stats": stats,
            "status": status,
            "priority": priority,
            "assigned": assigned or "",
            "scheduled": scheduled or "",
            "period_days": period_days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "statuses": [s.value for s in WorkOrderStatus],
            "priorities": [p.value for p in WorkOrderPriority],
            "status_options": [s.value for s in WorkOrderStatus],
        },
    )


@router.get("/work-orders/new", response_class=HTMLResponse)
def work_order_new(
    request: Request,
    db: Session = Depends(get_db),
):
    """New work order form."""
    user = get_current_user(request)
    technicians = dispatch_service.technicians.list(
        db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=200,
        offset=0,
    )

    return templates.TemplateResponse(
        "admin/operations/work_order_form.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "work_order": None,
            "technicians": technicians,
            "status_options": [s.value for s in WorkOrderStatus],
            "priority_options": [p.value for p in WorkOrderPriority],
            "type_options": [t.value for t in WorkOrderType],
            "is_new": True,
            "form_action": "/admin/operations/work-orders",
            "cancel_url": "/admin/operations/work-orders",
        },
    )


@router.post("/work-orders", response_class=HTMLResponse)
def work_order_create(
    request: Request,
    title: str = Form(...),
    description: str | None = Form(None),
    status: str = Form("draft"),
    priority: str = Form("normal"),
    work_type: str = Form("install"),
    assigned_to_person_id: str | None = Form(None),
    scheduled_start: str | None = Form(None),
    scheduled_end: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    try:
        payload = WorkOrderCreate(
            title=title.strip(),
            description=description.strip() if description else None,
            status=WorkOrderStatus(status),
            priority=WorkOrderPriority(priority),
            work_type=WorkOrderType(work_type),
            assigned_to_person_id=UUID(assigned_to_person_id) if assigned_to_person_id else None,
            scheduled_start=_parse_local_datetime(scheduled_start),
            scheduled_end=_parse_local_datetime(scheduled_end),
        )
        order = workforce_service.work_orders.create(db, payload)
        return RedirectResponse(
            url=f"/admin/operations/work-orders/{order.id}",
            status_code=303,
        )
    except Exception as exc:
        technicians = dispatch_service.technicians.list(
            db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="asc",
            limit=200,
            offset=0,
        )
        return templates.TemplateResponse(
            "admin/operations/work_order_form.html",
            {
                "request": request,
                "user": user,
                "current_user": user,
                "sidebar_stats": get_sidebar_stats(db),
                "work_order": None,
                "technicians": technicians,
                "status_options": [s.value for s in WorkOrderStatus],
                "priority_options": [p.value for p in WorkOrderPriority],
                "type_options": [t.value for t in WorkOrderType],
                "is_new": True,
                "form_action": "/admin/operations/work-orders",
                "cancel_url": "/admin/operations/work-orders",
                "error": str(exc),
                "form": {
                    "title": title,
                    "description": description or "",
                    "status": status,
                    "priority": priority,
                    "work_type": work_type,
                    "assigned_to_person_id": assigned_to_person_id or "",
                    "scheduled_start": scheduled_start or "",
                    "scheduled_end": scheduled_end or "",
                },
            },
            status_code=400,
        )


@router.get("/work-orders/{order_id}/edit", response_class=HTMLResponse)
def work_order_edit(
    request: Request,
    order_id: UUID,
    db: Session = Depends(get_db),
):
    """Edit work order form."""
    user = get_current_user(request)
    order = workforce_service.work_orders.get(db, str(order_id))

    technicians = dispatch_service.technicians.list(
        db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=200,
        offset=0,
    )

    return templates.TemplateResponse(
        "admin/operations/work_order_form.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "work_order": order,
            "technicians": technicians,
            "status_options": [s.value for s in WorkOrderStatus],
            "priority_options": [p.value for p in WorkOrderPriority],
            "type_options": [t.value for t in WorkOrderType],
            "is_new": False,
            "form_action": f"/admin/operations/work-orders/{order_id}/edit",
            "cancel_url": f"/admin/operations/work-orders/{order_id}",
        },
    )


@router.post("/work-orders/{order_id}/edit", response_class=HTMLResponse)
def work_order_update(
    request: Request,
    order_id: UUID,
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
    user = get_current_user(request)
    try:
        payload = WorkOrderUpdate(
            title=title.strip(),
            description=description.strip() if description else None,
            status=WorkOrderStatus(status) if status else None,
            priority=WorkOrderPriority(priority) if priority else None,
            work_type=WorkOrderType(work_type) if work_type else None,
            assigned_to_person_id=UUID(assigned_to_person_id) if assigned_to_person_id else None,
            scheduled_start=_parse_local_datetime(scheduled_start),
            scheduled_end=_parse_local_datetime(scheduled_end),
        )
        workforce_service.work_orders.update(db, str(order_id), payload)
        return RedirectResponse(
            url=f"/admin/operations/work-orders/{order_id}",
            status_code=303,
        )
    except Exception as exc:
        technicians = dispatch_service.technicians.list(
            db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="asc",
            limit=200,
            offset=0,
        )
        order = workforce_service.work_orders.get(db, str(order_id))
        return templates.TemplateResponse(
            "admin/operations/work_order_form.html",
            {
                "request": request,
                "user": user,
                "current_user": user,
                "sidebar_stats": get_sidebar_stats(db),
                "work_order": order,
                "technicians": technicians,
                "status_options": [s.value for s in WorkOrderStatus],
                "priority_options": [p.value for p in WorkOrderPriority],
                "type_options": [t.value for t in WorkOrderType],
                "is_new": False,
                "form_action": f"/admin/operations/work-orders/{order_id}/edit",
                "cancel_url": f"/admin/operations/work-orders/{order_id}",
                "error": str(exc),
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
            },
            status_code=400,
        )


@router.get("/work-orders/{order_id}", response_class=HTMLResponse)
def work_order_detail(
    request: Request,
    order_id: UUID,
    db: Session = Depends(get_db),
):
    """Work order detail page."""
    user = get_current_user(request)

    order = workforce_service.work_orders.get(db, str(order_id))
    if not order:
        return RedirectResponse(url="/admin/operations/work-orders", status_code=303)

    assignments = workforce_service.work_order_assignments.list(
        db,
        work_order_id=str(order_id),
        person_id=None,
        order_by="assigned_at",
        order_dir="desc",
        limit=50,
        offset=0,
    )

    notes = workforce_service.work_order_notes.list(
        db,
        work_order_id=str(order_id),
        is_internal=None,
        order_by="created_at",
        order_dir="desc",
        limit=50,
        offset=0,
    )

    return templates.TemplateResponse(
        "admin/operations/work_order_detail.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "order": order,
            "work_order": order,
            "assignments": assignments,
            "notes": notes,
        },
    )


@router.post("/work-orders/{order_id}/delete")
def work_order_delete(
    request: Request,
    order_id: UUID,
    db: Session = Depends(get_db),
):
    """Soft-delete a work order and return to the list."""
    _ = get_current_user(request)
    with contextlib.suppress(HTTPException):
        workforce_service.work_orders.delete(db, str(order_id))
    return RedirectResponse(url="/admin/operations/work-orders", status_code=303)


# =============================================================================
# Installations (Vendor Projects)
# =============================================================================


@router.get("/installations", response_class=HTMLResponse)
def installations_list(
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List installation projects."""
    user = get_current_user(request)

    offset = (page - 1) * per_page

    projects = vendor_service.installation_projects.list(
        db=db,
        status=status,
        vendor_id=None,
        subscriber_id=None,
        project_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )

    # Count for pagination
    count_query = db.query(func.count(InstallationProject.id))
    if status:
        count_query = count_query.filter(InstallationProject.status == status)
    total = count_query.scalar() or 0
    total_pages = math.ceil(total / per_page) if total > 0 else 1

    return templates.TemplateResponse(
        "admin/operations/installations.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "projects": projects,
            "status": status,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
        },
    )


# =============================================================================
# Technicians
# =============================================================================


@router.get("/technicians", response_class=HTMLResponse)
def technicians_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List technicians."""
    user = get_current_user(request)

    offset = (page - 1) * per_page

    technicians = dispatch_service.technicians.list(
        db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )
    credential_exists = db.query(UserCredential.id).filter(UserCredential.person_id == Person.id).exists()
    people = (
        db.query(Person)
        .filter(credential_exists)
        .filter(Person.is_active.is_(True))
        .order_by(Person.last_name.asc(), Person.first_name.asc())
        .limit(500)
        .all()
    )

    total = db.query(func.count(TechnicianProfile.id)).filter(TechnicianProfile.is_active.is_(True)).scalar() or 0
    total_pages = math.ceil(total / per_page) if total > 0 else 1

    return templates.TemplateResponse(
        "admin/operations/technicians.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "technicians": technicians,
            "people": people,
            "csrf_token": get_csrf_token(request),
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
        },
    )


@router.post("/technicians", response_class=HTMLResponse)
def technicians_create(
    request: Request,
    person_id: str = Form(...),
    title: str | None = Form(None),
    region: str | None = Form(None),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    user = get_current_user(request)

    error = None
    try:
        person_uuid = coerce_uuid(person_id)
        existing = (
            db.query(TechnicianProfile)
            .filter(TechnicianProfile.person_id == person_uuid)
            .filter(TechnicianProfile.is_active.is_(True))
            .first()
        )
        if existing:
            raise HTTPException(status_code=400, detail="This person already has an active technician profile.")

        payload = TechnicianProfileCreate(
            person_id=person_uuid,
            title=title.strip() if title else None,
            region=region.strip() if region else None,
        )
        dispatch_service.technicians.create(db, payload)
        return RedirectResponse(url="/admin/operations/technicians", status_code=303)
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        error = detail if isinstance(detail, str) else str(exc)

    offset = (page - 1) * per_page
    technicians = dispatch_service.technicians.list(
        db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )
    credential_exists = db.query(UserCredential.id).filter(UserCredential.person_id == Person.id).exists()
    people = (
        db.query(Person)
        .filter(credential_exists)
        .filter(Person.is_active.is_(True))
        .order_by(Person.last_name.asc(), Person.first_name.asc())
        .limit(500)
        .all()
    )
    total = db.query(func.count(TechnicianProfile.id)).filter(TechnicianProfile.is_active.is_(True)).scalar() or 0
    total_pages = math.ceil(total / per_page) if total > 0 else 1

    return templates.TemplateResponse(
        "admin/operations/technicians.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "technicians": technicians,
            "people": people,
            "csrf_token": get_csrf_token(request),
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "error": error,
            "form": {
                "person_id": person_id,
                "title": title,
                "region": region,
            },
        },
    )


@router.get("/technicians/{technician_id}", response_class=HTMLResponse)
def technician_detail(
    request: Request,
    technician_id: UUID,
    db: Session = Depends(get_db),
):
    """Technician detail page."""
    user = get_current_user(request)

    technician = dispatch_service.technicians.get(db, str(technician_id))
    if not technician:
        return RedirectResponse(url="/admin/operations/technicians", status_code=303)

    skills = dispatch_service.technician_skills.list(
        db,
        technician_id=str(technician_id),
        skill_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=50,
        offset=0,
    )

    return templates.TemplateResponse(
        "admin/operations/technician_detail.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "technician": technician,
            "skills": skills,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/technicians/{technician_id}/edit", response_class=HTMLResponse)
def technician_update(
    request: Request,
    technician_id: UUID,
    title: str | None = Form(None),
    region: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    technician = dispatch_service.technicians.get(db, str(technician_id))
    if not technician:
        return RedirectResponse(url="/admin/operations/technicians", status_code=303)

    error = None
    try:
        payload = TechnicianProfileUpdate(
            title=title.strip() if title else None,
            region=region.strip() if region else None,
            is_active=is_active == "true",
        )
        dispatch_service.technicians.update(db, str(technician_id), payload)
        return RedirectResponse(url=f"/admin/operations/technicians/{technician_id}", status_code=303)
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        error = detail if isinstance(detail, str) else str(exc)

    skills = dispatch_service.technician_skills.list(
        db,
        technician_id=str(technician_id),
        skill_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=50,
        offset=0,
    )

    return templates.TemplateResponse(
        "admin/operations/technician_detail.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "technician": technician,
            "skills": skills,
            "csrf_token": get_csrf_token(request),
            "error": error,
        },
    )


# =============================================================================
# Dispatch
# =============================================================================


@router.get("/dispatch", response_class=HTMLResponse)
def dispatch_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    """Dispatch dashboard."""
    user = get_current_user(request)

    active_statuses = [
        WorkOrderStatus.scheduled,
        WorkOrderStatus.dispatched,
        WorkOrderStatus.in_progress,
    ]
    active_orders = (
        db.query(WorkOrder)
        .filter(
            WorkOrder.is_active.is_(True),
            WorkOrder.status.in_(active_statuses),
        )
        .order_by(WorkOrder.scheduled_start.asc())
        .limit(200)
        .all()
    )
    unassigned_work_orders = [order for order in active_orders if not order.assigned_to_person_id]
    assigned_jobs = [order for order in active_orders if order.assigned_to_person_id]

    # Get active technicians
    technicians = dispatch_service.technicians.list(
        db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=50,
        offset=0,
    )

    stats = {
        "unassigned": len(unassigned_work_orders),
        "assigned_jobs": len(assigned_jobs),
        "technicians_active": len(technicians),
        "in_progress": sum(1 for order in active_orders if order.status == WorkOrderStatus.in_progress),
    }

    return templates.TemplateResponse(
        "admin/operations/dispatch.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "unassigned_work_orders": unassigned_work_orders,
            "assigned_jobs": assigned_jobs,
            "technicians": technicians,
            "stats": stats,
        },
    )
