"""Admin reports web routes."""
import csv
import io
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services import dispatch as dispatch_service
from app.services.crm import reports as crm_reports_service
from app.services.crm import team as crm_team_service
from app.web.admin import get_current_user, get_sidebar_stats

router = APIRouter(prefix="/reports", tags=["admin-reports"])
templates = Jinja2Templates(directory="templates")


def _parse_date_range(
    days: int | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[datetime, datetime]:
    """Parse date range from days or custom dates."""
    now = datetime.now(UTC)
    end_dt = now

    if start_date and end_date:
        try:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=UTC)
            end_dt = datetime.fromisoformat(end_date).replace(tzinfo=UTC)
            # Ensure end_date is end of day
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
            return start_dt, end_dt
        except ValueError:
            pass

    # Fall back to days
    days = days or 30
    start_dt = now - timedelta(days=days)
    return start_dt, end_dt


def _csv_response(data: list[dict], filename: str) -> StreamingResponse:
    """Create a CSV streaming response."""
    if not data:
        output = io.StringIO()
        output.write("No data available\n")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=data[0].keys())
    writer.writeheader()
    writer.writerows(data)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/operations")
def operations_report_alias():
    return RedirectResponse(url="/admin/operations/work-orders", status_code=302)


# Legacy subscriber/churn reports removed - redirect to dashboard
@router.get("/subscribers")
def subscribers_report_redirect():
    """Legacy subscriber report - redirect to dashboard."""
    return RedirectResponse(url="/admin/dashboard", status_code=302)


@router.get("/churn")
def churn_report_redirect():
    """Legacy churn report - redirect to dashboard."""
    return RedirectResponse(url="/admin/dashboard", status_code=302)


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
    now = datetime.now(UTC)
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
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "stats": stats,
            "chart_data": chart_data,
        },
    )


# =============================================================================
# Technician Performance Report
# =============================================================================

def _get_technician_stats(
    db: Session,
    start_date: datetime,
) -> tuple[list[dict], int, dict[str, int], list]:
    """Get technician performance stats for a date range."""
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
            "completion_rate": round(completion_rate, 1),
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

    return technician_stats, total_jobs_completed, job_type_breakdown, recent_completions


@router.get("/technician", response_class=HTMLResponse)
def technician_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Technician performance report."""
    user = get_current_user(request)

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    technician_stats, total_jobs_completed, job_type_breakdown, recent_completions = \
        _get_technician_stats(db, start_dt)

    # Summary stats
    avg_completion_hours = 2.5  # Placeholder
    first_visit_rate = 85.0  # Placeholder

    return templates.TemplateResponse(
        "admin/reports/technician.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "total_technicians": len(technician_stats),
            "jobs_completed": total_jobs_completed,
            "avg_completion_hours": avg_completion_hours,
            "first_visit_rate": first_visit_rate,
            "technician_stats": technician_stats,
            "job_type_breakdown": job_type_breakdown,
            "recent_completions": recent_completions,
            "days": days,
            "start_date": start_dt.strftime("%Y-%m-%d"),
            "end_date": end_dt.strftime("%Y-%m-%d"),
        },
    )


@router.get("/technician/export")
def technician_report_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export technician performance report as CSV."""
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    technician_stats, _, _, _ = _get_technician_stats(db, start_dt)

    # Format for CSV
    export_data = []
    for i, tech in enumerate(technician_stats, 1):
        export_data.append({
            "Rank": i,
            "Technician": tech["name"],
            "Total Jobs": tech["total_jobs"],
            "Completed Jobs": tech["completed_jobs"],
            "Completion Rate (%)": tech["completion_rate"],
            "Avg Hours": tech["avg_hours"],
            "Rating": tech["rating"],
        })

    filename = f"technician_performance_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


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
    now = datetime.now(UTC)
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

    # Channel type breakdown (ensure key channels appear even with zero data)
    channel_breakdown = inbox_stats.get("messages", {}).get("by_channel", {})
    channel_labels: dict[str, str] = {}
    email_inbox_breakdown = inbox_stats.get("messages", {}).get("by_email_inbox", {}) or {}

    if email_inbox_breakdown:
        channel_breakdown.pop(str(ChannelType.email), None)
        for inbox_id, data in email_inbox_breakdown.items():
            inbox_key = f"email:{inbox_id}"
            channel_breakdown[inbox_key] = data.get("count", 0)
            inbox_label = data.get("label") or "Unknown Inbox"
            channel_labels[inbox_key] = f"Email - {inbox_label}"

    for channel in (ChannelType.whatsapp, ChannelType.facebook_messenger, ChannelType.instagram_dm):
        channel_key = str(channel)
        if channel_key not in channel_breakdown:
            channel_breakdown[channel_key] = 0

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
            "channel_labels": channel_labels,
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


@router.get("/crm-performance/export")
def crm_performance_report_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    agent_id: str | None = Query(None),
    team_id: str | None = Query(None),
    channel_type: str | None = Query(None),
):
    """Export CRM performance report as CSV."""
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    # Get per-agent performance metrics
    agent_stats = crm_reports_service.agent_performance_metrics(
        db=db,
        start_at=start_dt,
        end_at=end_dt,
        agent_id=agent_id,
        team_id=team_id,
        channel_type=channel_type,
    )

    # Format for CSV
    export_data = []
    for i, agent in enumerate(agent_stats, 1):
        resolution_rate = (
            agent["resolved_conversations"] / agent["total_conversations"] * 100
            if agent["total_conversations"] > 0 else 0
        )
        export_data.append({
            "Rank": i,
            "Agent": agent["name"],
            "Total Conversations": agent["total_conversations"],
            "Resolved": agent["resolved_conversations"],
            "Resolution Rate (%)": round(resolution_rate, 1),
            "Avg First Response (min)": round(agent["avg_first_response_minutes"], 1) if agent["avg_first_response_minutes"] else "",
            "Avg Resolution Time (min)": round(agent["avg_resolution_minutes"], 1) if agent["avg_resolution_minutes"] else "",
        })

    filename = f"crm_performance_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)
