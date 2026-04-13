"""Dedicated Billing Risk admin routes."""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.db import get_db
from app.models.subscriber import Subscriber
from app.services import billing_risk_reports as billing_risk_service
from app.tasks.subscribers import sync_subscribers_from_splynx
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.auth.rbac import require_web_role
from app.web.templates import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["admin-reports"])
templates = Jinja2Templates(directory="templates")


def _normalize_segment_filters(segments: list[str] | str | None, segment: str | None) -> list[str]:
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


def _csv_response(data: list[dict], filename: str) -> StreamingResponse:
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


def _billing_risk_page_metrics(churn_rows: list[dict]) -> dict[str, int | float]:
    total_count = len(churn_rows)
    total_balance = round(sum(float(row.get("balance") or 0) for row in churn_rows), 2)
    overdue_values = [int(row["days_past_due"]) for row in churn_rows if isinstance(row.get("days_past_due"), int)]
    avg_days_overdue = round(sum(overdue_values) / len(overdue_values)) if overdue_values else 0
    return {
        "total_count": total_count,
        "total_balance": total_balance,
        "avg_days_overdue": avg_days_overdue,
    }


def _billing_risk_page_rows(
    db: Session,
    *,
    due_soon_days: int,
    high_balance_only: bool,
    segment: str | None,
    selected_segments: list[str],
    days_past_due: str | None,
    page: int,
    page_size: int,
    search: str | None,
    overdue_bucket: str | None,
) -> tuple[list[dict], dict[str, int | float], bool]:
    fetch_size = max(1, int(page_size)) + 1
    churn_rows = billing_risk_service.get_billing_risk_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=days_past_due,
        page=page,
        page_size=fetch_size,
        search=search,
        overdue_bucket=overdue_bucket,
        enrich_visible_rows=True,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    has_next = len(churn_rows) > page_size
    visible_rows = churn_rows[:page_size]
    return visible_rows, _billing_risk_page_metrics(visible_rows), has_next


def _billing_risk_initial_rows(
    churn_rows: list[dict],
    *,
    page_size: int,
) -> tuple[list[dict], dict[str, int | float], bool]:
    has_next = len(churn_rows) > page_size
    visible_rows = [dict(row) for row in churn_rows[:page_size]]
    billing_risk_service.enrich_billing_risk_rows(visible_rows)
    return visible_rows, _billing_risk_page_metrics(visible_rows), has_next


@router.get("/subscribers/billing-risk", response_class=HTMLResponse)
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
    user = get_current_user(request)
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )
    global_churn_rows = billing_risk_service.get_billing_risk_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        limit=6000,
        enrich_visible_rows=False,
    )
    churn_rows, page_metrics, has_next = _billing_risk_initial_rows(global_churn_rows, page_size=50)
    overdue_invoices = billing_risk_service.get_overdue_invoices_table(
        db,
        min_days_past_due=overdue_invoice_days,
        limit=250,
    )
    kpis = billing_risk_service.get_billing_risk_summary(global_churn_rows, overdue_invoices)
    segment_breakdown = billing_risk_service.get_billing_risk_segment_breakdown(global_churn_rows)
    aging_buckets = billing_risk_service.get_billing_risk_aging_buckets(global_churn_rows)

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
            "refresh_query": refresh_query,
            "last_synced_at": _latest_subscriber_sync_at(db),
            "csrf_token": get_csrf_token(request),
            "refresh_started": request.query_params.get("refresh_started") == "1",
            "refresh_error": request.query_params.get("refresh_error"),
            "live_page": 1,
            "live_page_size": 50,
            "live_has_next": has_next,
            "live_search": "",
            "live_bucket": "all",
            "page_metrics": page_metrics,
            "page": 1,
            "has_prev": False,
            "has_next": has_next,
        },
    )


@router.post("/subscribers/billing-risk/refresh")
def subscriber_billing_risk_refresh(
    request: Request,
    next_url: str = Form("/admin/reports/subscribers/billing-risk"),
    _admin: dict = Depends(require_web_role("admin")),
):
    if not next_url.startswith("/admin/reports/subscribers/billing-risk"):
        next_url = "/admin/reports/subscribers/billing-risk"

    try:
        sync_subscribers_from_splynx.delay()
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_started", "1"), status_code=303)
    except Exception:
        logger.exception("Failed to enqueue Splynx subscriber sync")
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_error", "queue_unavailable"), status_code=303)


@router.get("/subscribers/billing-risk/blocked-dates")
def subscriber_billing_risk_blocked_dates(
    request: Request,
    external_id: list[str] = Query(default=[]),
):
    get_current_user(request)
    blocked_dates = billing_risk_service.get_live_blocked_dates(external_id)
    return JSONResponse({"blocked_dates": blocked_dates})


@router.get("/subscribers/billing-risk/rows", response_class=HTMLResponse)
def subscriber_billing_risk_rows(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    overdue_invoice_days: int = Query(30, ge=1, le=180),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    search: str | None = Query(None),
    bucket: str | None = Query("all"),
):
    get_current_user(request)
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )
    churn_rows, page_metrics, has_next = _billing_risk_page_rows(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        selected_segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        page=page,
        page_size=page_size,
        search=search,
        overdue_bucket=bucket,
    )
    return templates.TemplateResponse(
        "admin/reports/_subscriber_billing_risk_results.html",
        {
            "request": request,
            "churn_rows": churn_rows,
            "page_metrics": page_metrics,
            "page": page,
            "has_prev": page > 1,
            "has_next": has_next,
        },
    )


@router.get("/subscribers/billing-risk/blocked-date-cell", response_class=HTMLResponse)
def subscriber_billing_risk_blocked_date_cell(
    request: Request,
    external_id: str = Query(...),
):
    get_current_user(request)
    blocked_dates = billing_risk_service.get_live_blocked_dates([external_id])
    return HTMLResponse(blocked_dates.get(external_id, "N/A"))


@router.get("/subscribers/billing-risk/export")
def subscriber_billing_risk_export(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
):
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )

    churn_rows = billing_risk_service.get_billing_risk_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
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
