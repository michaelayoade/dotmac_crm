"""Admin reports web routes."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.models.dispatch import TechnicianProfile
from app.services.subscriber import subscriber as subscriber_service
from app.services import workforce as workforce_service
from app.services import dispatch as dispatch_service
from app.services.crm import reports as crm_reports_service
from app.services.crm import team as crm_team_service
from app.web.admin import get_current_user, get_sidebar_stats

router = APIRouter(prefix="/reports", tags=["admin-reports"])
templates = Jinja2Templates(directory="templates")


@router.get("/operations")
def operations_report_alias():
    return RedirectResponse(url="/admin/operations/work-orders", status_code=302)


# =============================================================================
# Subscriber Growth Report
# =============================================================================

@router.get("/subscribers", response_class=HTMLResponse)
def subscribers_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
):
    """Subscriber growth report."""
    user = get_current_user(request)

    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)
    month_ago = now - timedelta(days=30)

    # Get stats from subscriber service
    stats = subscriber_service.get_stats(db)

    # Total subscribers
    total_subscribers = stats.get("total", 0)
    active_subscribers = stats.get("active", 0)
    suspended_subscribers = stats.get("suspended", 0)

    # New this month
    new_this_month = (
        db.query(func.count(Subscriber.id))
        .filter(Subscriber.created_at >= month_ago)
        .scalar()
    ) or 0

    # Calculate growth rate
    subscribers_month_ago = (
        db.query(func.count(Subscriber.id))
        .filter(Subscriber.created_at < month_ago)
        .scalar()
    ) or 0

    if subscribers_month_ago > 0:
        subscriber_growth = ((total_subscribers - subscribers_month_ago) / subscribers_month_ago) * 100
    else:
        subscriber_growth = 100.0 if total_subscribers > 0 else 0.0

    active_rate = (active_subscribers / total_subscribers * 100) if total_subscribers > 0 else 0

    # Build chart data - group by day
    chart_data = []
    cumulative_total = subscribers_month_ago
    for i in range(days):
        day = start_date + timedelta(days=i)
        day_end = day + timedelta(days=1)
        count = (
            db.query(func.count(Subscriber.id))
            .filter(Subscriber.created_at >= day)
            .filter(Subscriber.created_at < day_end)
            .scalar()
        ) or 0
        cumulative_total += count
        chart_data.append({
            "date": day.strftime("%Y-%m-%d"),
            "count": count,
            "total": cumulative_total,
        })

    # Build growth_data for chart (last 6 months)
    growth_labels = []
    growth_total = []
    growth_new = []
    for i in range(5, -1, -1):
        month_start = now - timedelta(days=(i + 1) * 30)
        month_end = now - timedelta(days=i * 30)
        growth_labels.append(month_start.strftime("%b"))
        # Total at end of month
        total_at_month = (
            db.query(func.count(Subscriber.id))
            .filter(Subscriber.created_at < month_end)
            .scalar()
        ) or 0
        growth_total.append(total_at_month)
        # New during month
        new_in_month = (
            db.query(func.count(Subscriber.id))
            .filter(Subscriber.created_at >= month_start)
            .filter(Subscriber.created_at < month_end)
            .scalar()
        ) or 0
        growth_new.append(new_in_month)

    growth_data = {
        "labels": growth_labels,
        "total": growth_total,
        "new": growth_new,
    }

    # Status breakdown
    status_breakdown = {}
    for status in SubscriberStatus:
        count = (
            db.query(func.count(Subscriber.id))
            .filter(Subscriber.status == status)
            .scalar()
        ) or 0
        if count > 0:
            status_breakdown[status.value] = count

    # Recent subscribers
    recent_subscribers = (
        db.query(Subscriber)
        .order_by(Subscriber.created_at.desc())
        .limit(5)
        .all()
    )

    return templates.TemplateResponse(
        "admin/reports/subscribers.html",
        {
            "request": request,
            "user": user,
            "total_subscribers": total_subscribers,
            "new_this_month": new_this_month,
            "active_subscribers": active_subscribers,
            "suspended_subscribers": suspended_subscribers,
            "subscriber_growth": subscriber_growth,
            "active_rate": active_rate,
            "chart_data": chart_data,
            "growth_data": growth_data,
            "status_breakdown": status_breakdown,
            "recent_subscribers": recent_subscribers,
            "days": days,
        },
    )


# =============================================================================
# Churn Analysis Report
# =============================================================================

@router.get("/churn", response_class=HTMLResponse)
def churn_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(90, ge=30, le=365),
):
    """Churn analysis report."""
    user = get_current_user(request)

    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Get terminated subscribers in period
    terminated_count = (
        db.query(func.count(Subscriber.id))
        .filter(Subscriber.terminated_at >= start_date)
        .filter(Subscriber.status == SubscriberStatus.terminated)
        .scalar()
    ) or 0

    # Get total at start of period (approximation)
    total_at_start = (
        db.query(func.count(Subscriber.id))
        .filter(Subscriber.created_at < start_date)
        .scalar()
    ) or 0

    # Churn rate
    churn_rate = (terminated_count / total_at_start * 100) if total_at_start > 0 else 0

    # Monthly churn breakdown
    monthly_churn = []
    for i in range(min(days // 30, 12)):
        month_start = now - timedelta(days=(i + 1) * 30)
        month_end = now - timedelta(days=i * 30)
        churned = (
            db.query(func.count(Subscriber.id))
            .filter(Subscriber.terminated_at >= month_start)
            .filter(Subscriber.terminated_at < month_end)
            .scalar()
        ) or 0
        monthly_churn.append({
            "month": month_start.strftime("%b %Y"),
            "churned": churned,
        })

    monthly_churn.reverse()

    return templates.TemplateResponse(
        "admin/reports/churn.html",
        {
            "request": request,
            "user": user,
            "terminated_count": terminated_count,
            "total_at_start": total_at_start,
            "churn_rate": churn_rate,
            "monthly_churn": monthly_churn,
            "days": days,
        },
    )


# =============================================================================
# Network Usage Report
# =============================================================================

@router.get("/network", response_class=HTMLResponse)
def network_report(
    request: Request,
    db: Session = Depends(get_db),
):
    """Network usage report."""
    user = get_current_user(request)

    # Placeholder data - in a real implementation this would pull from
    # bandwidth metrics or network monitoring
    stats = {
        "total_bandwidth": "10 Gbps",
        "peak_usage": "7.2 Gbps",
        "average_usage": "4.5 Gbps",
        "utilization": 45,
    }

    # Placeholder chart data
    chart_data = []
    now = datetime.now(timezone.utc)
    for i in range(24):
        hour = now - timedelta(hours=23 - i)
        chart_data.append({
            "time": hour.strftime("%H:00"),
            "download": 3.5 + (i % 5) * 0.5,
            "upload": 1.2 + (i % 3) * 0.3,
        })

    return templates.TemplateResponse(
        "admin/reports/network.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "chart_data": chart_data,
        },
    )


# =============================================================================
# Technician Performance Report
# =============================================================================

@router.get("/technician", response_class=HTMLResponse)
def technician_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
):
    """Technician performance report."""
    user = get_current_user(request)

    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Get all active technicians
    technicians = dispatch_service.technicians.list(
        db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    # Build performance data for each technician
    technician_stats = []
    total_jobs_completed = 0
    total_hours = 0
    job_count_with_hours = 0

    for tech in technicians:
        # Count completed work orders
        completed = (
            db.query(func.count(WorkOrder.id))
            .filter(WorkOrder.assigned_to_person_id == tech.person_id)
            .filter(WorkOrder.status == WorkOrderStatus.completed)
            .filter(WorkOrder.completed_at >= start_date)
            .scalar()
        ) or 0

        # Count total assigned
        total_assigned = (
            db.query(func.count(WorkOrder.id))
            .filter(WorkOrder.assigned_to_person_id == tech.person_id)
            .filter(WorkOrder.created_at >= start_date)
            .scalar()
        ) or 0

        total_jobs_completed += completed

        # Get technician name
        tech_name = "Unknown"
        if tech.person:
            tech_name = f"{tech.person.first_name or ''} {tech.person.last_name or ''}".strip() or "Unknown"

        # Calculate average hours (placeholder - would need actual time tracking)
        avg_hours = 2.5 if completed > 0 else 0

        # Rating based on completion rate
        completion_rate = (completed / total_assigned * 100) if total_assigned > 0 else 0
        rating = min(5, max(1, int(completion_rate / 20))) if total_assigned > 0 else 3

        technician_stats.append({
            "name": tech_name,
            "total_jobs": total_assigned,
            "completed_jobs": completed,
            "avg_hours": avg_hours,
            "rating": rating,
            "completion_rate": completion_rate,
        })

    # Sort by completed jobs (descending)
    technician_stats.sort(key=lambda x: x["completed_jobs"], reverse=True)

    # Job type breakdown
    job_type_breakdown: dict[str, int] = {}
    work_orders = (
        db.query(WorkOrder)
        .filter(WorkOrder.created_at >= start_date)
        .all()
    )
    for wo in work_orders:
        work_type = wo.work_type.value if wo.work_type else "other"
        job_type_breakdown[work_type] = job_type_breakdown.get(work_type, 0) + 1

    # Recent completions
    recent_completions = (
        db.query(WorkOrder)
        .filter(WorkOrder.status == WorkOrderStatus.completed)
        .filter(WorkOrder.completed_at >= start_date)
        .order_by(WorkOrder.completed_at.desc())
        .limit(5)
        .all()
    )

    # Summary stats
    avg_completion_hours = 2.5  # Placeholder
    first_visit_rate = 85.0  # Placeholder

    return templates.TemplateResponse(
        "admin/reports/technician.html",
        {
            "request": request,
            "user": user,
            "total_technicians": len(technicians),
            "jobs_completed": total_jobs_completed,
            "avg_completion_hours": avg_completion_hours,
            "first_visit_rate": first_visit_rate,
            "technician_stats": technician_stats,
            "job_type_breakdown": job_type_breakdown,
            "recent_completions": recent_completions,
            "days": days,
        },
    )


# =============================================================================
# CRM Performance Report
# =============================================================================

@router.get("/crm-performance", response_class=HTMLResponse)
def crm_performance_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    agent_id: str | None = Query(None),
    team_id: str | None = Query(None),
    channel_type: str | None = Query(None),
):
    """CRM agent/team performance report."""
    from app.models.crm.enums import ChannelType

    user = get_current_user(request)
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Get inbox KPIs
    inbox_stats = crm_reports_service.inbox_kpis(
        db=db,
        start_at=start_date,
        end_at=now,
        channel_type=channel_type,
        agent_id=agent_id,
        team_id=team_id,
    )

    # Get per-agent performance metrics
    agent_stats = crm_reports_service.agent_performance_metrics(
        db=db,
        start_at=start_date,
        end_at=now,
        agent_id=agent_id,
        team_id=team_id,
        channel_type=channel_type,
    )

    # Get conversation trend data
    trend_data = crm_reports_service.conversation_trend(
        db=db,
        start_at=start_date,
        end_at=now,
        agent_id=agent_id,
        team_id=team_id,
        channel_type=channel_type,
    )

    # Summary stats
    total_conversations = sum(agent["total_conversations"] for agent in agent_stats)
    resolved_conversations = sum(agent["resolved_conversations"] for agent in agent_stats)
    resolution_rate = (resolved_conversations / total_conversations * 100) if total_conversations > 0 else 0

    # Average FRT across agents (only count agents with data)
    frt_values = [a["avg_first_response_minutes"] for a in agent_stats if a["avg_first_response_minutes"] is not None]
    avg_frt = sum(frt_values) / len(frt_values) if frt_values else None

    # Average resolution time across agents
    resolution_values = [a["avg_resolution_minutes"] for a in agent_stats if a["avg_resolution_minutes"] is not None]
    avg_resolution = sum(resolution_values) / len(resolution_values) if resolution_values else None

    # Get teams and agents for filter dropdowns
    teams = crm_team_service.Teams.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    agents = crm_team_service.Agents.list(
        db=db,
        person_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    agent_labels = crm_team_service.get_agent_labels(db, agents)

    # Channel type breakdown
    channel_breakdown = inbox_stats.get("messages", {}).get("by_channel", {})

    return templates.TemplateResponse(
        "admin/reports/crm_performance.html",
        {
            "request": request,
            "user": user,
            "active_page": "crm-performance",
            "active_menu": "reports",
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            # Summary metrics
            "total_conversations": total_conversations,
            "resolved_conversations": resolved_conversations,
            "resolution_rate": resolution_rate,
            "avg_frt_minutes": avg_frt,
            "avg_resolution_minutes": avg_resolution,
            "total_messages": inbox_stats.get("messages", {}).get("total", 0),
            "inbound_messages": inbox_stats.get("messages", {}).get("inbound", 0),
            "outbound_messages": inbox_stats.get("messages", {}).get("outbound", 0),
            # Agent breakdown
            "agent_stats": agent_stats,
            # Trend data for charts
            "trend_data": trend_data,
            # Channel breakdown
            "channel_breakdown": channel_breakdown,
            # Filters
            "days": days,
            "selected_agent_id": agent_id,
            "selected_team_id": team_id,
            "selected_channel_type": channel_type,
            # Dropdown options
            "teams": teams,
            "agents": agents,
            "agent_labels": agent_labels,
            "channel_types": [t.value for t in ChannelType],
        },
    )
