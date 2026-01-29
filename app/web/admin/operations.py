"""Admin operations web routes."""
import math
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.sales_order import SalesOrder, SalesOrderStatus, SalesOrderPaymentStatus
from app.models.workforce import WorkOrder, WorkOrderStatus, WorkOrderPriority, WorkOrderType
from app.models.vendor import InstallationProject
from app.models.dispatch import TechnicianProfile
from app.services import sales_orders as sales_orders_service
from app.services import workforce as workforce_service
from app.services import dispatch as dispatch_service
from app.services import vendor as vendor_service
from app.web.admin import get_current_user

router = APIRouter(prefix="/operations", tags=["admin-operations"])
templates = Jinja2Templates(directory="templates")


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

    return templates.TemplateResponse(
        "admin/operations/sales_order_detail.html",
        {
            "request": request,
            "user": user,
            "order": order,
            "lines": lines,
        },
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
        order_by="created_at",
        order_dir="asc",
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
            "order": order,
            "assignments": assignments,
            "notes": notes,
        },
    )


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
            "unassigned_work_orders": unassigned_work_orders,
            "assigned_jobs": assigned_jobs,
            "technicians": technicians,
            "stats": stats,
        },
    )
