"""Service helpers for admin dashboard routes."""

from datetime import UTC, datetime, timedelta

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.audit import AuditActorType
from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.enums import LeadStatus, QuoteStatus
from app.models.crm.sales import Lead, Quote
from app.models.domain_settings import SettingDomain
from app.models.network import OLTDevice
from app.models.person import Person
from app.models.projects import Project, ProjectStatus
from app.models.sales_order import SalesOrder, SalesOrderStatus
from app.models.tickets import Ticket, TicketStatus
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services import (
    audit as audit_service,
)
from app.services import (
    settings_spec,
)
from app.services import (
    system_health as system_health_service,
)
from app.services import (
    web_admin as web_admin_service,
)
from app.services.audit_helpers import (
    _resolve_actor_name,
    extract_changes,
    format_audit_datetime,
    format_changes,
    humanize_action,
    humanize_entity,
)
from app.services.crm.inbox.metrics import get_inbox_metrics
from app.services.crm.inbox.queries import get_inbox_stats

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


def _build_stats_context(db: Session) -> dict:
    """Build only stats-related context (counts and network health)."""
    inbox_stats = get_inbox_stats(db)
    inbox_metrics = get_inbox_metrics(db)
    customers_count = db.query(func.count(Person.id)).scalar() or 0
    open_tickets_count = (
        db.query(func.count(Ticket.id))
        .filter(
            Ticket.status.in_(
                [
                    TicketStatus.new,
                    TicketStatus.open,
                    TicketStatus.pending,
                    TicketStatus.waiting_on_customer,
                    TicketStatus.lastmile_rerun,
                    TicketStatus.site_under_construction,
                    TicketStatus.on_hold,
                ]
            )
        )
        .scalar()
        or 0
    )
    pending_work_orders = (
        db.query(func.count(WorkOrder.id))
        .filter(WorkOrder.status.in_([WorkOrderStatus.draft, WorkOrderStatus.scheduled]))
        .scalar()
        or 0
    )
    active_olts = db.query(func.count(OLTDevice.id)).filter(OLTDevice.is_active.is_(True)).scalar() or 0

    # CRM metrics
    total_contacts = db.query(func.count(Person.id)).scalar() or 0
    total_leads = db.query(func.count(Lead.id)).filter(Lead.is_active.is_(True)).scalar() or 0
    open_leads = (
        db.query(func.count(Lead.id))
        .filter(
            Lead.is_active.is_(True),
            Lead.status.in_(
                [
                    LeadStatus.new,
                    LeadStatus.contacted,
                    LeadStatus.qualified,
                    LeadStatus.proposal,
                    LeadStatus.negotiation,
                ]
            ),
        )
        .scalar()
        or 0
    )
    won_leads = (
        db.query(func.count(Lead.id)).filter(Lead.is_active.is_(True), Lead.status == LeadStatus.won).scalar() or 0
    )
    pipeline_value = (
        db.query(func.coalesce(func.sum(Lead.estimated_value), 0))
        .filter(
            Lead.is_active.is_(True),
            Lead.status.in_(
                [
                    LeadStatus.new,
                    LeadStatus.contacted,
                    LeadStatus.qualified,
                    LeadStatus.proposal,
                    LeadStatus.negotiation,
                ]
            ),
        )
        .scalar()
        or 0
    )

    total_quotes = db.query(func.count(Quote.id)).filter(Quote.is_active.is_(True)).scalar() or 0
    draft_quotes = (
        db.query(func.count(Quote.id)).filter(Quote.is_active.is_(True), Quote.status == QuoteStatus.draft).scalar()
        or 0
    )
    sent_quotes = (
        db.query(func.count(Quote.id)).filter(Quote.is_active.is_(True), Quote.status == QuoteStatus.sent).scalar() or 0
    )
    accepted_quotes = (
        db.query(func.count(Quote.id)).filter(Quote.is_active.is_(True), Quote.status == QuoteStatus.accepted).scalar()
        or 0
    )

    active_projects = db.query(func.count(Project.id)).filter(Project.status == ProjectStatus.active).scalar() or 0

    # Ticket breakdown — "in progress" = open + pending + waiting_on_customer
    tickets_in_progress = (
        db.query(func.count(Ticket.id))
        .filter(Ticket.status.in_([TicketStatus.open, TicketStatus.pending, TicketStatus.waiting_on_customer]))
        .scalar()
        or 0
    )

    # Sales orders
    active_sales_orders = (
        db.query(func.count(SalesOrder.id))
        .filter(SalesOrder.status.in_([SalesOrderStatus.draft, SalesOrderStatus.confirmed]))
        .scalar()
        or 0
    )

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
        "tickets_in_progress": tickets_in_progress,
        "pending_work_orders": pending_work_orders,
        "unread_messages": inbox_stats.get("unread", 0),
        "inbox_open": inbox_stats.get("open", 0),
        "inbox_pending": inbox_stats.get("pending", 0),
        "inbox_snoozed": inbox_stats.get("snoozed", 0),
        "inbox_resolved": inbox_stats.get("resolved", 0),
        "total_contacts": total_contacts,
        "total_leads": total_leads,
        "open_leads": open_leads,
        "won_leads": won_leads,
        "pipeline_value": pipeline_value,
        "total_quotes": total_quotes,
        "draft_quotes": draft_quotes,
        "sent_quotes": sent_quotes,
        "accepted_quotes": accepted_quotes,
        "active_projects": active_projects,
        "active_sales_orders": active_sales_orders,
    }

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
        "inbox_metrics": inbox_metrics,
        "inbox_stats": inbox_stats,
    }


def _build_live_stats_context(db: Session) -> dict:
    """Build lightweight context for high-priority agent metrics."""
    return {"high_priority_stats": get_high_priority_stats(db)}


def get_high_priority_stats(db: Session) -> dict:
    """Return high-priority live metrics for the dashboard."""
    now = datetime.now(UTC)
    last_24h = now - timedelta(hours=24)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    assigned_subq = (
        db.query(ConversationAssignment.conversation_id)
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.agent_id.isnot(None))
        .distinct()
    )
    unassigned_conversations = (
        db.query(func.count(Conversation.id))
        .filter(Conversation.is_active.is_(True))
        .filter(~Conversation.id.in_(assigned_subq))
        .scalar()
        or 0
    )

    lead_velocity_24h = (
        db.query(func.count(Lead.id)).filter(Lead.is_active.is_(True)).filter(Lead.created_at >= last_24h).scalar() or 0
    )

    opened_today = (
        db.query(func.count(Ticket.id))
        .filter(Ticket.created_at >= day_start)
        .filter(Ticket.created_at < day_end)
        .scalar()
        or 0
    )
    closed_timestamp = func.coalesce(Ticket.closed_at, Ticket.resolved_at)
    closed_today = (
        db.query(func.count(Ticket.id))
        .filter(closed_timestamp.isnot(None))
        .filter(closed_timestamp >= day_start)
        .filter(closed_timestamp < day_end)
        .scalar()
        or 0
    )
    resolution_rate_pct = (closed_today / opened_today * 100.0) if opened_today > 0 else None

    total_leads = db.query(func.count(Lead.id)).filter(Lead.is_active.is_(True)).scalar() or 0
    won_leads = (
        db.query(func.count(Lead.id)).filter(Lead.is_active.is_(True), Lead.status == LeadStatus.won).scalar() or 0
    )
    conversion_rate_pct = (won_leads / total_leads * 100.0) if total_leads > 0 else None
    inbox_stats = get_inbox_stats(db)
    inbox_metrics = get_inbox_metrics(db)
    waiting_queue_count = int(inbox_stats.get("open", 0) or 0) + int(inbox_stats.get("snoozed", 0) or 0)
    failed_outbox_count = int((inbox_metrics.get("outbox") or {}).get("failed", 0) or 0)

    return {
        "unassigned_conversations": unassigned_conversations,
        "lead_velocity_24h": lead_velocity_24h,
        "tickets_opened_today": opened_today,
        "tickets_closed_today": closed_today,
        "resolution_rate_pct": resolution_rate_pct,
        "won_leads": won_leads,
        "total_leads": total_leads,
        "conversion_rate_pct": conversion_rate_pct,
        "waiting_queue_count": waiting_queue_count,
        "failed_outbox_count": failed_outbox_count,
    }


def _build_activity_context(db: Session) -> dict:
    """Build only activity-related context (audit log)."""
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

    # Batch-load Person records for user-type actors
    actor_ids = {
        str(event.actor_id)
        for event in recent_activity
        if event.actor_id and _is_user_actor(event.actor_type)
    }
    people: dict[str, Person] = {}
    if actor_ids:
        people = {str(p.id): p for p in db.query(Person).filter(Person.id.in_(actor_ids)).all()}

    activity_items = []
    for event in recent_activity:
        changes = extract_changes(event.metadata_, event.action)
        activity_items.append(
            {
                "id": str(event.id),
                "action": humanize_action(event.action),
                "entity_type": humanize_entity(event.entity_type),
                "entity_id": event.entity_id,
                "actor_type": event.actor_type.value if event.actor_type else None,
                "actor_id": event.actor_id,
                "actor_name": _resolve_actor_name(event, people),
                "created_at": format_audit_datetime(event.occurred_at, "%Y-%m-%d %H:%M"),
                "is_user_actor": _is_user_actor(event.actor_type),
                "changes": format_changes(changes),
            }
        )

    return {
        "recent_activity": activity_items,
    }


def _build_server_health_context(db: Session) -> dict:
    """Build only server health context (system metrics and thresholds)."""
    server_health = system_health_service.get_system_health()

    # Batch load all threshold settings in one query
    threshold_keys = [
        "server_health_disk_warn_pct",
        "server_health_disk_crit_pct",
        "server_health_mem_warn_pct",
        "server_health_mem_crit_pct",
        "server_health_load_warn",
        "server_health_load_crit",
    ]
    values = settings_spec.resolve_values_atomic(db, SettingDomain.network, threshold_keys)

    thresholds = {
        "disk_warn_pct": _float_setting(values.get("server_health_disk_warn_pct")),
        "disk_crit_pct": _float_setting(values.get("server_health_disk_crit_pct")),
        "mem_warn_pct": _float_setting(values.get("server_health_mem_warn_pct")),
        "mem_crit_pct": _float_setting(values.get("server_health_mem_crit_pct")),
        "load_warn": _float_setting(values.get("server_health_load_warn")),
        "load_crit": _float_setting(values.get("server_health_load_crit")),
    }
    server_health_status = system_health_service.evaluate_health(server_health, thresholds)

    return {
        "server_health": server_health,
        "server_health_status": server_health_status,
        "thresholds": thresholds,
    }


def _build_dashboard_context(db: Session) -> dict:
    """Build full dashboard context for initial page load."""
    context = {}
    context.update(_build_stats_context(db))
    context.update(_build_live_stats_context(db))
    context.update(_build_activity_context(db))
    context.update(_build_server_health_context(db))
    context["now"] = datetime.now()
    context["alarms"] = []
    return context


def dashboard(request: Request, db: Session):
    context = web_admin_service.build_admin_context(request, db)
    context.update(_build_dashboard_context(db))
    return templates.TemplateResponse(
        request,
        "admin/dashboard/index.html",
        context,
    )


def dashboard_stats_partial(request: Request, db: Session):
    """HTMX partial for stats section only."""
    context = web_admin_service.build_admin_context(request, db)
    context.update(_build_stats_context(db))
    return templates.TemplateResponse(
        request,
        "admin/dashboard/_stats.html",
        context,
    )


def dashboard_activity_partial(request: Request, db: Session):
    """HTMX partial for activity section only."""
    context = web_admin_service.build_admin_context(request, db)
    context.update(_build_activity_context(db))
    return templates.TemplateResponse(
        request,
        "admin/dashboard/_activity.html",
        context,
    )


def dashboard_live_stats_partial(request: Request, db: Session):
    """HTMX partial for high-priority live stats cards."""
    context = web_admin_service.build_admin_context(request, db)
    context.update(_build_live_stats_context(db))
    return templates.TemplateResponse(
        request,
        "admin/dashboard/_live_stats_cards.html",
        context,
    )


def dashboard_server_health_partial(request: Request, db: Session):
    """HTMX partial for server health section only."""
    context = web_admin_service.build_admin_context(request, db)
    context.update(_build_server_health_context(db))
    return templates.TemplateResponse(
        request,
        "admin/dashboard/_server_health.html",
        context,
    )
