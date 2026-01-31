"""Admin operations web routes."""
import math
from datetime import datetime
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.sales_order import SalesOrder, SalesOrderStatus, SalesOrderPaymentStatus
from app.models.workforce import WorkOrder, WorkOrderStatus, WorkOrderPriority, WorkOrderType
from app.models.vendor import InstallationProject
from app.models.dispatch import TechnicianProfile
from app.schemas.workforce import WorkOrderCreate, WorkOrderUpdate
from app.services import sales_orders as sales_orders_service
from app.services import workforce as workforce_service
from app.services import dispatch as dispatch_service
from app.services import vendor as vendor_service
from app.services.auth_dependencies import require_permission
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
        "draft": db.query(func.count(SalesOrder.id)).filter(SalesOrder.status == SalesOrderStatus.draft, SalesOrder.is_active.is_(True)).scalar() or 0,
        "confirmed": db.query(func.count(SalesOrder.id)).filter(SalesOrder.status == SalesOrderStatus.confirmed, SalesOrder.is_active.is_(True)).scalar() or 0,
        "paid": db.query(func.count(SalesOrder.id)).filter(SalesOrder.payment_status == SalesOrderPaymentStatus.paid, SalesOrder.is_active.is_(True)).scalar() or 0,
        "pending_payment": db.query(func.count(SalesOrder.id)).filter(SalesOrder.payment_status == SalesOrderPaymentStatus.pending, SalesOrder.is_active.is_(True)).scalar() or 0,
    }

    # Count for pagination
    count_query = db.query(func.count(SalesOrder.id)).filter(SalesOrder.is_active.is_(True))
    if status:
        try:
            count_query = count_query.filter(SalesOrder.status == SalesOrderStatus(status))
        except ValueError:
            pass
    if payment_status:
        try:
            count_query = count_query.filter(SalesOrder.payment_status == SalesOrderPaymentStatus(payment_status))
        except ValueError:
            pass
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
        },
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
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List work orders."""
    user = get_current_user(request)

    offset = (page - 1) * per_page

    orders = workforce_service.work_orders.list(
        db,
        subscriber_id=None,
        ticket_id=None,
        project_id=None,
        assigned_to_person_id=None,
        status=status,
        priority=priority,
        work_type=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )

    # Get stats using direct queries
    stats = {
        "total": db.query(func.count(WorkOrder.id)).scalar() or 0,
        "draft": db.query(func.count(WorkOrder.id)).filter(WorkOrder.status == WorkOrderStatus.draft).scalar() or 0,
        "scheduled": db.query(func.count(WorkOrder.id)).filter(WorkOrder.status == WorkOrderStatus.scheduled).scalar() or 0,
        "in_progress": db.query(func.count(WorkOrder.id)).filter(WorkOrder.status == WorkOrderStatus.in_progress).scalar() or 0,
        "completed": db.query(func.count(WorkOrder.id)).filter(WorkOrder.status == WorkOrderStatus.completed).scalar() or 0,
    }

    # Count for pagination
    count_query = db.query(func.count(WorkOrder.id))
    if status:
        try:
            count_query = count_query.filter(WorkOrder.status == WorkOrderStatus(status))
        except ValueError:
            pass
    if priority:
        try:
            count_query = count_query.filter(WorkOrder.priority == WorkOrderPriority(priority))
        except ValueError:
            pass
    total = count_query.scalar() or 0
    total_pages = math.ceil(total / per_page) if total > 0 else 1

    return templates.TemplateResponse(
        "admin/operations/work-orders.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "orders": orders,
            "stats": stats,
            "status": status,
            "priority": priority,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "statuses": [s.value for s in WorkOrderStatus],
            "priorities": [p.value for p in WorkOrderPriority],
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
    try:
        workforce_service.work_orders.delete(db, str(order_id))
    except HTTPException:
        pass
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
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
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
