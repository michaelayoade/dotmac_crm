"""Admin reports web routes."""

import csv
import io
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.csrf import get_csrf_token
from app.db import get_db
from app.models.dispatch import TechnicianProfile
from app.models.person import Person
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services import operations_sla_reports as operations_sla_reports_service
from app.services.crm import reports as crm_reports_service
from app.services.crm import team as crm_team_service
from app.services.auth_dependencies import require_any_permission
from app.tasks.subscribers import sync_subscribers_from_splynx
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.templates import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["admin-reports"])
templates = Jinja2Templates(directory="templates")


def _normalize_segment_filters(segments: list[str] | str | None, segment: str | None) -> list[str]:
    """Normalize repeated/comma-separated segment query values."""
    raw_values: list[str] = []
    if isinstance(segments, list):
        raw_values.extend(segments)
    elif isinstance(segments, str):
        raw_values.append(segments)
    if segment:
        raw_values.append(segment)

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        for part in str(raw_value).split(","):
            candidate = part.strip().lower().replace(" ", "_")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
    return normalized


def _segment_labels(selected_segments: list[str]) -> set[str]:
    mapping = {
        "overdue": "Overdue",
        "suspended": "Suspended",
        "due_soon": "Due Soon",
        "churned": "Churned",
        "pending": "Pending",
    }
    return {mapping[key] for key in selected_segments if key in mapping}


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


def _append_query_flag(url: str, key: str, value: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{quote(key)}={quote(value)}"


def _latest_subscriber_sync_at(db: Session) -> datetime | None:
    latest = db.scalar(select(func.max(Subscriber.last_synced_at)))
    if latest is None:
        return None
    if latest.tzinfo is None:
        return latest.replace(tzinfo=UTC)
    return latest.astimezone(UTC)


def _resolve_lifecycle_date_range(
    db: Session,
    days: int | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[datetime, datetime]:
    """Resolve lifecycle report range, defaulting to inception when days is 0/None."""
    if start_date and end_date:
        return _parse_date_range(days, start_date, end_date)

    if days and days > 0:
        return _parse_date_range(days, start_date, end_date)

    now = datetime.now(UTC)
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    inception = db.scalar(select(func.min(activation_event_at)))
    if inception is None:
        return now - timedelta(days=30), now
    if inception.tzinfo is None:
        inception = inception.replace(tzinfo=UTC)
    else:
        inception = inception.astimezone(UTC)
    return inception, now


@router.get("/operations")
def operations_report_alias():
    return RedirectResponse(url="/admin/operations/work-orders", status_code=302)


@router.get("/operations-sla-violations", response_class=HTMLResponse)
def operations_sla_violations_report(
    request: Request,
    db: Session = Depends(get_db),
    data_type: str = Query("ticket"),
    region: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    user = get_current_user(request)
    _valid_types = {"ticket", "project", "project_task"}
    selected_type: Literal["ticket", "project", "project_task"] = (
        data_type if data_type in _valid_types else "ticket"  # type: ignore[assignment]
    )
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    report = operations_sla_reports_service.operations_sla_violations_report
    region_options = report.region_options(db, selected_type)
    selected_region = region if region in region_options else None

    summary = report.summary(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        open_only=True,
    )
    region_chart = report.by_region(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        open_only=True,
    )
    trend_chart = report.trend_daily(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        open_only=True,
    )
    records = report.list_records(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        open_only=True,
    )

    data_type_options = [
        {"value": "ticket", "label": "Tickets"},
        {"value": "project", "label": "Projects"},
        {"value": "project_task", "label": "Project Tasks"},
    ]

    return templates.TemplateResponse(
        "admin/reports/operations_sla_violations.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "active_menu": "reports",
            "active_page": "operations-sla-violations",
            "sidebar_stats": get_sidebar_stats(db),
            "data_type_options": data_type_options,
            "selected_data_type": selected_type,
            "region_options": region_options,
            "selected_region": selected_region or "",
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "summary": summary,
            "region_chart": region_chart,
            "trend_chart": trend_chart,
            "records": records,
        },
    )


# Legacy redirects point to new subscriber overview
@router.get("/subscribers")
def subscribers_report_redirect():
    """Legacy subscriber report - redirect to overview."""
    return RedirectResponse(url="/admin/reports/subscribers/overview", status_code=302)


@router.get("/churn")
def churn_report_redirect():
    """Legacy churn report - redirect to churned subscribers."""
    return RedirectResponse(url="/admin/reports/subscribers/churned", status_code=302)


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
    filter_opts = sr.overview_filter_options(db)
    region_options = filter_opts.get("regions", [])
    region_value = region if isinstance(region, str) else None
    status_value = status if isinstance(status, str) else None
    selected_region = region_value if region_value in region_options else None
    valid_statuses = {status.value: status for status in SubscriberStatus}
    selected_status = valid_statuses.get((status_value or "").strip().lower())
    subscriber_ids = sr.overview_filtered_subscriber_ids(db, status=selected_status, region=selected_region)

    kpis = sr.overview_kpis(db, start_dt, end_dt, subscriber_ids=subscriber_ids)
    growth_trend = sr.overview_growth_trend(db, start_dt, end_dt, subscriber_ids=subscriber_ids)
    status_dist = sr.overview_status_distribution(db, subscriber_ids=subscriber_ids)
    plan_dist = sr.overview_plan_distribution(db, subscriber_ids=subscriber_ids)
    regional = sr.overview_regional_breakdown(db, start_dt, end_dt, subscriber_ids=subscriber_ids)

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
            "selected_status": selected_status.value if selected_status else "",
            "selected_region": selected_region or "",
        },
    )


@router.get("/subscribers/overview/export")
def subscriber_overview_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    status: str | None = Query(None),
    region: str | None = Query(None),
):
    """Export subscriber overview as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    filter_opts = sr.overview_filter_options(db)
    region_options = filter_opts.get("regions", [])
    region_value = region if isinstance(region, str) else None
    status_value = status if isinstance(status, str) else None
    selected_region = region_value if region_value in region_options else None
    valid_statuses = {subscriber_status.value: subscriber_status for subscriber_status in SubscriberStatus}
    selected_status = valid_statuses.get((status_value or "").strip().lower())
    subscriber_ids = sr.overview_filtered_subscriber_ids(db, status=selected_status, region=selected_region)
    regional = sr.overview_regional_breakdown(db, start_dt, end_dt, subscriber_ids=subscriber_ids)

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
    days: int = Query(0, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    sort_by: str = Query("total_paid"),
):
    """Subscriber lifecycle and churn report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)

    kpis = sr.lifecycle_kpis(db, start_dt, end_dt)
    funnel = sr.lifecycle_funnel(db)
    churn_trend = sr.lifecycle_churn_trend(db)
    conversion_by_source = sr.lifecycle_conversion_by_source(db, start_dt, end_dt)
    retention_cohorts = sr.lifecycle_retention_cohorts(db, start_dt, end_dt)
    time_to_convert_distribution = sr.lifecycle_time_to_convert_distribution(db, start_dt, end_dt)
    plan_migration_flow = sr.lifecycle_plan_migration_flow(db, start_dt, end_dt)
    plan_distribution = sr.overview_plan_distribution(db, limit=8)
    recent_churns = sr.lifecycle_recent_churns(db)
    recent_churn_summary = sr.lifecycle_recent_churn_summary(db)
    longest_tenure = sr.lifecycle_longest_tenure(db)
    top_subscribers_by_value = sr.lifecycle_top_subscribers_by_value(db)
    top_subscribers_title = "Top Subscribers By Value (All Time)"
    top_subscribers_description = "Sorted by total paid across all subscriber histories."
    if sort_by == "tenure_months":
        top_subscribers_by_value = sorted(
            top_subscribers_by_value,
            key=lambda row: (-(row.get("tenure_months") or 0), -(row.get("total_paid") or 0), row.get("name") or ""),
        )
        top_subscribers_title = "By Tenure"
        top_subscribers_description = "Sorted by tenure, with total paid as tie-breaker."
    elif sort_by == "plan_type":
        top_subscribers_by_value = sorted(
            top_subscribers_by_value,
            key=lambda row: (
                (row.get("plan") or "").lower(),
                -(row.get("total_paid") or 0),
                -(row.get("tenure_months") or 0),
                row.get("name") or "",
            ),
        )
        top_subscribers_title = "Plan Type"
        top_subscribers_description = "Sorted alphabetically by plan type, with revenue and tenure as tie-breakers."
    else:
        sort_by = "total_paid"

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
            "retention_cohorts": retention_cohorts,
            "time_to_convert_distribution": time_to_convert_distribution,
            "plan_migration_flow": plan_migration_flow,
            "plan_distribution": plan_distribution,
            "recent_churns": recent_churns,
            "recent_churn_summary": recent_churn_summary,
            "longest_tenure": longest_tenure,
            "top_subscribers_by_value": top_subscribers_by_value,
            "top_subscribers_title": top_subscribers_title,
            "top_subscribers_description": top_subscribers_description,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "sort_by": sort_by,
        },
    )


@router.get("/subscribers/lifecycle/export")
def subscriber_lifecycle_export(
    db: Session = Depends(get_db),
    days: int = Query(0, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export subscriber lifecycle data as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)
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
# Churned Subscribers Report
# =============================================================================


@router.get("/subscribers/churned", response_class=HTMLResponse)
def churned_subscribers(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    behavioral_days: int = Query(60, ge=30, le=180),
):
    """Standard churned subscribers dashboard with KPIs, trend, and churn detail tables."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)
    kpis = sr.churned_subscribers_kpis(db, start_dt, end_dt, behavioral_days=behavioral_days)
    churn_trend = sr.churned_subscribers_trend(db, start_dt, end_dt, behavioral_days=behavioral_days)
    churned_rows = sr.churned_subscribers_rows(db, start_dt, end_dt, limit=100, behavioral_days=behavioral_days)
    failed_payment_rows = sr.churned_failed_payment_rows(
        db,
        start_dt,
        end_dt,
        limit=50,
        behavioral_days=behavioral_days,
    )
    cancelled_rows = sr.churned_cancelled_rows(db, start_dt, end_dt, limit=50)
    inactive_usage_rows = sr.churned_inactive_usage_rows(db, end_dt, limit=50)

    churned_count = kpis.get("churned_count")
    if churned_count is None:
        churned_count = kpis.get("terminated_in_period")
    if churned_count is None:
        churned_count = len(churned_rows)
    kpis["churned_count"] = int(churned_count or 0)

    active_at_start = int(kpis.get("total_active_subscribers_start") or 0)
    kpis["churn_rate"] = round((kpis["churned_count"] / active_at_start) * 100, 1) if active_at_start > 0 else 0.0

    return templates.TemplateResponse(
        "admin/reports/churned_subscribers.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-churned",
            "active_menu": "reports",
            "kpis": kpis,
            "churn_trend": churn_trend,
            "churned_rows": churned_rows,
            "failed_payment_rows": failed_payment_rows,
            "cancelled_rows": cancelled_rows,
            "inactive_usage_rows": inactive_usage_rows,
            "distinct_churned_subscribers_count": kpis["churned_count"],
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "behavioral_days": behavioral_days,
        },
    )


@router.get("/subscribers/churned/export")
def churned_subscribers_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    behavioral_days: int = Query(60, ge=30, le=180),
):
    """Export churned subscriber rows as CSV for selected range."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)
    churned_rows = sr.churned_subscribers_rows(db, start_dt, end_dt, limit=1000, behavioral_days=behavioral_days)

    export_data = [
        {
            "Name": row["name"],
            "Subscriber #": row["subscriber_number"],
            "Plan": row["plan"],
            "Region": row["region"],
            "Activated": row["activated_at"],
            "Terminated": row["terminated_at"],
            "Tenure (days)": row["tenure_days"],
        }
        for row in churned_rows
    ]
    filename = f"subscriber_churned_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Billing Risk Report
# =============================================================================


@router.get(
    "/subscribers/billing-risk",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
def subscriber_billing_risk(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    overdue_invoice_days: int = Query(30, ge=1, le=180),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
):
    """Billing risk dashboard for blocked, overdue, and otherwise at-risk subscribers."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)

    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments, query_segment or segment
    )

    churn_rows = sr.get_churn_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        source="splynx_live",
        limit=500,
        enrich_visible_rows=False,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    overdue_invoices = sr.get_overdue_invoices_table(
        db,
        min_days_past_due=overdue_invoice_days,
        limit=250,
    )
    kpis = sr.churn_risk_summary(churn_rows, overdue_invoices)
    segment_breakdown = sr.churn_risk_segment_breakdown(churn_rows)
    aging_buckets = sr.churn_risk_aging_buckets(churn_rows, due_soon_days=due_soon_days)

    export_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
        },
        doseq=True,
    )
    retention_tracker_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
        },
        doseq=True,
    )
    refresh_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segment": segment or "",
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due or "",
        },
        doseq=True,
    )

    return templates.TemplateResponse(
        "admin/reports/subscriber_billing_risk.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-billing-risk",
            "active_menu": "reports",
            "kpis": kpis,
            "segment_breakdown": segment_breakdown,
            "aging_buckets": aging_buckets,
            "churn_rows": churn_rows,
            "overdue_invoices": overdue_invoices,
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": high_balance_only,
            "selected_segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "export_query": export_query,
            "retention_tracker_query": retention_tracker_query,
            "refresh_query": refresh_query,
            "last_synced_at": _latest_subscriber_sync_at(db),
            "billing_risk_cache": {"row_count": len(churn_rows)},
            "csrf_token": get_csrf_token(request),
            "refresh_started": request.query_params.get("refresh_started") == "1",
            "refresh_error": request.query_params.get("refresh_error"),
        },
    )


@router.post("/subscribers/billing-risk/refresh")
def subscriber_billing_risk_refresh(
    request: Request,
    next_url: str = Form("/admin/reports/subscribers/billing-risk"),
    _permission: dict = Depends(require_any_permission("reports:billing", "reports:subscribers", "reports")),
):
    if not next_url.startswith("/admin/reports/subscribers/billing-risk"):
        next_url = "/admin/reports/subscribers/billing-risk"

    try:
        sync_subscribers_from_splynx.delay()
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_started", "1"), status_code=303)
    except Exception:
        logger.exception("Failed to enqueue Splynx subscriber sync")
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_error", "queue_unavailable"), status_code=303)


@router.get(
    "/subscribers/billing-risk/export",
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
def subscriber_billing_risk_export(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
):
    """Export billing risk rows as CSV."""
    from app.services import subscriber_reports as sr

    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments, query_segment or segment
    )

    churn_rows = sr.get_churn_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        source="splynx_live",
        limit=2000,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    export_data = [
        {
            "Name": row["name"],
            "Email": row["email"],
            "Phone": row.get("phone", ""),
            "Subscriber Status": row["subscriber_status"],
            "Risk Segment": row["risk_segment"],
            "Next Bill Date": row["next_bill_date"],
            "Days To Due": row["days_to_due"],
            "Days Past Due": row.get("days_past_due", ""),
            "Balance": row["balance"],
            "Billing Cycle": row["billing_cycle"],
            "Last Transaction Date": row["last_transaction_date"],
            "Expires In": row["expires_in"],
            "Invoiced Until": row["invoiced_until"],
            "Days Since Last Payment": row.get("days_since_last_payment", ""),
            "Total Paid": row["total_paid"],
            "High Balance Risk": "Yes" if row["is_high_balance_risk"] else "No",
        }
        for row in churn_rows
    ]
    filename = f"subscriber_billing_risk_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
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

    technician_stats.sort(
        key=lambda x: (
            -(x["completed_jobs"] if isinstance(x["completed_jobs"], int) else 0),
            -(x["total_jobs"] if isinstance(x["total_jobs"], int) else 0),
            str(x.get("name", "")).lower(),
        )
    )
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


# =============================================================================
# Agent Performance Report (Weekly Trends)
# =============================================================================


@router.get("/agent-performance", response_class=HTMLResponse)
def agent_performance_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(7, ge=7, le=90),
):
    """Weekly agent performance report with trend comparisons."""
    user = get_current_user(request)
    now = datetime.now(UTC)
    current_start = now - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)
    previous_end = current_start

    current_metrics = crm_reports_service.agent_weekly_performance(
        db,
        start_at=current_start,
        end_at=now,
    )
    previous_metrics = crm_reports_service.agent_weekly_performance(
        db,
        start_at=previous_start,
        end_at=previous_end,
    )

    prev_map = {m["agent_id"]: m for m in previous_metrics}

    all_resolved = [m["resolved_count"] for m in current_metrics]
    team_median_resolved = sorted(all_resolved)[len(all_resolved) // 2] if all_resolved else 0

    for m in current_metrics:
        prev = prev_map.get(m["agent_id"], {})
        m["prev_resolved_count"] = prev.get("resolved_count", 0)
        m["prev_median_response_seconds"] = prev.get("median_response_seconds")
        m["prev_median_resolution_seconds"] = prev.get("median_resolution_seconds")
        m["prev_open_backlog"] = prev.get("open_backlog", 0)
        m["prev_csat_avg"] = prev.get("csat_avg")
        m["prev_sla_breach_count"] = prev.get("sla_breach_count", 0)
        m["below_median"] = m["resolved_count"] < team_median_resolved

    return templates.TemplateResponse(
        "admin/reports/agent_performance.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "agent-performance",
            "active_menu": "reports",
            "days": days,
            "agents": current_metrics,
            "team_median_resolved": team_median_resolved,
        },
    )
