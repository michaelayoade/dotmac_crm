"""Admin reports web routes."""

import csv
import io
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models.dispatch import TechnicianProfile
from app.models.person import Person
from app.models.workforce import WorkOrder, WorkOrderStatus
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


# Legacy redirects point to new subscriber overview
@router.get("/subscribers")
def subscribers_report_redirect():
    """Legacy subscriber report - redirect to overview."""
    return RedirectResponse(url="/admin/reports/subscribers/overview", status_code=302)


@router.get("/churn")
def churn_report_redirect():
    """Legacy churn report - redirect to lifecycle."""
    return RedirectResponse(url="/admin/reports/subscribers/lifecycle", status_code=302)


# =============================================================================
# Network Infrastructure Report (real data)
# =============================================================================


@router.get("/network", response_class=HTMLResponse)
def network_report(
    request: Request,
    db: Session = Depends(get_db),
    period_days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Network infrastructure report with real OLT/ONT/fiber data."""
    from app.services import network_reports as nr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(period_days, start_date, end_date)

    kpis = nr.get_network_kpis(db)
    olt_capacity = nr.get_olt_capacity(db)
    fiber_strand_status = nr.get_fiber_strand_status(db)
    ont_trend = nr.get_ont_activation_trend(db, start_dt, end_dt)
    olt_table = nr.get_olt_table(db)
    fdh_table = nr.get_fdh_utilization(db)
    fiber_inventory = nr.get_fiber_inventory(db)
    recent_ont = nr.get_recent_ont_activity(db)

    return templates.TemplateResponse(
        "admin/reports/network.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "network-report",
            "active_menu": "reports",
            "kpis": kpis,
            "olt_capacity": olt_capacity,
            "fiber_strand_status": fiber_strand_status,
            "ont_trend": ont_trend,
            "olt_table": olt_table,
            "fdh_table": fdh_table,
            "fiber_inventory": fiber_inventory,
            "recent_ont": recent_ont,
            "period_days": period_days,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/network/export")
def network_report_export(
    db: Session = Depends(get_db),
):
    """Export network infrastructure report as CSV."""
    from app.services import network_reports as nr

    export_data = nr.get_network_export_data(db)
    filename = f"network_infrastructure_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Overview Report
# =============================================================================


@router.get("/subscribers/overview", response_class=HTMLResponse)
def subscriber_overview(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    status: str | None = Query(None),
    region: str | None = Query(None),
):
    """Subscriber overview report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    kpis = sr.overview_kpis(db, start_dt, end_dt)
    growth_trend = sr.overview_growth_trend(db, start_dt, end_dt)
    status_dist = sr.overview_status_distribution(db)
    plan_dist = sr.overview_plan_distribution(db)
    regional = sr.overview_regional_breakdown(db, start_dt, end_dt)
    filter_opts = sr.overview_filter_options(db)

    return templates.TemplateResponse(
        "admin/reports/subscriber_overview.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-overview",
            "active_menu": "reports",
            "kpis": kpis,
            "growth_trend": growth_trend,
            "status_dist": status_dist,
            "plan_dist": plan_dist,
            "regional": regional,
            "filter_opts": filter_opts,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "selected_status": status or "",
            "selected_region": region or "",
        },
    )


@router.get("/subscribers/overview/export")
def subscriber_overview_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export subscriber overview as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    regional = sr.overview_regional_breakdown(db, start_dt, end_dt)

    export_data = [
        {
            "Region": r["region"],
            "Active": r["active"],
            "Suspended": r["suspended"],
            "Terminated": r["terminated"],
            "New in Period": r["new_in_period"],
            "Tickets": r["ticket_count"],
        }
        for r in regional
    ]
    filename = f"subscriber_overview_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Lifecycle Report
# =============================================================================


@router.get("/subscribers/lifecycle", response_class=HTMLResponse)
def subscriber_lifecycle(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Subscriber lifecycle and churn report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    kpis = sr.lifecycle_kpis(db, start_dt, end_dt)
    funnel = sr.lifecycle_funnel(db)
    churn_trend = sr.lifecycle_churn_trend(db)
    conversion_by_source = sr.lifecycle_conversion_by_source(db, start_dt, end_dt)
    recent_churns = sr.lifecycle_recent_churns(db)
    longest_tenure = sr.lifecycle_longest_tenure(db)

    return templates.TemplateResponse(
        "admin/reports/subscriber_lifecycle.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-lifecycle",
            "active_menu": "reports",
            "kpis": kpis,
            "funnel": funnel,
            "churn_trend": churn_trend,
            "conversion_by_source": conversion_by_source,
            "recent_churns": recent_churns,
            "longest_tenure": longest_tenure,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/subscribers/lifecycle/export")
def subscriber_lifecycle_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export subscriber lifecycle data as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    recent_churns = sr.lifecycle_recent_churns(db, limit=100)

    export_data = [
        {
            "Name": c["name"],
            "Subscriber #": c["subscriber_number"],
            "Plan": c["plan"],
            "Region": c["region"],
            "Activated": c["activated_at"],
            "Terminated": c["terminated_at"],
            "Tenure (days)": c["tenure_days"],
        }
        for c in recent_churns
    ]
    filename = f"subscriber_lifecycle_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Service Quality Report
# =============================================================================


@router.get("/subscribers/service-quality", response_class=HTMLResponse)
def subscriber_service_quality(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Subscriber service quality report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    kpis = sr.service_quality_kpis(db, start_dt, end_dt)
    tickets_by_type = sr.service_quality_tickets_by_type(db, start_dt, end_dt)
    wo_by_type = sr.service_quality_wo_by_type(db, start_dt, end_dt)
    weekly_trend = sr.service_quality_weekly_trend(db, start_dt, end_dt)
    high_maintenance = sr.service_quality_high_maintenance(db, start_dt, end_dt)
    regional_quality = sr.service_quality_regional(db, start_dt, end_dt)

    return templates.TemplateResponse(
        "admin/reports/subscriber_service_quality.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-service-quality",
            "active_menu": "reports",
            "kpis": kpis,
            "tickets_by_type": tickets_by_type,
            "wo_by_type": wo_by_type,
            "weekly_trend": weekly_trend,
            "high_maintenance": high_maintenance,
            "regional_quality": regional_quality,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/subscribers/service-quality/export")
def subscriber_service_quality_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export service quality data as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    high_maintenance = sr.service_quality_high_maintenance(db, start_dt, end_dt, limit=100)

    export_data = [
        {
            "Name": h["name"],
            "Subscriber #": h["subscriber_number"],
            "Region": h["region"],
            "Plan": h["plan"],
            "Tickets": h["tickets"],
            "Work Orders": h["work_orders"],
            "Projects": h["projects"],
            "Total Issues": h["total"],
        }
        for h in high_maintenance
    ]
    filename = f"service_quality_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Revenue & Pipeline Report
# =============================================================================


@router.get("/subscribers/revenue", response_class=HTMLResponse)
def subscriber_revenue(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Subscriber revenue and pipeline report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    kpis = sr.revenue_kpis(db, start_dt, end_dt)
    monthly_trend = sr.revenue_monthly_trend(db)
    payment_status = sr.revenue_payment_status(db, start_dt, end_dt)
    order_status = sr.revenue_order_status(db, start_dt, end_dt)
    top_subscribers = sr.revenue_top_subscribers(db, start_dt, end_dt)
    outstanding = sr.revenue_outstanding_balances(db)

    return templates.TemplateResponse(
        "admin/reports/subscriber_revenue.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-revenue",
            "active_menu": "reports",
            "kpis": kpis,
            "monthly_trend": monthly_trend,
            "payment_status": payment_status,
            "order_status": order_status,
            "top_subscribers": top_subscribers,
            "outstanding": outstanding,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/subscribers/revenue/export")
def subscriber_revenue_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export revenue data as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    top_subs = sr.revenue_top_subscribers(db, start_dt, end_dt, limit=100)

    export_data = [
        {
            "Name": s["name"],
            "Email": s["email"],
            "Total Revenue": s["total_revenue"],
            "Order Count": s["order_count"],
            "Avg Order Value": s["avg_value"],
            "Latest Order": s["latest_order"],
            "Status": s["status"],
        }
        for s in top_subs
    ]
    filename = f"subscriber_revenue_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Technician Performance Report
# =============================================================================


def _get_technician_stats(
    db: Session,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[dict[str, object]], int, dict[str, int], list[WorkOrder]]:
    """Get technician performance stats for a date range."""
    # Active technician profiles should appear even when they have no jobs in range.
    active_technician_person_ids = {
        row
        for row in db.scalars(select(TechnicianProfile.person_id).where(TechnicianProfile.is_active.is_(True))).all()
        if row is not None
    }

    total_rows = db.execute(
        select(WorkOrder.assigned_to_person_id, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.assigned_to_person_id.isnot(None),
            WorkOrder.created_at >= start_date,
            WorkOrder.created_at <= end_date,
        )
        .group_by(WorkOrder.assigned_to_person_id)
    ).all()
    completed_rows = db.execute(
        select(WorkOrder.assigned_to_person_id, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.assigned_to_person_id.isnot(None),
            WorkOrder.status == WorkOrderStatus.completed,
            WorkOrder.completed_at >= start_date,
            WorkOrder.completed_at <= end_date,
        )
        .group_by(WorkOrder.assigned_to_person_id)
    ).all()

    total_by_person = {person_id: count for person_id, count in total_rows if person_id is not None}
    completed_by_person = {person_id: count for person_id, count in completed_rows if person_id is not None}

    person_ids = set(active_technician_person_ids) | set(total_by_person.keys()) | set(completed_by_person.keys())
    people_by_id: dict = {}
    if person_ids:
        people = db.scalars(select(Person).where(Person.id.in_(person_ids), Person.is_active.is_(True))).all()
        people_by_id = {person.id: person for person in people}

    def _person_name(person: Person | None) -> str:
        if not person:
            return "Unknown"
        if person.display_name:
            return person.display_name
        return f"{person.first_name or ''} {person.last_name or ''}".strip() or "Unknown"

    technician_stats = []
    for person_id in person_ids:
        total_assigned = int(total_by_person.get(person_id, 0))
        completed = int(completed_by_person.get(person_id, 0))
        completion_rate = (completed / total_assigned * 100) if total_assigned > 0 else 0
        rating = min(5, max(1, int(completion_rate / 20))) if total_assigned > 0 else 3
        technician_stats.append(
            {
                "name": _person_name(people_by_id.get(person_id)),
                "total_jobs": total_assigned,
                "completed_jobs": completed,
                "avg_hours": 2.5 if completed > 0 else 0,  # Placeholder: use time tracking when available
                "rating": rating,
                "completion_rate": round(completion_rate, 1),
            }
        )

    technician_stats.sort(key=lambda x: (-int(x["completed_jobs"]), -int(x["total_jobs"]), str(x["name"]).lower()))
    total_jobs_completed = sum(completed_by_person.values())

    # Job type breakdown
    type_rows = db.execute(
        select(WorkOrder.work_type, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.created_at >= start_date,
            WorkOrder.created_at <= end_date,
        )
        .group_by(WorkOrder.work_type)
    ).all()
    job_type_breakdown: dict[str, int] = {
        (work_type.value if work_type else "other"): count for work_type, count in type_rows
    }

    # Recent completions
    recent_completions = (
        db.scalars(
            select(WorkOrder)
            .options(joinedload(WorkOrder.assigned_to))
            .where(
                WorkOrder.is_active.is_(True),
                WorkOrder.status == WorkOrderStatus.completed,
                WorkOrder.completed_at >= start_date,
                WorkOrder.completed_at <= end_date,
            )
            .order_by(WorkOrder.completed_at.desc())
            .limit(5)
        )
        .unique()
        .all()
    )

    return technician_stats, total_jobs_completed, job_type_breakdown, list(recent_completions)


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

    technician_stats, total_jobs_completed, job_type_breakdown, recent_completions = _get_technician_stats(
        db, start_dt, end_dt
    )

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
    technician_stats, _, _, _ = _get_technician_stats(db, start_dt, end_dt)

    # Format for CSV
    export_data = []
    for i, tech in enumerate(technician_stats, 1):
        export_data.append(
            {
                "Rank": i,
                "Technician": tech["name"],
                "Total Jobs": tech["total_jobs"],
                "Completed Jobs": tech["completed_jobs"],
                "Completion Rate (%)": tech["completion_rate"],
                "Avg Hours": tech["avg_hours"],
                "Rating": tech["rating"],
            }
        )

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
    resolution_rate = resolved_conversations / total_conversations * 100 if total_conversations > 0 else 0

    # Weighted average FRT across agents (weight by total conversations with valid FRT)
    total_team_response_minutes = sum(
        (a["avg_first_response_minutes"] or 0) * a["total_conversations"]
        for a in agent_stats
        if a["avg_first_response_minutes"] is not None
    )
    total_convos_with_frt = sum(
        a["total_conversations"] for a in agent_stats if a["avg_first_response_minutes"] is not None
    )
    avg_frt = total_team_response_minutes / total_convos_with_frt if total_convos_with_frt > 0 else None

    # Weighted average resolution time across agents (weight by resolved conversations)
    total_resolution_minutes = sum(
        (a["avg_resolution_minutes"] or 0) * a["resolved_conversations"]
        for a in agent_stats
        if a["avg_resolution_minutes"] is not None
    )
    avg_resolution_time = total_resolution_minutes / resolved_conversations if resolved_conversations > 0 else None

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
            "avg_resolution_minutes": avg_resolution_time,
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
            if agent["total_conversations"] > 0
            else 0
        )
        export_data.append(
            {
                "Rank": i,
                "Agent": agent["name"],
                "Active Hours": agent.get("active_hours_display") or "",
                "Total Conversations": agent["total_conversations"],
                "Resolved": agent["resolved_conversations"],
                "Resolution Rate (%)": round(resolution_rate, 1),
                "Avg First Response (min)": round(agent["avg_first_response_minutes"], 1)
                if agent["avg_first_response_minutes"]
                else "",
                "Avg Resolution Time (min)": round(agent["avg_resolution_minutes"], 1)
                if agent["avg_resolution_minutes"]
                else "",
            }
        )

    filename = f"crm_performance_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)
