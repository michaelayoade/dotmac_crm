"""Service helpers for admin dashboard routes."""

from datetime import datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.audit import AuditActorType
from app.models.network import OLTDevice, OntUnit
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.tickets import Ticket, TicketStatus
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services import (
    audit as audit_service,
    tickets as tickets_service,
    web_admin as web_admin_service,
    system_health as system_health_service,
    settings_spec,
)
from app.services.audit_helpers import (
    extract_changes,
    format_audit_datetime,
    format_changes,
    humanize_action,
    humanize_entity,
)

templates = Jinja2Templates(directory="templates")


def _get_status(obj) -> str:
    status = getattr(obj, "status", "")
    return status.value if hasattr(status, "value") else str(status)


def _float_setting(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_user_actor(actor_type) -> bool:
    return actor_type in {AuditActorType.user, AuditActorType.user.value, "user"}


def _build_dashboard_context(db: Session) -> dict:
    server_health = system_health_service.get_system_health()
    thresholds = {
        "disk_warn_pct": _float_setting(
            settings_spec.resolve_value(db, SettingDomain.network, "server_health_disk_warn_pct")
        ),
        "disk_crit_pct": _float_setting(
            settings_spec.resolve_value(db, SettingDomain.network, "server_health_disk_crit_pct")
        ),
        "mem_warn_pct": _float_setting(
            settings_spec.resolve_value(db, SettingDomain.network, "server_health_mem_warn_pct")
        ),
        "mem_crit_pct": _float_setting(
            settings_spec.resolve_value(db, SettingDomain.network, "server_health_mem_crit_pct")
        ),
        "load_warn": _float_setting(
            settings_spec.resolve_value(db, SettingDomain.network, "server_health_load_warn")
        ),
        "load_crit": _float_setting(
            settings_spec.resolve_value(db, SettingDomain.network, "server_health_load_crit")
        ),
    }
    server_health_status = system_health_service.evaluate_health(server_health, thresholds)

    # Get counts
    customers_count = db.query(func.count(Person.id)).scalar() or 0
    open_tickets_count = (
        db.query(func.count(Ticket.id))
        .filter(Ticket.status.in_([TicketStatus.new, TicketStatus.open, TicketStatus.pending]))
        .scalar()
        or 0
    )
    pending_work_orders = (
        db.query(func.count(WorkOrder.id))
        .filter(WorkOrder.status.in_([WorkOrderStatus.draft, WorkOrderStatus.scheduled]))
        .scalar()
        or 0
    )
    active_olts = (
        db.query(func.count(OLTDevice.id))
        .filter(OLTDevice.is_active.is_(True))
        .scalar()
        or 0
    )

    # Recent activity
    recent_activity = audit_service.audit_events.list(
        db,
        entity_type=None,
        entity_id=None,
        action=None,
        actor_type=None,
        actor_id=None,
        order_by="occurred_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )

    activity_items = []
    for event in recent_activity:
        changes = extract_changes(event.metadata_, event.action)
        activity_items.append({
            "id": str(event.id),
            "action": humanize_action(event.action),
            "entity_type": humanize_entity(event.entity_type),
            "entity_id": event.entity_id,
            "actor_type": event.actor_type.value if event.actor_type else None,
            "actor_id": event.actor_id,
            "created_at": format_audit_datetime(event.occurred_at, "%Y-%m-%d %H:%M"),
            "is_user_actor": _is_user_actor(event.actor_type),
            "changes": format_changes(changes),
        })

    # Build stats dict for template compatibility
    stats = {
        "olts_total": active_olts,
        "olts_online": active_olts,
        "olts_offline": 0,
        "onts_total": 0,
        "onts_online": 0,
        "onts_offline": 0,
        "subscribers_total": customers_count,
        "subscribers_active": customers_count,
        "open_tickets": open_tickets_count,
        "pending_work_orders": pending_work_orders,
    }

    # Network health for template
    network_health = {
        "status": "healthy" if active_olts > 0 else "unknown",
        "percent": 100 if active_olts > 0 else 0,
    }

    return {
        "stats": stats,
        "network_health": network_health,
        "customers_count": customers_count,
        "open_tickets_count": open_tickets_count,
        "pending_work_orders": pending_work_orders,
        "active_olts": active_olts,
        "recent_activity": activity_items,
        "server_health": server_health,
        "server_health_status": server_health_status,
        "thresholds": thresholds,
        "now": datetime.now(),
        "alarms": [],
    }


def dashboard(request: Request, db: Session):
    context = web_admin_service.build_admin_context(request, db)
    context.update(_build_dashboard_context(db))
    return templates.TemplateResponse(
        request,
        "admin/dashboard/index.html",
        context,
    )


def dashboard_stats_partial(request: Request, db: Session):
    context = web_admin_service.build_admin_context(request, db)
    context.update(_build_dashboard_context(db))
    return templates.TemplateResponse(
        request,
        "admin/dashboard/_stats.html",
        context,
    )


def dashboard_activity_partial(request: Request, db: Session):
    context = web_admin_service.build_admin_context(request, db)
    context.update(_build_dashboard_context(db))
    return templates.TemplateResponse(
        request,
        "admin/dashboard/_activity.html",
        context,
    )


def dashboard_server_health_partial(request: Request, db: Session):
    context = web_admin_service.build_admin_context(request, db)
    context.update(_build_dashboard_context(db))
    return templates.TemplateResponse(
        request,
        "admin/dashboard/_server_health.html",
        context,
    )
