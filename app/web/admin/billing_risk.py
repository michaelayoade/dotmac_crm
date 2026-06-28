"""Dedicated Billing Risk admin routes."""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import bindparam, func, or_, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.csrf import get_csrf_token
from app.db import end_read_only_transaction, get_db
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.customer_retention import CustomerRetentionEngagement
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember
from app.models.subscriber import Subscriber, SubscriberBillingRiskSnapshot
from app.services import billing_risk_cache, selfcare
from app.services import billing_risk_reports as billing_risk_service
from app.services.common import coerce_uuid
from app.services.crm.web_campaigns import create_billing_risk_outreach_campaign, outreach_channel_target_options
from app.services.customer_retention import create_retention_engagement_and_sync
from app.services.external_systems import EXTERNAL_SUBSCRIBER_SYSTEMS
from app.tasks.subscribers import sync_subscribers_from_selfcare
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.auth.rbac import require_web_role
from app.web.templates import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["admin-reports"])
customer_retention_router = APIRouter(tags=["admin-customer-retention"])
templates = Jinja2Templates(directory="templates")

RETENTION_PIPELINE_STEPS = ("Contacted", "Follow-up Pending", "Promised to Pay", "Resolved", "Lost")
ENTERPRISE_MRR_THRESHOLD = int(billing_risk_service.ENTERPRISE_MRR_THRESHOLD)
RETENTION_FIXED_REP_NAMES = (
    "Chizaram Ogbonna",
    "Grace Moses",
    "Abigail Tongov",
    "Stephanie Mojekwu",
)
RETENTION_REP_TEAM_OVERRIDES = {
    "ahmed omodara": "Customer Support",
    "chinenye onyeagba": "Customer Support",
    "chris mbah": "Customer Support",
    "david dikko": "Customer Support",
    "david ekechukwu": "Customer Support",
    "divine madu": "Customer Support",
    "james akah": "Customer Support",
    "monica eyire edako": "Customer Support",
    "seun ayoade": "Customer Support",
    "shallom chukwueke": "Customer Support",
    "chinelo okoro": "Sales Call Center",
    "martin nwaogu": "Sales Call Center",
    "ochanya abah": "Sales Call Center",
    "ruth ogbedebi": "Sales Call Center",
}
RETENTION_REP_LABEL_ONLY_NAMES = {"ejiro onovwiona"}
RETENTION_EXCLUDED_CUSTOMER_NAMES = {"test", "test account"}
ACTIVE_OFFLINE_LAST_SEEN_START = date(2026, 3, 1)


def _normalize_customer_name_for_exclusion(name: object) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(name or "").strip().casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def _is_excluded_retention_customer(name: object) -> bool:
    normalized = _normalize_customer_name_for_exclusion(name)
    return bool(normalized) and (
        normalized in RETENTION_EXCLUDED_CUSTOMER_NAMES or normalized.startswith("test account")
    )


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
        "active": "Active",
        "overdue": "Due Soon",
        "suspended": "Suspended",
        "due_soon": "Due Soon",
        "churned": "Churned",
        "pending": "Pending",
    }
    return {mapping[key] for key in selected_segments if key in mapping}


def _normalize_billing_risk_customer_status(value: str | None) -> str:
    normalized = str(value or "suspended").strip().lower()
    if normalized in {"active", "suspended", "all"}:
        return normalized
    return "suspended"


def _normalize_billing_type_filter(value: str | None) -> str:
    normalized = str(value or "all").strip().lower()
    if normalized in {"prepaid", "postpaid", "all"}:
        return normalized
    return "all"


def _billing_type_category(value: object) -> str:
    normalized = str(value or "").strip().lower()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    if normalized == "unknown":
        return ""
    if normalized == "prepaid" or normalized.startswith("prepaid"):
        return "prepaid"
    if normalized in {"recurring", "postpaid", "post_paid"}:
        return "postpaid"
    return ""


def _billing_row_type_category(row: dict) -> str:
    for key in ("billing_mode", "subscription_billing_mode"):
        normalized = str(row.get(key) or "").strip().lower().replace("-", "_").replace(" ", "_")
        if normalized == "prepaid":
            return "prepaid"
        if normalized == "postpaid":
            return "postpaid"
    return _billing_type_category(row.get("billing_type"))


def _billing_risk_billing_type_rows(rows: list[dict], billing_type: str) -> list[dict]:
    normalized = _normalize_billing_type_filter(billing_type)
    if normalized == "all":
        return rows
    return [row for row in rows if _billing_row_type_category(row) == normalized]


def _billing_type_display_label(row: dict) -> str:
    category = _billing_row_type_category(row)
    if category == "prepaid":
        return "Prepaid"
    if category == "postpaid":
        return "Recurring/Postpaid"
    return "Unknown"


def _money(value: object) -> float:
    coerced = _coerce_money_value(value)
    return float(coerced or 0)


def _postpaid_dashboard_rows(
    db: Session,
    *,
    search: str | None = None,
    location: str | None = None,
    status: str | None = None,
    service_status: str | None = None,
    plan: str | None = None,
    billing_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 5000,
) -> list[dict]:
    rows = billing_risk_cache.all_cached_rows(
        db,
        selected_segments=["active", "due_soon", "suspended"],
        search=search,
        location=(location or "").strip(),
        limit=max(1, int(limit)),
    )
    normalized_status = str(status or "all").strip().lower()
    normalized_service_status = str(service_status or "all").strip().lower()
    normalized_plan = str(plan or "").strip()
    normalized_billing_type = _normalize_billing_type_filter(billing_type or "postpaid")
    start_date = _parse_report_date(date_from)
    end_date = _parse_report_date(date_to)
    postpaid_rows: list[dict] = []
    for source_row in rows:
        row = dict(source_row)
        if _billing_row_type_category(row) != "postpaid":
            continue
        if normalized_billing_type != "all" and _billing_row_type_category(row) != normalized_billing_type:
            continue
        row_status = str(row.get("subscriber_status") or "").strip().lower()
        if normalized_status != "all" and row_status != normalized_status:
            continue
        row_service_status = str(row.get("risk_segment") or "").strip().lower()
        if normalized_service_status != "all" and row_service_status != normalized_service_status:
            continue
        row_plan = str(row.get("plan") or "").strip()
        if normalized_plan and row_plan.casefold() != normalized_plan.casefold():
            continue
        _enrich_expiration_fields([row])
        row_expiration_date = _parse_report_date(row.get("service_expiration_date") or row.get("expiration_date"))
        if start_date is not None and (row_expiration_date is None or row_expiration_date < start_date):
            continue
        if end_date is not None and (row_expiration_date is None or row_expiration_date > end_date):
            continue
        row["revenue_owed"] = _money(row.get("revenue_owed"))
        row["balance"] = _money(row.get("balance"))
        row["mrr_total"] = _money(row.get("mrr_total"))
        row["billing_type_label"] = _billing_type_display_label(row)
        postpaid_rows.append(row)
    return postpaid_rows


def _postpaid_dashboard_kpis(
    rows: list[dict], *, all_customer_rows: list[dict] | None = None
) -> dict[str, int | float]:
    total_customers = len(rows)
    outstanding_balance = round(sum(_money(row.get("balance")) for row in rows), 2)
    overdue_balance = round(
        sum(_money(row.get("balance")) for row in rows if int(row.get("days_past_due") or 0) > 0),
        2,
    )
    customers_with_overdue = sum(1 for row in rows if int(row.get("days_past_due") or 0) > 0)
    unpaid_invoices = sum(1 for row in rows if _money(row.get("balance")) > 0)
    total_revenue_owed = round(sum(_money(row.get("revenue_owed")) for row in rows), 2)
    total_mrr = round(sum(_money(row.get("mrr_total")) for row in rows), 2)
    return {
        "total_customers": total_customers,
        "outstanding_balance": outstanding_balance,
        "overdue_balance": overdue_balance,
        "customers_with_overdue": customers_with_overdue,
        "unpaid_invoices": unpaid_invoices,
        "prepaid_customers_with_unpaid_balances": 0,
        "prepaid_unpaid_invoice_balance": 0,
        "average_mrr": round(total_mrr / total_customers, 2) if total_customers else 0,
        "total_revenue_owed": total_revenue_owed,
        "total_mrr": total_mrr,
        "avg_revenue_owed": round(total_revenue_owed / total_customers, 2) if total_customers else 0,
    }


def _postpaid_location_breakdown(rows: list[dict], *, limit: int = 10) -> list[dict[str, int | float | str]]:
    grouped: dict[str, dict[str, int | float | str]] = {}
    for row in rows:
        label = str(row.get("location") or row.get("city") or "Unknown").strip() or "Unknown"
        entry = grouped.setdefault(label, {"location": label, "customers": 0, "revenue_owed": 0.0})
        entry["customers"] = int(entry["customers"]) + 1
        entry["revenue_owed"] = round(float(entry["revenue_owed"]) + _money(row.get("revenue_owed")), 2)
    return sorted(
        grouped.values(), key=lambda item: (float(item["revenue_owed"]), int(item["customers"])), reverse=True
    )[:limit]


def _postpaid_top_customer_balance_chart(rows: list[dict], *, limit: int = 10) -> list[dict[str, float | str]]:
    sorted_rows = sorted(rows, key=lambda row: _money(row.get("balance")), reverse=True)
    return [
        {
            "name": str(row.get("name") or "Unknown"),
            "balance": _money(row.get("balance")),
        }
        for row in sorted_rows[:limit]
        if _money(row.get("balance")) > 0
    ]


def _postpaid_customer_status_chart(rows: list[dict]) -> list[dict[str, int | str]]:
    buckets = {"active": 0, "suspended": 0, "blocked": 0}
    for row in rows:
        status = str(row.get("subscriber_status") or "").strip().lower()
        if status in {"active", "suspended", "blocked"}:
            buckets[status] += 1
    return [
        {"label": "Active", "count": buckets["active"], "color": "#10b981"},
        {"label": "Suspended", "count": buckets["suspended"], "color": "#f59e0b"},
        {"label": "Blocked", "count": buckets["blocked"], "color": "#ef4444"},
    ]


def _postpaid_payment_recency_chart(rows: list[dict]) -> list[dict[str, int | str]]:
    buckets = {
        "Paid <30 Days": 0,
        "Paid 31-60 Days": 0,
        "Paid 61-90 Days": 0,
        "Paid >90 Days": 0,
        "Never Paid": 0,
    }
    today = datetime.now(UTC).date()
    for row in rows:
        days_value = row.get("days_since_last_payment")
        days_since_payment: int | None = None
        if isinstance(days_value, int):
            days_since_payment = days_value
        elif isinstance(days_value, str) and days_value.strip().isdigit():
            days_since_payment = int(days_value.strip())
        else:
            payment_date = _parse_report_date(row.get("last_transaction_date"))
            if payment_date is not None:
                days_since_payment = max(0, (today - payment_date).days)
        if days_since_payment is None:
            buckets["Never Paid"] += 1
        elif days_since_payment <= 30:
            buckets["Paid <30 Days"] += 1
        elif days_since_payment <= 60:
            buckets["Paid 31-60 Days"] += 1
        elif days_since_payment <= 90:
            buckets["Paid 61-90 Days"] += 1
        else:
            buckets["Paid >90 Days"] += 1
    colors = {
        "Paid <30 Days": "#10b981",
        "Paid 31-60 Days": "#06b6d4",
        "Paid 61-90 Days": "#f59e0b",
        "Paid >90 Days": "#ef4444",
        "Never Paid": "#64748b",
    }
    return [{"label": label, "count": count, "color": colors[label]} for label, count in buckets.items()]


def _postpaid_payment_amount_trend(rows: list[dict], *, limit: int = 12) -> list[dict[str, float | str]]:
    grouped: dict[str, float] = {}
    for row in rows:
        payment_date = _parse_report_date(row.get("last_transaction_date"))
        payment_amount = _money(row.get("total_paid"))
        if payment_date is None or payment_amount <= 0:
            continue
        month_key = payment_date.strftime("%Y-%m")
        grouped[month_key] = grouped.get(month_key, 0.0) + payment_amount
    return [
        {"month": month, "amount": round(amount, 2)}
        for month, amount in sorted(grouped.items(), key=lambda item: item[0])[-limit:]
    ]


def _postpaid_invoice_segment_chart(rows: list[dict]) -> list[dict[str, int | str]]:
    grouped: dict[str, dict[str, int | str]] = {}
    for row in rows:
        segment = str(row.get("risk_segment") or "Unknown").strip() or "Unknown"
        entry = grouped.setdefault(segment, {"segment": segment, "unpaid": 0, "overdue": 0})
        if _money(row.get("balance")) > 0:
            entry["unpaid"] = int(entry["unpaid"]) + 1
        if int(row.get("days_past_due") or 0) > 0:
            entry["overdue"] = int(entry["overdue"]) + 1
    return [
        grouped[segment] for segment in ("Active", "Due Soon", "Suspended", "Blocked", "Unknown") if segment in grouped
    ] + [
        value
        for segment, value in sorted(grouped.items(), key=lambda item: item[0])
        if segment not in {"Active", "Due Soon", "Suspended", "Blocked", "Unknown"}
    ]


def _postpaid_invoice_aging_chart(rows: list[dict]) -> list[dict[str, float | str]]:
    buckets = {
        "Current": 0.0,
        "1-30 Days": 0.0,
        "31-60 Days": 0.0,
        "61-90 Days": 0.0,
        "90+ Days": 0.0,
    }
    for row in rows:
        balance = _money(row.get("balance"))
        days_past_due = int(row.get("days_past_due") or 0)
        if days_past_due <= 0:
            buckets["Current"] += balance
        elif days_past_due <= 30:
            buckets["1-30 Days"] += balance
        elif days_past_due <= 60:
            buckets["31-60 Days"] += balance
        elif days_past_due <= 90:
            buckets["61-90 Days"] += balance
        else:
            buckets["90+ Days"] += balance
    return [{"bucket": bucket, "balance": round(balance, 2)} for bucket, balance in buckets.items()]


def _postpaid_detail_table_rows(rows: list[dict]) -> list[dict]:
    blocked_terms = ("dotmac", "test")
    return [row for row in rows if not any(term in str(row.get("name") or "").casefold() for term in blocked_terms)]


def _billing_invoice_rows(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    invoice_rows: list[dict] = []
    for key in ("invoices", "active_invoices", "unpaid_invoices", "open_invoices"):
        value = payload.get(key)
        if isinstance(value, list):
            invoice_rows.extend(row for row in value if isinstance(row, dict))
    for key in ("billing", "account", "customer"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            invoice_rows.extend(_billing_invoice_rows(nested))
    return invoice_rows


def _invoice_balance_due(invoice: dict) -> float:
    return _money(
        invoice.get("balance_due")
        or invoice.get("balanceDue")
        or invoice.get("due_balance")
        or invoice.get("amount_due")
        or invoice.get("outstanding_balance")
        or invoice.get("balance")
    )


def _is_active_unpaid_invoice(invoice: dict) -> bool:
    if _invoice_balance_due(invoice) <= 0:
        return False
    if invoice.get("is_active") is False or invoice.get("active") is False:
        return False
    status = str(invoice.get("status") or invoice.get("invoice_status") or "").strip().casefold()
    payment_status = str(invoice.get("payment_status") or invoice.get("paid_status") or "").strip().casefold()
    excluded = {"paid", "cancelled", "canceled", "void", "voided", "deleted", "draft", "refunded"}
    return not (status in excluded or payment_status in excluded)


def _active_unpaid_invoice_summary(billing_payload: dict | None) -> dict[str, object]:
    invoices = [invoice for invoice in _billing_invoice_rows(billing_payload) if _is_active_unpaid_invoice(invoice)]
    balance_due = round(sum(_invoice_balance_due(invoice) for invoice in invoices), 2)
    last_invoice_date = _first_text(
        *[
            _first_text(
                invoice.get("invoice_date"),
                invoice.get("date"),
                invoice.get("created_at"),
                invoice.get("issued_at"),
            )
            for invoice in invoices
        ]
    )
    next_due_date = _first_text(
        *[
            _first_text(
                invoice.get("due_date"),
                invoice.get("dueDate"),
                invoice.get("payment_due_date"),
            )
            for invoice in invoices
        ]
    )
    return {
        "count": len(invoices),
        "balance_due": balance_due,
        "last_invoice_date": last_invoice_date,
        "next_due_date": next_due_date,
    }


def _apply_prepaid_unpaid_invoice_summary(rows: list[dict]) -> None:
    for row in rows:
        summary = row.get("_prepaid_unpaid_invoice_summary")
        if not isinstance(summary, dict):
            continue
        row["detail_unpaid_invoices"] = int(summary.get("count") or 0)
        row["detail_overdue_invoices"] = 0
        row["detail_outstanding_balance"] = _money(summary.get("balance_due"))
        row["detail_overdue_balance"] = 0
        row["detail_last_invoice_date"] = _first_text(
            summary.get("last_invoice_date"), row.get("detail_last_invoice_date")
        )
        row["detail_next_due_date"] = _first_text(summary.get("next_due_date"), row.get("detail_next_due_date"))


def _prepaid_unpaid_balance_table_rows(
    rows: list[dict],
    *,
    status: str | None = None,
    plan: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    normalized_status = str(status or "all").strip().lower()
    normalized_plan = str(plan or "").strip()
    start_date = _parse_report_date(date_from)
    end_date = _parse_report_date(date_to)
    table_rows: list[dict] = []
    for source_row in rows:
        row = dict(source_row)
        if _billing_row_type_category(row) != "prepaid":
            continue
        invoice_balance_due = _money(row.get("prepaid_unpaid_invoice_balance_due"))
        invoice_count = int(row.get("prepaid_unpaid_invoice_count") or 0)
        if invoice_balance_due <= 0 or invoice_count <= 0:
            continue
        if any(term in str(row.get("name") or "").casefold() for term in ("dotmac", "test")):
            continue
        row_status = str(row.get("subscriber_status") or "").strip().lower()
        if normalized_status != "all" and row_status != normalized_status:
            continue
        row_plan = str(row.get("plan") or "").strip()
        if normalized_plan and row_plan.casefold() != normalized_plan.casefold():
            continue
        _enrich_expiration_fields([row])
        row_expiration_date = _parse_report_date(row.get("service_expiration_date") or row.get("expiration_date"))
        if start_date is not None and (row_expiration_date is None or row_expiration_date < start_date):
            continue
        if end_date is not None and (row_expiration_date is None or row_expiration_date > end_date):
            continue
        row["billing_type_label"] = _billing_type_display_label(row)
        row["_prepaid_unpaid_invoice_summary"] = {
            "count": invoice_count,
            "balance_due": invoice_balance_due,
            "last_invoice_date": row.get("prepaid_unpaid_last_invoice_date"),
            "next_due_date": row.get("prepaid_unpaid_next_due_date"),
        }
        _apply_prepaid_unpaid_invoice_summary([row])
        table_rows.append(row)
    return sorted(table_rows, key=lambda item: _money(item.get("detail_outstanding_balance")), reverse=True)


def _first_text(*values: object) -> str:
    for value in values:
        text_value = str(value or "").strip()
        if text_value and text_value != "0000-00-00":
            return text_value
    return ""


def _billing_payload_count(payload: dict | None, *keys: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, str) and value.strip().isdigit():
            return max(0, int(value.strip()))
        if isinstance(value, list):
            return len(value)
    return None


def _latest_payment_by_customer(payments: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for payment in payments:
        if not isinstance(payment, dict):
            continue
        customer_id = str(
            payment.get("customer_id")
            or payment.get("subscriber_id")
            or payment.get("customerId")
            or payment.get("subscriberId")
            or ""
        ).strip()
        if not customer_id:
            continue
        payment_date = _first_text(
            payment.get("date"),
            payment.get("paid_at"),
            payment.get("payment_date"),
            payment.get("created_at"),
        )
        if customer_id not in latest or payment_date > str(latest[customer_id].get("date") or ""):
            latest[customer_id] = {
                "date": payment_date,
                "amount": _money(payment.get("amount") or payment.get("paid_amount") or payment.get("total")),
            }
    return latest


def _latest_payment_for_customer(db: Session, customer_id: str) -> dict[str, object] | None:
    normalized_customer_id = str(customer_id or "").strip()
    if not normalized_customer_id:
        return None
    try:
        payments = selfcare.fetch_customer_payments(db, normalized_customer_id, page=1, per_page=1)
    except Exception:
        return None
    latest_by_customer = _latest_payment_by_customer(payments)
    if normalized_customer_id in latest_by_customer:
        return latest_by_customer[normalized_customer_id]
    if not payments:
        return None
    payment = payments[0]
    if not isinstance(payment, dict):
        return None
    payment_date = _first_text(
        payment.get("date"),
        payment.get("paid_at"),
        payment.get("payment_date"),
        payment.get("created_at"),
    )
    if not payment_date:
        return None
    return {
        "date": payment_date,
        "amount": _money(payment.get("amount") or payment.get("paid_amount") or payment.get("total")),
    }


def _postpaid_last_seen_by_customer(db: Session, customer_ids: list[str]) -> dict[str, str]:
    if not customer_ids:
        return {}
    statement = text(
        """
        select
            customer_id,
            max(coalesce(last_change, observed_at)) as last_seen_at
        from customer_uptime_snapshots
        where customer_id in :customer_ids
          and is_online = true
        group by customer_id
        """
    ).bindparams(bindparam("customer_ids", expanding=True))
    try:
        return {
            str(row.customer_id): row.last_seen_at.isoformat() if row.last_seen_at else ""
            for row in db.execute(statement, {"customer_ids": customer_ids})
        }
    except Exception:
        return {}


def _postpaid_enrich_detail_fields(
    db: Session,
    rows: list[dict],
    *,
    latest_payments_by_customer: dict[str, dict] | None = None,
) -> None:
    latest_payments_by_customer = latest_payments_by_customer or {}
    customer_ids = sorted({_billing_risk_row_customer_id(row) for row in rows if _billing_risk_row_customer_id(row)})
    last_seen_by_customer = _postpaid_last_seen_by_customer(db, customer_ids)
    for row in rows:
        customer_id = _billing_risk_row_customer_id(row)
        latest_payment = latest_payments_by_customer.get(customer_id) if customer_id else None

        last_payment_date = _first_text(
            (latest_payment or {}).get("date"),
            row.get("last_payment_date"),
            row.get("last_transaction_date"),
        )
        last_payment_amount = _money((latest_payment or {}).get("amount") or row.get("last_payment_amount"))
        next_due_date = _first_text(
            row.get("next_due_date"),
            row.get("next_bill_date"),
            row.get("service_expiration_date"),
        )
        last_invoice_date = _first_text(
            row.get("last_invoice_date"),
            row.get("invoiced_until"),
            row.get("billing_end_date"),
        )
        last_online = _first_text(
            row.get("_customer_last_online"),
            row.get("_network_last_seen_at"),
            last_seen_by_customer.get(customer_id or ""),
            row.get("last_online"),
            row.get("last_seen"),
        )
        outstanding_balance = _money(row.get("outstanding_balance") or row.get("balance"))
        days_past_due = int(row.get("days_past_due") or 0)
        overdue_balance = _money(row.get("overdue_balance"))
        if overdue_balance <= 0 and days_past_due > 0:
            overdue_balance = outstanding_balance
        unpaid_invoices = _billing_payload_count(row, "unpaid_invoices", "unpaid_invoice_count", "open_invoices")
        overdue_invoices = _billing_payload_count(row, "overdue_invoices", "overdue_invoice_count", "past_due_invoices")
        row["detail_last_payment_date"] = last_payment_date
        row["detail_last_payment_amount"] = last_payment_amount
        row["detail_next_due_date"] = next_due_date
        row["detail_unpaid_invoices"] = unpaid_invoices if unpaid_invoices is not None else int(outstanding_balance > 0)
        row["detail_overdue_invoices"] = overdue_invoices if overdue_invoices is not None else int(days_past_due > 0)
        row["detail_outstanding_balance"] = outstanding_balance
        row["detail_overdue_balance"] = overdue_balance
        row["detail_last_invoice_date"] = last_invoice_date
        row["detail_last_online"] = last_online


def _billing_risk_segments_for_customer_status(customer_status: str) -> list[str]:
    normalized = _normalize_billing_risk_customer_status(customer_status)
    if normalized == "active":
        return ["active", "due_soon"]
    if normalized == "all":
        return ["active", "due_soon", "suspended"]
    return ["suspended"]


def _billing_risk_status_rows(rows: list[dict], customer_status: str) -> list[dict]:
    normalized = _normalize_billing_risk_customer_status(customer_status)
    allowed_statuses = {"active", "suspended"} if normalized == "all" else {normalized}
    return [
        row
        for row in rows
        if str(row.get("subscriber_status") or row.get("status") or "").strip().lower() in allowed_statuses
    ]


def _billing_risk_row_customer_id(row: dict) -> str:
    return str(row.get("_external_id") or row.get("subscriber_id") or row.get("_subscriber_number") or "").strip()


def _coerce_money_value(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _service_plan_text(services_payload: object) -> str:
    if not isinstance(services_payload, list):
        return ""
    services = [service for service in services_payload if isinstance(service, dict)]
    primary_service = selfcare._select_primary_service(services)
    if not isinstance(primary_service, dict):
        return ""
    for key in ("description", "tariff_name", "plan_name", "package", "name"):
        text = str(primary_service.get(key) or "").strip()
        if text:
            return text
    return ""


def _enrich_missing_plan_fields(db: Session, rows: list[dict]) -> None:
    for row in rows:
        if str(row.get("plan") or "").strip():
            continue
        customer_id = _billing_risk_row_customer_id(row)
        if not customer_id:
            continue
        try:
            services_payload = selfcare.fetch_customer_internet_services(db, customer_id)
        except Exception:
            services_payload = []
        plan = _service_plan_text(services_payload)
        if plan:
            row["plan"] = plan


def _enrich_unknown_billing_type_fields(db: Session, rows: list[dict]) -> None:
    for row in rows:
        if _billing_row_type_category(row):
            continue
        customer_id = _billing_risk_row_customer_id(row)
        if not customer_id:
            continue
        try:
            customer = selfcare.fetch_customer(db, customer_id)
        except Exception as exc:
            logger.debug("billing_risk_billing_type_lookup_failed customer_id=%s error=%s", customer_id, exc)
            customer = None
        if not isinstance(customer, dict):
            continue
        raw_billing = customer.get("billing")
        billing: dict = raw_billing if isinstance(raw_billing, dict) else {}
        billing_mode = str(
            customer.get("billing_mode")
            or customer.get("billingMode")
            or billing.get("billing_mode")
            or billing.get("billingMode")
            or ""
        ).strip()
        subscription_billing_mode = str(
            customer.get("subscription_billing_mode")
            or customer.get("subscriptionBillingMode")
            or billing.get("subscription_billing_mode")
            or billing.get("subscriptionBillingMode")
            or ""
        ).strip()
        billing_type = str(
            customer.get("billing_type")
            or customer.get("billingType")
            or billing.get("billing_type")
            or billing.get("billingType")
            or ""
        ).strip()
        normalized = billing_risk_cache._display_billing_type(
            billing_mode,
            subscription_billing_mode,
            billing_type,
        )
        row["billing_mode"] = billing_mode
        row["subscription_billing_mode"] = subscription_billing_mode
        row["billing_type"] = normalized


def _enrich_account_balance_deposit(db: Session, rows: list[dict]) -> None:
    for row in rows:
        subscriber_status = str(row.get("subscriber_status") or row.get("status") or "").strip().lower()
        is_postpaid = _billing_row_type_category(row) == "postpaid"
        cached_account_balance_deposit = _coerce_money_value(row.get("account_balance_deposit"))
        needs_account_balance_deposit = is_postpaid and cached_account_balance_deposit in (None, 0)
        needs_service_expiration_date = subscriber_status == "active" or not any(
            str(row.get(key) or "").strip() for key in ("blocked_date", "next_bill_date", "billing_end_date")
        )
        if not needs_account_balance_deposit and not needs_service_expiration_date:
            continue
        customer_id = _billing_risk_row_customer_id(row)
        if not customer_id:
            continue
        try:
            billing_payload = selfcare.fetch_customer_billing(db, customer_id)
        except Exception:
            billing_payload = None
        if isinstance(billing_payload, dict):
            if needs_account_balance_deposit:
                row["account_balance_deposit"] = _coerce_money_value(billing_payload.get("deposit"))
            if needs_service_expiration_date:
                service_expiration_date = billing_risk_service._service_expiration_date_from_billing(
                    billing_payload,
                    subscriber_status=subscriber_status,
                )
                if service_expiration_date:
                    row["next_bill_date"] = service_expiration_date
                    row["billing_end_date"] = service_expiration_date
                elif subscriber_status == "active":
                    row["next_bill_date"] = ""
                    row["billing_end_date"] = ""


def _active_toggle_uptime_rows(db: Session, rows: list[dict], customer_status: str) -> list[dict]:
    if _normalize_billing_risk_customer_status(customer_status) != "active" or not rows:
        return rows

    today = datetime.now(UTC).date()
    customer_ids = sorted({_billing_risk_row_customer_id(row) for row in rows if _billing_risk_row_customer_id(row)})
    if not customer_ids:
        return []

    statement = text(
        """
        with latest_service as (
            select distinct on (customer_id, coalesce(service_id, ''))
                customer_id,
                service_id,
                is_online,
                observed_at
            from customer_uptime_snapshots
            where customer_id in :customer_ids
            order by customer_id, coalesce(service_id, ''), observed_at desc
        ),
        latest_customer as (
            select
                customer_id,
                bool_or(is_online) as is_online,
                max(observed_at) as observed_at
            from latest_service
            group by customer_id
        ),
        online_history as (
            select
                customer_id,
                max(coalesce(last_change, observed_at)) as last_seen_at
            from customer_uptime_snapshots
            where customer_id in :customer_ids
              and is_online = true
            group by customer_id
        )
        select
            latest_customer.customer_id,
            latest_customer.is_online,
            latest_customer.observed_at,
            online_history.last_seen_at
        from latest_customer
        left join online_history on online_history.customer_id = latest_customer.customer_id
        """
    ).bindparams(bindparam("customer_ids", expanding=True))
    uptime_by_customer = {
        str(row.customer_id): {
            "is_online": bool(row.is_online),
            "observed_at": row.observed_at,
            "last_seen_at": row.last_seen_at,
        }
        for row in db.execute(statement, {"customer_ids": customer_ids})
    }

    filtered_rows: list[dict] = []
    for row in rows:
        customer_id = _billing_risk_row_customer_id(row)
        uptime = uptime_by_customer.get(customer_id)
        if uptime and uptime["is_online"]:
            row["_network_is_online"] = True
            row["_network_observed_at"] = uptime["observed_at"]
            filtered_rows.append(row)
            continue

        last_seen_date = _parse_report_date(uptime.get("last_seen_at") if uptime else None) or _parse_report_date(
            row.get("_customer_last_online")
        )
        if last_seen_date is not None and ACTIVE_OFFLINE_LAST_SEEN_START <= last_seen_date <= today:
            row["_network_is_online"] = False
            row["_network_last_seen_at"] = last_seen_date.isoformat()
            filtered_rows.append(row)

    return filtered_rows


def _parse_report_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text == "0000-00-00":
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _enrich_expiration_fields(rows: list[dict]) -> None:
    today = datetime.now(UTC).date()
    for row in rows:
        subscriber_status = str(row.get("subscriber_status") or row.get("status") or "").strip().lower()
        uses_billing_cutoff = subscriber_status in {"active", "suspended"}
        is_postpaid = _billing_row_type_category(row) == "postpaid"
        expiration_date = _parse_report_date(
            row.get("blocked_date") or row.get("next_bill_date") or row.get("billing_end_date")
        )
        if uses_billing_cutoff and expiration_date is not None:
            row["expiration_date"] = expiration_date.isoformat()
            row["service_expiration_date"] = expiration_date.isoformat()
            row["remaining_days"] = (expiration_date - today).days
        else:
            row["expiration_date"] = ""
            row["service_expiration_date"] = ""
            row["remaining_days"] = None
        if is_postpaid:
            account_balance_deposit = _coerce_money_value(row.get("account_balance_deposit"))
            if account_balance_deposit in (None, 0):
                account_balance_deposit = _coerce_money_value(row.get("balance"))
            if account_balance_deposit is None:
                account_balance_deposit = 0.0
            row["revenue_owed"] = float(account_balance_deposit)
            row["postpaid_remaining_days"] = (expiration_date - today).days if expiration_date is not None else None
        else:
            row["revenue_owed"] = None
            row["postpaid_remaining_days"] = None


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


def _billing_risk_cache_available(db: Session | object) -> bool:
    return hasattr(db, "query")


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
    enterprise_only: bool = False,
    customer_segment: str | None = None,
    location: str | None = None,
    mrr_sort: str | None = None,
    customer_status: str = "suspended",
    billing_type: str = "all",
) -> tuple[list[dict], dict[str, int | float], bool]:
    requested_page_size = max(1, int(page_size))
    churn_rows = billing_risk_service.get_billing_risk_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=days_past_due,
        limit=requested_page_size + 1,
        page=max(1, int(page)),
        page_size=requested_page_size + 1,
        search=search,
        overdue_bucket=overdue_bucket,
        enterprise_only=enterprise_only,
        customer_segment=customer_segment,
        location=location,
        mrr_sort=mrr_sort,
        enrich_visible_rows=False,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    churn_rows = _billing_risk_status_rows(churn_rows, customer_status)
    churn_rows = _active_toggle_uptime_rows(db, churn_rows, customer_status)
    churn_rows = _billing_risk_billing_type_rows(churn_rows, billing_type)
    has_next = len(churn_rows) > requested_page_size
    visible_rows = [dict(row) for row in churn_rows[:requested_page_size]]
    if not str(search or "").strip():
        billing_risk_service.enrich_billing_risk_rows(visible_rows)
    _enrich_missing_plan_fields(db, visible_rows)
    _enrich_missing_blocked_fields(visible_rows, force_live=False)
    _enrich_account_balance_deposit(db, visible_rows)
    _enrich_expiration_fields(visible_rows)
    return visible_rows, _billing_risk_page_metrics(visible_rows), has_next


def _billing_risk_rows_source(
    db: Session,
    *,
    due_soon_days: int,
    high_balance_only: bool,
    segment: str | None,
    selected_segments: list[str],
    days_past_due: str | None,
    search: str | None,
    overdue_bucket: str | None,
    enterprise_only: bool = False,
    customer_segment: str | None = None,
    location: str | None = None,
    mrr_sort: str | None = None,
    limit: int = 6000,
) -> tuple[list[dict], dict[str, object]]:
    normalized_customer_segment = customer_segment or "all"
    normalized_enterprise_only = bool(enterprise_only)
    normalized_location = (location if isinstance(location, str) else "").strip()
    if (
        settings.billing_risk_route_use_cache
        and _billing_risk_cache_available(db)
        and not normalized_enterprise_only
        and normalized_customer_segment == "all"
    ):
        rows = billing_risk_cache.all_cached_rows(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=days_past_due,
            search=search,
            overdue_bucket=overdue_bucket,
            location=normalized_location,
            limit=limit,
        )
        return rows, {
            "mode": "cache",
            "metadata": billing_risk_cache.cache_metadata(db),
        }
    rows = billing_risk_service.get_billing_risk_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=days_past_due,
        limit=limit,
        search=search,
        overdue_bucket=overdue_bucket,
        enterprise_only=normalized_enterprise_only,
        customer_segment=normalized_customer_segment,
        location=normalized_location,
        mrr_sort=mrr_sort,
        enrich_visible_rows=False,
    )
    return rows, {"mode": "live", "metadata": {"row_count": len(rows)}}


def _billing_risk_location_options(
    db: Session,
    *,
    due_soon_days: int,
    segment: str | None,
    selected_location: str | None = None,
) -> list[str]:
    def _location_key_text(value: object) -> str:
        text_value = str(value or "").strip()
        if text_value.casefold().startswith("address:"):
            text_value = text_value.split(":", 1)[1].strip()
        return billing_risk_cache._display_location(text_value)

    try:
        selfcare_locations = selfcare.fetch_locations(db)
    except Exception:
        logger.exception("Failed to load Selfcare billing-risk location options")
        selfcare_locations = []
    locations = []
    for location in selfcare_locations:
        if not isinstance(location, dict):
            continue
        location_name = billing_risk_cache._display_location(location.get("name"))
        if not location_name:
            continue
        location_key = _location_key_text(location.get("id"))
        if location_key and location_key.casefold() == location_name.casefold():
            continue
        locations.append(location_name)
    if locations:
        normalized_locations = {location for location in locations if location}
        if selected_location:
            normalized_locations.add(str(selected_location).strip())
        return sorted(normalized_locations, key=str.casefold)

    selected_segments = ["active", "due_soon", "suspended"]
    if settings.billing_risk_route_use_cache and _billing_risk_cache_available(db):
        locations = billing_risk_cache.location_options_cached(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=False,
            selected_segments=selected_segments,
            days_past_due=None,
            search=None,
            overdue_bucket="all",
        )
    else:
        rows, _ = _billing_risk_rows_source(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=False,
            segment=segment,
            selected_segments=selected_segments,
            days_past_due=None,
            search=None,
            overdue_bucket="all",
            enterprise_only=False,
            customer_segment="all",
            location="",
            mrr_sort=None,
            limit=10000,
        )
        locations = [str(row.get("location") or "").strip() for row in rows if str(row.get("location") or "").strip()]
    normalized_locations = {str(location or "").strip() for location in locations if str(location or "").strip()}
    if selected_location:
        normalized_locations.add(str(selected_location).strip())
    return sorted(normalized_locations, key=str.casefold)


def _billing_risk_cached_page_rows(
    db: Session,
    *,
    due_soon_days: int,
    high_balance_only: bool,
    selected_segments: list[str],
    days_past_due: str | None,
    page: int,
    page_size: int,
    search: str | None,
    overdue_bucket: str | None,
    location: str | None = None,
    customer_status: str = "suspended",
    billing_type: str = "all",
) -> tuple[list[dict], dict[str, int | float], bool]:
    requested_page_size = max(1, int(page_size))
    requested_page = max(1, int(page))
    cached_rows = billing_risk_cache.all_cached_rows(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=days_past_due,
        search=search,
        overdue_bucket=overdue_bucket,
        location=location,
        limit=10000,
    )
    filtered_rows = _billing_risk_status_rows([dict(row) for row in cached_rows], customer_status)
    filtered_rows = _active_toggle_uptime_rows(db, filtered_rows, customer_status)
    filtered_rows = _billing_risk_billing_type_rows(filtered_rows, billing_type)
    start = (requested_page - 1) * requested_page_size
    end = start + requested_page_size
    visible_rows = filtered_rows[start:end]
    _enrich_missing_plan_fields(db, visible_rows)
    _enrich_unknown_billing_type_fields(db, visible_rows)
    _enrich_missing_blocked_fields(visible_rows, force_live=False)
    _enrich_account_balance_deposit(db, visible_rows)
    _enrich_expiration_fields(visible_rows)
    return visible_rows, _billing_risk_page_metrics(visible_rows), len(filtered_rows) > end


def _billing_risk_initial_rows(
    db: Session,
    churn_rows: list[dict],
    *,
    page_size: int,
    customer_status: str = "suspended",
    billing_type: str = "all",
) -> tuple[list[dict], dict[str, int | float], bool]:
    churn_rows = _billing_risk_status_rows(churn_rows, customer_status)
    churn_rows = _active_toggle_uptime_rows(db, churn_rows, customer_status)
    churn_rows = _billing_risk_billing_type_rows(churn_rows, billing_type)
    has_next = len(churn_rows) > page_size
    visible_rows = [dict(row) for row in churn_rows[:page_size]]
    billing_risk_service.enrich_billing_risk_rows(visible_rows)
    _enrich_missing_plan_fields(db, visible_rows)
    _enrich_unknown_billing_type_fields(db, visible_rows)
    _enrich_missing_blocked_fields(visible_rows, force_live=False)
    _enrich_account_balance_deposit(db, visible_rows)
    _enrich_expiration_fields(visible_rows)
    return visible_rows, _billing_risk_page_metrics(visible_rows), has_next


def _billing_risk_unfiltered_at_risk_count(
    db: Session,
    *,
    due_soon_days: int,
    segment: str | None,
    selected_segments: list[str],
) -> int:
    rows, _route_state = _billing_risk_rows_source(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=False,
        segment=segment,
        selected_segments=selected_segments,
        days_past_due=None,
        search=None,
        overdue_bucket="all",
        enterprise_only=False,
        customer_segment="all",
        location="",
        mrr_sort=None,
        limit=10000,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        rows = [row for row in rows if str(row.get("risk_segment") or "") in selected_labels]
    return len(_billing_risk_status_rows(rows, "all"))


def _billing_risk_unfiltered_kpis(
    db: Session,
    *,
    due_soon_days: int,
    segment: str | None,
    selected_segments: list[str],
    overdue_invoices: list[dict] | None = None,
) -> dict[str, float | int]:
    if settings.billing_risk_route_use_cache and _billing_risk_cache_available(db):
        overdue_invoice_balance = round(
            sum(float(row.get("total_balance_due") or 0) for row in overdue_invoices or []),
            2,
        )
        return billing_risk_cache.summary_cached(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=False,
            selected_segments=selected_segments,
            days_past_due=None,
            search=None,
            overdue_bucket="all",
            location="",
            overdue_invoice_balance=overdue_invoice_balance,
        )

    rows, _route_state = _billing_risk_rows_source(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=False,
        segment=segment,
        selected_segments=selected_segments,
        days_past_due=None,
        search=None,
        overdue_bucket="all",
        enterprise_only=False,
        customer_segment="all",
        location="",
        mrr_sort=None,
        limit=10000,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        rows = [row for row in rows if str(row.get("risk_segment") or "") in selected_labels]
    rows = _billing_risk_status_rows(rows, "all")
    return billing_risk_service.get_billing_risk_summary(rows, overdue_invoices or [])


def _billing_risk_unfiltered_blocked_buckets(
    db: Session,
    *,
    due_soon_days: int,
    segment: str | None,
) -> list[dict[str, int | str]]:
    rows, _route_state = _billing_risk_rows_source(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=False,
        segment=segment,
        selected_segments=["suspended"],
        days_past_due=None,
        search=None,
        overdue_bucket="all",
        enterprise_only=False,
        customer_segment="all",
        location="",
        mrr_sort=None,
        limit=10000,
    )
    return _blocked_days_buckets(_billing_risk_status_rows(rows, "suspended"))


def _retention_rep_options(db: Session) -> list[dict[str, str]]:
    target_team_names = {
        "helpdesk",
        "help desk",
        "enterprise sales",
        "customer support",
        "sales (call center)",
        "sales call center",
    }
    target_departments = {
        "helpdesk",
        "help_desk",
        "enterprise_sales",
        "customer_support",
        "sales_call_center",
    }
    options_by_person_id: dict[str, dict[str, str]] = {}
    fixed_options_by_label: dict[str, dict[str, str]] = {
        rep_name.casefold(): {
            "value": f"manual:{rep_name.casefold().replace(' ', '-')}",
            "label": rep_name,
            "team": "",
            "person_id": "",
        }
        for rep_name in RETENTION_FIXED_REP_NAMES
    }
    for rep_label, team_label in RETENTION_REP_TEAM_OVERRIDES.items():
        formatted_label = " ".join(part.capitalize() for part in rep_label.split())
        fixed_options_by_label.setdefault(
            rep_label,
            {
                "value": f"manual:{rep_label.replace(' ', '-')}",
                "label": formatted_label,
                "team": team_label,
                "person_id": "",
            },
        )

    service_team_rows = db.execute(
        select(Person.id, Person.display_name, Person.first_name, Person.last_name, Person.email, ServiceTeam.name)
        .select_from(ServiceTeamMember)
        .join(ServiceTeam, ServiceTeam.id == ServiceTeamMember.team_id)
        .join(Person, Person.id == ServiceTeamMember.person_id)
        .where(
            ServiceTeam.is_active.is_(True),
            ServiceTeamMember.is_active.is_(True),
            Person.is_active.is_(True),
        )
    ).all()
    for person_id, display_name, first_name, last_name, email, team_name in service_team_rows:
        team_key = str(team_name or "").strip().lower()
        team_department_key = team_key.replace(" ", "_")
        if team_key not in target_team_names and team_department_key not in target_departments:
            continue
        label = str(display_name or f"{first_name or ''} {last_name or ''}".strip() or email or "Unnamed rep").strip()
        team_label = "" if label.casefold() in RETENTION_REP_LABEL_ONLY_NAMES else str(team_name or "").strip()
        team_label = RETENTION_REP_TEAM_OVERRIDES.get(label.casefold(), team_label)
        options_by_person_id[str(person_id)] = {
            "value": str(person_id),
            "label": label,
            "team": team_label,
            "person_id": str(person_id),
        }

    crm_team_rows = db.execute(
        select(Person.id, Person.display_name, Person.first_name, Person.last_name, Person.email, CrmTeam.name)
        .select_from(CrmAgentTeam)
        .join(CrmTeam, CrmTeam.id == CrmAgentTeam.team_id)
        .join(CrmAgent, CrmAgent.id == CrmAgentTeam.agent_id)
        .join(Person, Person.id == CrmAgent.person_id)
        .where(
            CrmTeam.is_active.is_(True),
            CrmAgentTeam.is_active.is_(True),
            CrmAgent.is_active.is_(True),
            Person.is_active.is_(True),
        )
    ).all()
    for person_id, display_name, first_name, last_name, email, team_name in crm_team_rows:
        team_key = str(team_name or "").strip().lower()
        if team_key not in target_team_names:
            continue
        label = str(display_name or f"{first_name or ''} {last_name or ''}".strip() or email or "Unnamed rep").strip()
        team_label = "" if label.casefold() in RETENTION_REP_LABEL_ONLY_NAMES else str(team_name or "").strip()
        team_label = RETENTION_REP_TEAM_OVERRIDES.get(label.casefold(), team_label)
        options_by_person_id.setdefault(
            str(person_id),
            {
                "value": str(person_id),
                "label": label,
                "team": team_label,
                "person_id": str(person_id),
            },
        )

    # Resolve manual rep cards to real people where possible so engagements can persist person IDs.
    manual_names = set(RETENTION_REP_TEAM_OVERRIDES.keys())
    first_name_candidates = {name.split()[0] for name in manual_names if name.split()}
    possible_people = db.execute(
        select(Person.id, Person.display_name, Person.first_name, Person.last_name, Person.email).where(
            Person.is_active.is_(True),
            func.lower(func.coalesce(Person.first_name, "")).in_(first_name_candidates),
        )
    ).all()
    for row in possible_people:
        if len(row) < 5:
            continue
        person_id, display_name, first_name, last_name, email = row[:5]
        label = str(display_name or f"{first_name or ''} {last_name or ''}".strip() or email or "").strip()
        normalized_label = label.casefold()
        if normalized_label not in manual_names:
            continue
        team_label = RETENTION_REP_TEAM_OVERRIDES[normalized_label]
        fixed_options_by_label[normalized_label] = {
            "value": str(person_id),
            "label": label,
            "team": team_label,
            "person_id": str(person_id),
        }

    for option in options_by_person_id.values():
        fixed_options_by_label[option["label"].casefold()] = option

    return sorted(
        fixed_options_by_label.values(),
        key=lambda option: (option["team"].casefold(), option["label"].casefold()),
    )


def _parse_follow_up_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid follow-up date") from exc


def _retention_engagement_payload(row: CustomerRetentionEngagement) -> dict[str, str | None]:
    return {
        "id": str(row.id),
        "customerId": row.customer_external_id,
        "customerName": row.customer_name,
        "outcome": row.outcome,
        "note": row.note or "",
        "followUp": row.follow_up_date.isoformat() if row.follow_up_date else "",
        "rep": row.rep_label or "",
        "repPersonId": str(row.rep_person_id) if row.rep_person_id else "",
        "createdAt": row.created_at.isoformat() if row.created_at else "",
    }


def _retention_engagements_by_customer(db: Session, customer_ids: list[str]) -> dict[str, list[dict[str, str | None]]]:
    normalized_ids = sorted(
        {str(customer_id or "").strip() for customer_id in customer_ids if str(customer_id or "").strip()}
    )
    if not normalized_ids:
        return {}
    rows = db.scalars(
        select(CustomerRetentionEngagement)
        .where(
            CustomerRetentionEngagement.customer_external_id.in_(normalized_ids),
            CustomerRetentionEngagement.is_active.is_(True),
        )
        .order_by(CustomerRetentionEngagement.created_at.desc())
    ).all()
    grouped: dict[str, list[dict[str, str | None]]] = {customer_id: [] for customer_id in normalized_ids}
    for row in rows:
        customer_id = str(row.customer_external_id or "").strip()
        if customer_id:
            grouped.setdefault(customer_id, []).append(_retention_engagement_payload(row))
    return grouped


def _retention_active_customer_ids(db: Session) -> list[str]:
    rows = db.execute(
        select(CustomerRetentionEngagement.customer_external_id)
        .where(CustomerRetentionEngagement.is_active.is_(True))
        .group_by(CustomerRetentionEngagement.customer_external_id)
        .order_by(func.max(CustomerRetentionEngagement.created_at).desc())
    ).all()
    customer_ids: list[str] = []
    for (customer_id,) in rows:
        normalized = str(customer_id or "").strip()
        if normalized:
            customer_ids.append(normalized)
    return customer_ids


def _retention_search_customer_ids(db: Session, search: str) -> list[str]:
    term = str(search or "").strip()
    if not term:
        return []
    if not hasattr(db, "query"):
        return []
    like_term = f"%{term}%"
    rows = (
        db.query(CustomerRetentionEngagement.customer_external_id)
        .filter(CustomerRetentionEngagement.is_active.is_(True))
        .filter(
            or_(
                CustomerRetentionEngagement.customer_external_id.ilike(like_term),
                CustomerRetentionEngagement.customer_name.ilike(like_term),
                CustomerRetentionEngagement.note.ilike(like_term),
                CustomerRetentionEngagement.rep_label.ilike(like_term),
            )
        )
        .order_by(CustomerRetentionEngagement.created_at.desc())
        .all()
    )
    customer_ids: list[str] = []
    seen: set[str] = set()
    for (customer_id,) in rows:
        normalized = str(customer_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        customer_ids.append(normalized)
    return customer_ids


def _retention_customer_id(row: dict) -> str:
    return str(row.get("_external_id") or row.get("subscriber_id") or row.get("_subscriber_number") or "").strip()


def _export_text(value: object, default: str = "-") -> str:
    text = str(value or "").strip()
    return text or default


def _export_currency(value: object) -> str:
    try:
        amount = float(str(value or 0))
    except (TypeError, ValueError):
        amount = 0.0
    return f"₦{amount:,.2f}"


def _export_int(value: object) -> int:
    try:
        return int(float(str(value or 0)))
    except (TypeError, ValueError):
        return 0


def _blocked_for_export_label(row: dict) -> str:
    blocked_for_days = row.get("blocked_for_days")
    if blocked_for_days in (None, ""):
        blocked_date_text = str(row.get("blocked_date") or "").strip()[:10]
        try:
            blocked_date = date.fromisoformat(blocked_date_text)
        except ValueError:
            return "-"
        blocked_for_days = max(0, (datetime.now(UTC).date() - blocked_date).days)
    try:
        days = int(float(str(blocked_for_days)))
    except (TypeError, ValueError):
        return "-"
    return f"Blocked for {days} days"


def _coerce_blocked_days_value(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return int(float(normalized))
    except (TypeError, ValueError):
        pass
    match = re.search(r"-?\d+\.?\d*", normalized)
    if match is None:
        return None
    try:
        return int(float(match.group(0)))
    except (TypeError, ValueError):
        return None


def _normalize_blocked_date_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"n/a", "na", "-", "none", "null", "0000-00-00"}:
        return ""
    return text


def _blocked_days_buckets(churn_rows: list[dict]) -> list[dict[str, int | str]]:
    blocked_counts = {
        "Blocked 0-7 Days": 0,
        "Blocked 8-30 Days": 0,
        "Blocked 31-60 Days": 0,
        "Blocked 61+ Days": 0,
    }
    today = datetime.now(UTC).date()

    def _coerce_row_blocked_days(row: dict) -> int | None:
        blocked_days_value = _coerce_blocked_days_value(row.get("blocked_for_days"))
        if blocked_days_value is not None:
            return blocked_days_value
        blocked_date_text = str(row.get("blocked_date") or "").strip()
        parsed_blocked_date = billing_risk_service._parse_iso_date_text(blocked_date_text)
        if parsed_blocked_date is not None:
            return max(0, (today - parsed_blocked_date).days)
        days_past_due = _coerce_blocked_days_value(row.get("days_past_due"))
        if days_past_due is not None and days_past_due >= 0:
            return days_past_due
        return None

    for row in churn_rows:
        blocked_days = _coerce_row_blocked_days(row)
        if blocked_days is None:
            continue

        segment_value = str(row.get("risk_segment") or "").strip()
        if segment_value not in {"Suspended", "Churned"}:
            continue

        if blocked_days <= 7:
            blocked_counts["Blocked 0-7 Days"] += 1
        elif blocked_days <= 30:
            blocked_counts["Blocked 8-30 Days"] += 1
        elif blocked_days <= 60:
            blocked_counts["Blocked 31-60 Days"] += 1
        else:
            blocked_counts["Blocked 61+ Days"] += 1

    return [{"label": label, "count": count} for label, count in blocked_counts.items()]


def _safe_live_blocked_dates(
    external_ids: list[str],
    *,
    force_live: bool = False,
    blocking_only_external_ids: list[str] | None = None,
) -> dict[str, str]:
    if not external_ids:
        return {}
    try:
        return billing_risk_service.get_live_blocked_dates(
            external_ids,
            force_live=force_live,
            blocking_only_external_ids=blocking_only_external_ids,
        )
    except TypeError as exc:
        # Keep compatibility with monkeypatched/test doubles that don't accept force_live kwarg.
        if "force_live" in str(exc) or "blocking_only_external_ids" in str(exc):
            try:
                return billing_risk_service.get_live_blocked_dates(external_ids)
            except Exception:
                return {}
        return {}
    except Exception:
        return {}


def _enrich_missing_blocked_fields(churn_rows: list[dict], *, force_live: bool = False) -> None:
    if not churn_rows:
        return

    today = datetime.now(UTC).date()

    def _status_and_segment_rules(row: dict) -> tuple[bool, bool]:
        subscriber_status = str(row.get("subscriber_status") or "").strip().lower()
        risk_segment = str(row.get("risk_segment") or "").strip()
        should_hide_blocked_for = subscriber_status == "active"
        is_blocked_like = subscriber_status in {"blocked", "suspended"} or risk_segment in {"Suspended", "Churned"}
        return should_hide_blocked_for, is_blocked_like

    def _infer_from_blocking_period(row: dict) -> int | None:
        blocking_period = _coerce_blocked_days_value(row.get("blocking_period"))
        days_past_due = _coerce_blocked_days_value(row.get("days_past_due"))
        if blocking_period is None or days_past_due is None:
            return None
        return max(0, days_past_due - blocking_period)

    def _infer_from_customer_last_update(row: dict) -> tuple[int, str] | None:
        parsed = billing_risk_service._parse_iso_date_text(str(row.get("_customer_last_update") or ""))
        if parsed is None:
            return None
        return max(0, (today - parsed).days), parsed.strftime("%Y-%m-%d")

    def _is_invalid_blocked_date_fallback(row: dict, blocked_date_text: str) -> bool:
        if not blocked_date_text:
            return False
        billing_start_text = _normalize_blocked_date_text(row.get("billing_start_date"))
        return bool(billing_start_text and blocked_date_text == billing_start_text)

    external_ids = sorted(
        {str(row.get("_external_id") or "").strip() for row in churn_rows if str(row.get("_external_id") or "").strip()}
    )
    # Preserve report blocked_date when present; live Splynx blocking_date can be stale.
    live_blocked_dates = _safe_live_blocked_dates(
        external_ids,
        force_live=force_live,
    )
    target_rows: list[tuple[dict, str]] = []
    missing_external_ids: set[str] = set()
    for row in churn_rows:
        external_id = str(row.get("_external_id") or "").strip()
        should_hide_blocked_for, is_blocked_like = _status_and_segment_rules(row)
        if should_hide_blocked_for:
            row["blocked_date"] = ""
            row["blocked_for_days"] = None
            continue
        existing_blocked_date = _normalize_blocked_date_text(row.get("blocked_date"))
        if _is_invalid_blocked_date_fallback(row, existing_blocked_date):
            row["blocked_date"] = ""
            existing_blocked_date = ""
        if existing_blocked_date:
            blocked_for_days = _coerce_blocked_days_value(row.get("blocked_for_days"))
            if blocked_for_days is None:
                parsed_existing_blocked_date = billing_risk_service._parse_iso_date_text(existing_blocked_date)
                if parsed_existing_blocked_date is not None and is_blocked_like:
                    row["blocked_for_days"] = max(0, (today - parsed_existing_blocked_date).days)
            continue
        if external_id and external_id in live_blocked_dates:
            live_blocked_date = live_blocked_dates.get(external_id, "")
            if live_blocked_date:
                row["blocked_date"] = live_blocked_date
                parsed_live_blocked_date = billing_risk_service._parse_iso_date_text(live_blocked_date)
                if parsed_live_blocked_date is not None and is_blocked_like:
                    row["blocked_for_days"] = max(0, (today - parsed_live_blocked_date).days)
                else:
                    row["blocked_for_days"] = None
                # Live Splynx blocked date is authoritative for this row.
                continue

        blocked_date_text = _normalize_blocked_date_text(row.get("blocked_date"))
        if _is_invalid_blocked_date_fallback(row, blocked_date_text):
            row["blocked_date"] = ""
            blocked_date_text = ""
        blocked_for_days = _coerce_blocked_days_value(row.get("blocked_for_days"))
        if blocked_for_days is not None:
            row["blocked_for_days"] = blocked_for_days
        else:
            parsed_blocked_date = billing_risk_service._parse_iso_date_text(blocked_date_text)
            if parsed_blocked_date is not None:
                blocked_for_days = max(0, (today - parsed_blocked_date).days)
                row["blocked_for_days"] = blocked_for_days

        if not is_blocked_like and not blocked_date_text:
            continue
        if blocked_date_text and blocked_for_days is not None:
            continue
        if blocked_for_days is None:
            inferred_blocked_days = _infer_from_blocking_period(row)
            if inferred_blocked_days is not None and is_blocked_like:
                row["blocked_for_days"] = inferred_blocked_days
                blocked_for_days = inferred_blocked_days
                if not blocked_date_text and inferred_blocked_days > 0:
                    row["blocked_date"] = (today - timedelta(days=inferred_blocked_days)).strftime("%Y-%m-%d")
                    blocked_date_text = row["blocked_date"]
            else:
                days_past_due = _coerce_blocked_days_value(row.get("days_past_due"))
                if days_past_due is not None and days_past_due >= 0 and is_blocked_like:
                    row["blocked_for_days"] = days_past_due
                    blocked_for_days = days_past_due
                else:
                    inferred_from_update = _infer_from_customer_last_update(row)
                    if inferred_from_update is not None and is_blocked_like:
                        inferred_days, inferred_date_text = inferred_from_update
                        row["blocked_for_days"] = inferred_days
                        blocked_for_days = inferred_days
                        if not blocked_date_text:
                            row["blocked_date"] = inferred_date_text
                            blocked_date_text = inferred_date_text

        if blocked_for_days is not None:
            continue

        if not external_id:
            continue
        target_rows.append((row, external_id))
        missing_external_ids.add(external_id)

    blocked_dates = {
        external_id: live_blocked_dates.get(external_id, "") for external_id in sorted(missing_external_ids)
    }
    for row, external_id in target_rows:
        should_hide_blocked_for, is_blocked_like = _status_and_segment_rules(row)
        if should_hide_blocked_for:
            row["blocked_for_days"] = None
            continue

        row_blocked_date_text = _normalize_blocked_date_text(row.get("blocked_date"))
        if _is_invalid_blocked_date_fallback(row, row_blocked_date_text):
            row["blocked_date"] = ""
            row_blocked_date_text = ""
        blocked_date_text = row_blocked_date_text
        if not row_blocked_date_text:
            live_blocked_date = blocked_dates.get(external_id, "")
            if live_blocked_date:
                row["blocked_date"] = live_blocked_date
                row_blocked_date_text = live_blocked_date
            blocked_date_text = row_blocked_date_text
        blocked_for_days = _coerce_blocked_days_value(row.get("blocked_for_days"))
        if blocked_for_days in (None, ""):
            parsed_blocked_date = billing_risk_service._parse_iso_date_text(blocked_date_text)
            if parsed_blocked_date is not None:
                row["blocked_for_days"] = max(0, (today - parsed_blocked_date).days)
                continue
            inferred_blocked_days = _infer_from_blocking_period(row)
            if inferred_blocked_days is not None and is_blocked_like:
                row["blocked_for_days"] = inferred_blocked_days
                if not blocked_date_text and inferred_blocked_days > 0:
                    row["blocked_date"] = (today - timedelta(days=inferred_blocked_days)).strftime("%Y-%m-%d")
                continue
            days_past_due = _coerce_blocked_days_value(row.get("days_past_due"))
            if days_past_due is not None and days_past_due >= 0 and is_blocked_like:
                row["blocked_for_days"] = days_past_due
                continue
            inferred_from_update = _infer_from_customer_last_update(row)
            if inferred_from_update is not None and is_blocked_like:
                inferred_days, inferred_date_text = inferred_from_update
                row["blocked_for_days"] = inferred_days
                if not blocked_date_text:
                    row["blocked_date"] = inferred_date_text


def _billing_risk_visible_export_rows(
    db: Session,
    churn_rows: list[dict],
) -> list[dict[str, object]]:
    engagement_history = _retention_engagements_by_customer(db, [_retention_customer_id(row) for row in churn_rows])
    export_rows: list[dict[str, object]] = []
    for row in churn_rows:
        customer_id = _retention_customer_id(row)
        customer_engagements = engagement_history.get(customer_id) or []
        latest_engagement = customer_engagements[0] if customer_engagements else None
        export_rows.append(
            {
                "Name": _export_text(row.get("name")),
                "Phone": _export_text(row.get("phone")),
                "City": _export_text(row.get("city")),
                "Street": _export_text(row.get("street")),
                "Area": _export_text(row.get("area")),
                "Plan": _export_text(row.get("plan")),
                "MRR Total": _export_currency(row.get("mrr_total")),
                "Status": _export_text(row.get("subscriber_status")),
                "Risk Segment": _export_text(row.get("risk_segment")),
                "Billing Start Date": _export_text(row.get("billing_start_date")),
                "Billing Type": _billing_type_display_label(row),
                "Expiration Date": _export_text(row.get("expiration_date")),
                "Remaining Days": _export_text(row.get("remaining_days")),
                "Revenue Owed": _export_currency(row.get("revenue_owed")),
                "Service Expiration Date": _export_text(row.get("service_expiration_date")),
                "Postpaid Remaining Days": _export_text(row.get("postpaid_remaining_days")),
                "Blocked Date": _export_text(row.get("blocked_date")),
                "Blocked For": _blocked_for_export_label(row),
                "Tickets Open": _export_int(row.get("open_tickets")),
                "Tickets Closed": _export_int(row.get("closed_tickets")),
                "Tickets Total": _export_int(row.get("total_tickets")),
                "Last Outcome": _export_text(latest_engagement.get("outcome") if latest_engagement else None),
                "Follow-up": _export_text(latest_engagement.get("followUp") if latest_engagement else None),
            }
        )
    return export_rows


def _pipeline_stage_from_engagement(engagement: dict[str, str | None] | None, row: dict | None = None) -> str:
    if not engagement:
        risk_segment = str((row or {}).get("risk_segment") or "").strip()
        if risk_segment == "Churned":
            return "Lost"
        return "Contacted"
    outcome = str(engagement.get("outcome") or "").strip()
    follow_up = str(engagement.get("followUp") or "").strip()
    if outcome in {"Renewing", "Paid", "Resolved"}:
        return "Resolved"
    if outcome in {"Lost", "Churning", "Do Not Reach Out"}:
        return "Lost"
    if outcome == "Promised to Pay":
        return "Promised to Pay"
    if follow_up:
        return "Follow-up Pending"
    return "Contacted"


def _days_until_follow_up(follow_up: str, *, today: date | None = None) -> int | None:
    try:
        follow_up_date = date.fromisoformat(str(follow_up or "").strip())
    except ValueError:
        return None
    return (follow_up_date - (today or datetime.now(UTC).date())).days


def _retention_rows_with_pipeline(
    tracker_rows: list[dict],
    engagement_history: dict[str, list[dict[str, str | None]]],
) -> list[dict]:
    enriched_rows: list[dict] = []
    for row in tracker_rows:
        customer_id = _retention_customer_id(row)
        customer_engagements = engagement_history.get(customer_id) or []
        latest_engagement = customer_engagements[0] if customer_engagements else None
        candidate = dict(row)
        candidate["retention_customer_id"] = customer_id
        candidate["latest_engagement"] = latest_engagement
        candidate["pipeline_stage"] = _pipeline_stage_from_engagement(latest_engagement, candidate)
        if latest_engagement:
            candidate["latest_follow_up"] = latest_engagement.get("followUp") or ""
            candidate["latest_rep"] = latest_engagement.get("rep") or ""
        else:
            candidate["latest_follow_up"] = ""
            candidate["latest_rep"] = ""
        days_until_follow_up = _days_until_follow_up(str(candidate.get("latest_follow_up") or ""))
        candidate["follow_up_due_label"] = ""
        if days_until_follow_up is not None:
            if days_until_follow_up < 0:
                candidate["follow_up_due_label"] = f"{abs(days_until_follow_up)} days overdue"
            elif days_until_follow_up == 0:
                candidate["follow_up_due_label"] = "Due today"
            else:
                candidate["follow_up_due_label"] = f"Due in {days_until_follow_up} days"
        enriched_rows.append(candidate)
    return enriched_rows


def _retention_rows_with_history(
    tracker_rows: list[dict],
    engagement_history: dict[str, list[dict[str, str | None]]],
) -> list[dict]:
    return [row for row in tracker_rows if engagement_history.get(_retention_customer_id(row))]


def _filter_excluded_retention_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if not _is_excluded_retention_customer(row.get("name"))]


def _retention_follow_up_reminders(
    tracker_rows: list[dict],
    engagement_history: dict[str, list[dict[str, str | None]]],
) -> list[dict[str, object]]:
    reminders: list[dict[str, object]] = []
    for row in tracker_rows:
        customer_id = _retention_customer_id(row)
        customer_engagements = engagement_history.get(customer_id) or []
        latest_engagement = customer_engagements[0] if customer_engagements else None
        if not latest_engagement:
            continue
        stage = _pipeline_stage_from_engagement(latest_engagement, row)
        if stage in {"Resolved", "Lost"}:
            continue
        follow_up = str(latest_engagement.get("followUp") or "").strip()
        days_until = _days_until_follow_up(follow_up)
        if days_until is None or days_until > 0:
            continue
        reminders.append(
            {
                "customer_id": customer_id,
                "customer_name": row.get("name") or "Unknown Customer",
                "phone": row.get("phone") or row.get("email") or "",
                "follow_up": follow_up,
                "days_overdue": abs(days_until),
                "due_label": "Due today" if days_until == 0 else f"{abs(days_until)} days overdue",
                "outcome": latest_engagement.get("outcome") or "",
                "rep": latest_engagement.get("rep") or "",
                "stage": stage,
            }
        )
    reminders.sort(
        key=lambda reminder: (
            -int(reminder["days_overdue"]) if isinstance(reminder["days_overdue"], int) else 0,
            str(reminder["customer_name"]).casefold(),
        )
    )
    return reminders


def _person_id_from_user(user: dict) -> str | None:
    return str(user.get("person_id") or user.get("id") or "").strip() or None


def _optional_uuid(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return coerce_uuid(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid person reference") from exc


def _resolve_subscriber_ids(db: Session, values: list[str]) -> list[str]:
    normalized_values = [str(value).strip() for value in values if str(value).strip()]
    if not normalized_values:
        return []

    resolved_ids: list[str] = []
    unresolved_values: list[str] = []
    for value in normalized_values:
        try:
            resolved_ids.append(str(coerce_uuid(value)))
        except ValueError:
            unresolved_values.append(value)

    if unresolved_values:
        rows = db.execute(
            select(Subscriber.external_id, Subscriber.id).where(Subscriber.external_id.in_(unresolved_values))
        ).all()
        id_by_external_id = {str(external_id or "").strip(): str(subscriber_id) for external_id, subscriber_id in rows}
        for value in unresolved_values:
            subscriber_id = id_by_external_id.get(value)
            if subscriber_id:
                resolved_ids.append(subscriber_id)

    unique_ids: list[str] = []
    seen: set[str] = set()
    for value in resolved_ids:
        if value in seen:
            continue
        seen.add(value)
        unique_ids.append(value)
    return unique_ids


def _retention_tracker_rows(churn_rows: list[dict], *, limit: int = 100) -> list[dict]:
    segment_priority = {
        "Suspended": 0,
        "Due Soon": 1,
        "Pending": 3,
        "Churned": 4,
    }

    def _int_value(value: object) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
        return 0

    def _stage_for(row: dict) -> str:
        segment = str(row.get("risk_segment") or "")
        days_past_due = _int_value(row.get("days_past_due"))
        if segment == "Churned":
            return "Win-back review"
        if segment == "Suspended" or days_past_due >= 30:
            return "Recovery priority"
        if segment in {"Due Soon", "Pending"}:
            return "Retention watch"
        return "Monitor"

    def _action_for(row: dict) -> str:
        segment = str(row.get("risk_segment") or "")
        days_past_due = _int_value(row.get("days_past_due"))
        balance = float(row.get("balance") or 0)
        if segment == "Churned":
            return "Confirm cancellation reason and queue a win-back offer."
        if segment == "Suspended":
            return "Call today, confirm payment path, and document restore conditions."
        if days_past_due >= 30 or balance >= 50000:
            return "Escalate to collections with a retention note before disconnection."
        if segment in {"Due Soon", "Pending"}:
            return "Confirm next invoice readiness and update customer contact status."
        return "Review account notes and keep in the retention watch list."

    tracker_rows = []
    for row in churn_rows:
        if _is_excluded_retention_customer(row.get("name")):
            continue
        candidate = dict(row)
        candidate["retention_stage"] = _stage_for(candidate)
        candidate["recommended_action"] = _action_for(candidate)
        candidate["days_past_due_display"] = _int_value(candidate.get("days_past_due"))
        tracker_rows.append(candidate)

    tracker_rows.sort(
        key=lambda row: (
            segment_priority.get(str(row.get("risk_segment") or ""), 99),
            -_int_value(row.get("days_past_due")),
            -float(row.get("balance") or 0),
            str(row.get("name") or "").casefold(),
        )
    )
    return tracker_rows[:limit]


def _retention_billing_rows_for_customer_ids(
    db: Session,
    *,
    customer_ids: list[str],
    due_soon_days: int,
    high_balance_only: bool = False,
    segment: str | None = None,
    selected_segments: list[str] | None = None,
    days_past_due: str | None = None,
    search: str | None = None,
    limit: int = 6000,
) -> list[dict]:
    normalized_customer_ids = sorted(
        {str(customer_id or "").strip() for customer_id in customer_ids if str(customer_id or "").strip()}
    )
    if not normalized_customer_ids:
        return []
    if settings.customer_retention_route_use_cache and _billing_risk_cache_available(db):
        return billing_risk_cache.cached_rows_by_external_ids(
            db,
            normalized_customer_ids,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=days_past_due,
            search=search,
            limit=limit,
        )
    rows = billing_risk_service.get_billing_risk_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=days_past_due,
        limit=limit,
        search=search,
        enrich_visible_rows=False,
    )
    return [row for row in rows if _retention_customer_id(row) in set(normalized_customer_ids)]


def _retention_date_text(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip()


def _retention_snapshot_row(row: SubscriberBillingRiskSnapshot) -> dict:
    source_metadata = row.source_metadata if isinstance(row.source_metadata, dict) else {}
    return {
        "subscriber_id": str(row.external_id or ""),
        "name": row.name,
        "email": row.email or "",
        "phone": row.phone or "",
        "city": row.city or "",
        "location": row.location or "",
        "mrr_total": float(row.mrr_total or 0),
        "subscriber_status": row.subscriber_status or "",
        "area": row.area or "",
        "plan": row.plan or "",
        "billing_start_date": _retention_date_text(row.billing_start_date),
        "billing_end_date": _retention_date_text(row.billing_end_date),
        "next_bill_date": _retention_date_text(row.next_bill_date),
        "balance": float(row.balance or 0),
        "account_balance_deposit": source_metadata.get("account_balance_deposit"),
        "billing_type": billing_risk_cache._display_billing_type(
            source_metadata.get("billing_mode"),
            source_metadata.get("subscription_billing_mode"),
            source_metadata.get("billing_type"),
        ),
        "billing_mode": str(source_metadata.get("billing_mode") or ""),
        "subscription_billing_mode": str(source_metadata.get("subscription_billing_mode") or ""),
        "billing_cycle": row.billing_cycle or "",
        "blocked_date": _retention_date_text(row.blocked_date),
        "blocked_for_days": row.blocked_for_days,
        "last_transaction_date": _retention_date_text(row.last_transaction_date),
        "expires_in": row.expires_in or "",
        "invoiced_until": _retention_date_text(row.invoiced_until),
        "days_since_last_payment": row.days_since_last_payment,
        "days_past_due": row.days_past_due,
        "total_paid": float(row.total_paid or 0),
        "days_to_due": row.days_to_due,
        "risk_segment": row.risk_segment,
        "is_high_balance_risk": bool(row.is_high_balance_risk),
        "_person_id": str(row.person_id) if row.person_id else "",
        "_external_id": str(row.external_id or ""),
        "_subscriber_number": row.subscriber_number or "",
        "_last_synced_at": row.refreshed_at.isoformat() if row.refreshed_at else "",
    }


def _retention_subscriber_row(subscriber: Subscriber, latest_engagement: dict[str, str | None] | None) -> dict:
    status_value = getattr(subscriber.status, "value", subscriber.status)
    status_text = str(status_value or "").strip()
    person = subscriber.person
    organization = subscriber.organization
    engagement_name = str((latest_engagement or {}).get("customerName") or "").strip()
    customer_name = subscriber.display_name or engagement_name or "Unknown Customer"
    phone = str(getattr(person, "phone", "") or "").strip() or str(getattr(organization, "phone", "") or "").strip()
    email = str(getattr(person, "email", "") or "").strip() or str(getattr(organization, "email", "") or "").strip()
    days_past_due = None
    if subscriber.suspended_at:
        days_past_due = max(0, (datetime.now(UTC).date() - subscriber.suspended_at.date()).days)
    return {
        "subscriber_id": str(subscriber.external_id or subscriber.subscriber_number or ""),
        "name": customer_name,
        "email": email,
        "phone": phone,
        "city": subscriber.service_city or "",
        "location": subscriber.service_region or subscriber.service_city or "",
        "mrr_total": 0,
        "subscriber_status": status_text.title(),
        "area": subscriber.service_region or "",
        "plan": subscriber.service_plan or subscriber.service_name or "",
        "billing_start_date": _retention_date_text(subscriber.activated_at),
        "billing_end_date": "",
        "next_bill_date": _retention_date_text(subscriber.next_bill_date),
        "balance": _coerce_money_value(subscriber.balance) or 0,
        "account_balance_deposit": None,
        "billing_type": "unknown",
        "billing_mode": "",
        "subscription_billing_mode": "",
        "billing_cycle": subscriber.billing_cycle or "",
        "blocked_date": _retention_date_text(subscriber.suspended_at),
        "blocked_for_days": days_past_due,
        "days_past_due": days_past_due,
        "days_to_due": None,
        "risk_segment": "Suspended" if status_text.lower() == "suspended" else "Retention",
        "is_high_balance_risk": False,
        "_person_id": str(subscriber.person_id) if subscriber.person_id else "",
        "_external_id": str(subscriber.external_id or ""),
        "_subscriber_number": subscriber.subscriber_number or "",
        "_last_synced_at": subscriber.last_synced_at.isoformat() if subscriber.last_synced_at else "",
    }


def _retention_history_only_row(customer_id: str, latest_engagement: dict[str, str | None] | None) -> dict:
    customer_name = str((latest_engagement or {}).get("customerName") or "").strip()
    return {
        "subscriber_id": customer_id,
        "name": customer_name or "Unknown Customer",
        "email": "",
        "phone": "",
        "city": "",
        "location": "",
        "mrr_total": 0,
        "subscriber_status": "",
        "area": "",
        "plan": "",
        "balance": 0,
        "blocked_for_days": None,
        "days_past_due": None,
        "days_to_due": None,
        "risk_segment": "Retention",
        "is_high_balance_risk": False,
        "_external_id": customer_id,
        "_subscriber_number": "",
        "_last_synced_at": "",
    }


def _retention_saved_only_rows(
    db: Session,
    customer_ids: list[str],
    engagement_history: dict[str, list[dict[str, str | None]]],
) -> list[dict]:
    normalized_ids = [str(customer_id or "").strip() for customer_id in customer_ids if str(customer_id or "").strip()]
    if not normalized_ids:
        return []
    snapshot_rows = db.scalars(
        select(SubscriberBillingRiskSnapshot).where(SubscriberBillingRiskSnapshot.external_id.in_(normalized_ids))
    ).all()
    rows_by_id = {str(row.external_id or "").strip(): _retention_snapshot_row(row) for row in snapshot_rows}
    missing_ids = [customer_id for customer_id in normalized_ids if customer_id not in rows_by_id]
    if missing_ids:
        subscriber_rows = db.scalars(
            select(Subscriber).where(
                Subscriber.external_system.in_(EXTERNAL_SUBSCRIBER_SYSTEMS),
                Subscriber.external_id.in_(missing_ids),
            )
        ).all()
        for subscriber in subscriber_rows:
            customer_id = str(subscriber.external_id or "").strip()
            customer_engagements = engagement_history.get(customer_id) or []
            latest_engagement = customer_engagements[0] if customer_engagements else None
            rows_by_id[customer_id] = _retention_subscriber_row(subscriber, latest_engagement)
    saved_rows: list[dict] = []
    for customer_id in normalized_ids:
        if customer_id in rows_by_id:
            saved_rows.append(rows_by_id[customer_id])
            continue
        customer_engagements = engagement_history.get(customer_id) or []
        latest_engagement = customer_engagements[0] if customer_engagements else None
        saved_rows.append(_retention_history_only_row(customer_id, latest_engagement))
    return saved_rows


def _retention_tracker_kpis(churn_rows: list[dict]) -> dict[str, int | float]:
    recovery_segments = {"Suspended", "Due Soon"}
    tracked_count = len(churn_rows)
    recovery_priority_count = sum(1 for row in churn_rows if str(row.get("risk_segment") or "") in recovery_segments)
    due_soon_count = sum(1 for row in churn_rows if str(row.get("risk_segment") or "") in {"Due Soon", "Pending"})
    recovered_count = sum(1 for row in churn_rows if str(row.get("pipeline_stage") or "") == "Resolved")
    lost_count = sum(1 for row in churn_rows if str(row.get("pipeline_stage") or "") == "Lost")
    winback_pool_count = int(recovered_count) + int(lost_count)
    winback_rate = round((int(recovered_count) / winback_pool_count) * 100, 1) if winback_pool_count else 0.0
    churn_rate = round((int(lost_count) / tracked_count) * 100, 1) if tracked_count else 0.0
    revenue_at_risk = round(sum(float(row.get("balance") or 0) for row in churn_rows), 2)
    high_balance_count = sum(1 for row in churn_rows if bool(row.get("is_high_balance_risk")))
    return {
        "tracked_count": tracked_count,
        "recovery_priority_count": recovery_priority_count,
        "due_soon_count": due_soon_count,
        "winback_rate": winback_rate,
        "recovered_count": recovered_count,
        "lost_count": lost_count,
        "churn_rate": churn_rate,
        "revenue_at_risk": revenue_at_risk,
        "high_balance_count": high_balance_count,
    }


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
    bucket: str | None = Query("all"),
    search: str | None = Query(None),
    enterprise_only: bool = Query(False),
    customer_segment: str | None = Query(None),
    location: str | None = Query(None),
    mrr_sort: str | None = Query(None),
    customer_status: str | None = Query("all"),
    billing_type: str | None = Query("all"),
):
    user = get_current_user(request)
    query_days_past_due = request.query_params.get("days_past_due")
    query_bucket = request.query_params.get("bucket")
    normalized_bucket = (
        query_bucket if query_bucket is not None else (bucket if isinstance(bucket, str) else "all")
    ).strip() or "all"
    query_search = request.query_params.get("search")
    normalized_search = query_search if query_search is not None else (search if isinstance(search, str) else None)
    query_location = request.query_params.get("location")
    normalized_location = (
        query_location if query_location is not None else (location if isinstance(location, str) else "")
    )
    normalized_location = normalized_location.strip()
    normalized_customer_segment = "all"
    normalized_enterprise_only = False
    normalized_location = (
        request.query_params.get("location") or (location if isinstance(location, str) else "")
    ).strip()
    query_mrr_sort = request.query_params.get("mrr_sort")
    normalized_mrr_sort = (
        (query_mrr_sort if query_mrr_sort is not None else (mrr_sort if isinstance(mrr_sort, str) else ""))
        .strip()
        .lower()
    )
    normalized_customer_status = _normalize_billing_risk_customer_status(
        request.query_params.get("customer_status") or customer_status
    )
    normalized_billing_type = _normalize_billing_type_filter(request.query_params.get("billing_type") or billing_type)
    selected_segments = _billing_risk_segments_for_customer_status(normalized_customer_status)
    selected_labels = _segment_labels(selected_segments)
    cache_eligible = (
        settings.billing_risk_route_use_cache
        and _billing_risk_cache_available(db)
        and not normalized_enterprise_only
        and normalized_customer_segment == "all"
    )
    if cache_eligible:
        full_metric_rows = _billing_risk_status_rows(
            billing_risk_cache.all_cached_rows(
                db,
                due_soon_days=due_soon_days,
                high_balance_only=high_balance_only,
                selected_segments=selected_segments,
                days_past_due=query_days_past_due or days_past_due,
                search=normalized_search,
                overdue_bucket=normalized_bucket,
                location=normalized_location,
                limit=10000,
            ),
            normalized_customer_status,
        )
        full_metric_rows = _billing_risk_billing_type_rows(full_metric_rows, normalized_billing_type)
        full_metric_rows = _active_toggle_uptime_rows(db, full_metric_rows, normalized_customer_status)
        page_rows = [dict(row) for row in full_metric_rows[:50]]
        _enrich_missing_plan_fields(db, page_rows)
        _enrich_unknown_billing_type_fields(db, page_rows)
        _enrich_missing_blocked_fields(page_rows, force_live=False)
        _enrich_account_balance_deposit(db, page_rows)
        _enrich_expiration_fields(page_rows)
        page_metrics = _billing_risk_page_metrics(page_rows)
        has_next = len(full_metric_rows) > 50
        end_read_only_transaction(db)
        billing_risk_route_state = {
            "mode": "cache",
            "metadata": billing_risk_cache.cache_metadata(db),
            "cached_metrics": False,
        }
    else:
        initial_rows, _initial_route_state = _billing_risk_rows_source(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            segment=segment,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            enterprise_only=normalized_enterprise_only,
            customer_segment=normalized_customer_segment,
            location=normalized_location,
            mrr_sort=normalized_mrr_sort,
            limit=51,
        )
        if selected_labels:
            initial_rows = [row for row in initial_rows if str(row.get("risk_segment") or "") in selected_labels]
        initial_rows = _billing_risk_status_rows(initial_rows, normalized_customer_status)
        initial_rows = _active_toggle_uptime_rows(db, initial_rows, normalized_customer_status)
        initial_rows = _billing_risk_billing_type_rows(initial_rows, normalized_billing_type)
        full_metric_rows, billing_risk_route_state = _billing_risk_rows_source(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            segment=segment,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            enterprise_only=normalized_enterprise_only,
            customer_segment=normalized_customer_segment,
            location=normalized_location,
            mrr_sort=normalized_mrr_sort,
            limit=10000,
        )
        end_read_only_transaction(db)
        if selected_labels:
            full_metric_rows = [
                row for row in full_metric_rows if str(row.get("risk_segment") or "") in selected_labels
            ]
        full_metric_rows = _billing_risk_status_rows(full_metric_rows, normalized_customer_status)
        full_metric_rows = _active_toggle_uptime_rows(db, full_metric_rows, normalized_customer_status)
        full_metric_rows = _billing_risk_billing_type_rows(full_metric_rows, normalized_billing_type)
        _enrich_missing_plan_fields(db, full_metric_rows)
        _enrich_unknown_billing_type_fields(db, full_metric_rows[:50])
        _enrich_account_balance_deposit(db, full_metric_rows)
        _enrich_expiration_fields(full_metric_rows)
        page_rows, page_metrics, has_next = _billing_risk_initial_rows(
            db,
            initial_rows,
            page_size=50,
            customer_status=normalized_customer_status,
            billing_type=normalized_billing_type,
        )
    overdue_invoices = billing_risk_service.get_overdue_invoices_table(
        db,
        min_days_past_due=overdue_invoice_days,
        limit=250,
    )
    billing_risk_cache_metadata = billing_risk_route_state.get("metadata") or {"row_count": len(full_metric_rows)}
    sidebar_stats = get_sidebar_stats(db)
    last_synced_at = _latest_subscriber_sync_at(db)
    rep_options = _retention_rep_options(db)
    outreach_targets = outreach_channel_target_options(db)
    live_location_options = _billing_risk_location_options(
        db,
        due_soon_days=due_soon_days,
        segment=segment,
        selected_location=normalized_location,
    )
    if billing_risk_route_state.get("cached_metrics"):
        overdue_invoice_balance = round(sum(float(row.get("total_balance_due") or 0) for row in overdue_invoices), 2)
        kpis = billing_risk_cache.summary_cached(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            location=normalized_location,
            overdue_invoice_balance=overdue_invoice_balance,
        )
        segment_breakdown = billing_risk_cache.segment_breakdown_cached(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            location=normalized_location,
        )
        aging_buckets = _billing_risk_unfiltered_blocked_buckets(
            db,
            due_soon_days=due_soon_days,
            segment=segment,
        )
    else:
        kpis = billing_risk_service.get_billing_risk_summary(full_metric_rows, overdue_invoices)
        segment_breakdown = billing_risk_service.get_billing_risk_segment_breakdown(full_metric_rows)
        aging_buckets = _billing_risk_unfiltered_blocked_buckets(
            db,
            due_soon_days=due_soon_days,
            segment=segment,
        )
    kpis = dict(kpis)
    normalized_days_past_due_filter = query_days_past_due or (days_past_due if isinstance(days_past_due, str) else "")
    at_risk_filters_active = any(
        [
            high_balance_only is True,
            bool(str(normalized_days_past_due_filter or "").strip()),
            normalized_bucket != "all",
            bool(str(normalized_search or "").strip()),
            bool(normalized_location),
        ]
    )
    if at_risk_filters_active:
        stable_kpis = _billing_risk_unfiltered_kpis(
            db,
            due_soon_days=due_soon_days,
            segment=segment,
            selected_segments=selected_segments,
            overdue_invoices=overdue_invoices,
        )
        kpis["total_at_risk"] = stable_kpis.get("total_at_risk", kpis.get("total_at_risk", 0))
        kpis["total_balance_exposure"] = stable_kpis.get(
            "total_balance_exposure",
            kpis.get("total_balance_exposure", 0),
        )
    else:
        kpis["total_at_risk"] = len(full_metric_rows)

    export_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
            "customer_status": normalized_customer_status,
            "billing_type": normalized_billing_type,
        },
        doseq=True,
    )
    retention_tracker_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
            "customer_status": normalized_customer_status,
            "billing_type": normalized_billing_type,
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
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
            "customer_status": normalized_customer_status,
            "billing_type": normalized_billing_type,
        },
        doseq=True,
    )
    segment_all_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
            "customer_status": normalized_customer_status,
            "billing_type": normalized_billing_type,
        },
        doseq=True,
    )
    segment_due_soon_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
            "segment": "overdue",
            "customer_status": "active",
            "billing_type": normalized_billing_type,
        },
        doseq=True,
    )
    segment_suspended_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
            "segment": "all",
            "customer_status": "all",
            "billing_type": normalized_billing_type,
        },
        doseq=True,
    )
    return templates.TemplateResponse(
        "admin/reports/subscriber_billing_risk.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": sidebar_stats,
            "active_page": "subscriber-billing-risk",
            "active_menu": "reports",
            "kpis": kpis,
            "segment_breakdown": segment_breakdown,
            "aging_buckets": aging_buckets,
            "churn_rows": page_rows,
            "overdue_invoices": overdue_invoices,
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": high_balance_only,
            "selected_segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "export_query": export_query,
            "retention_tracker_query": retention_tracker_query,
            "refresh_query": refresh_query,
            "segment_all_query": segment_all_query,
            "segment_due_soon_query": segment_due_soon_query,
            "segment_suspended_query": segment_suspended_query,
            "last_synced_at": last_synced_at,
            "billing_risk_cache": billing_risk_cache_metadata,
            "billing_risk_route_mode": billing_risk_route_state.get("mode", "live"),
            "csrf_token": get_csrf_token(request),
            "refresh_started": request.query_params.get("refresh_started") == "1",
            "refresh_error": request.query_params.get("refresh_error"),
            "live_page": 1,
            "live_page_size": 50,
            "live_has_next": has_next,
            "live_search": normalized_search or "",
            "live_location": normalized_location,
            "live_location_options": live_location_options,
            "live_bucket": normalized_bucket,
            "live_mrr_sort": normalized_mrr_sort,
            "customer_status": normalized_customer_status,
            "billing_type": normalized_billing_type,
            "page_metrics": page_metrics,
            "page": 1,
            "has_prev": False,
            "has_next": has_next,
            "rep_options": rep_options,
            "enterprise_mrr_threshold": ENTERPRISE_MRR_THRESHOLD,
            "outreach_channel_targets": outreach_targets,
        },
    )


@router.get("/subscribers/postpaid-customers", response_class=HTMLResponse)
def postpaid_customers_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = Query(None),
    location: str | None = Query(None),
    status: str | None = Query("all"),
    customer_status: str | None = Query(None),
    plan: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
):
    user = get_current_user(request)
    normalized_search = (request.query_params.get("search") or (search if isinstance(search, str) else "")).strip()
    normalized_location = (
        request.query_params.get("location") or (location if isinstance(location, str) else "")
    ).strip()
    normalized_status = (
        request.query_params.get("customer_status")
        or request.query_params.get("status")
        or (customer_status if isinstance(customer_status, str) else "")
        or (status if isinstance(status, str) else "all")
    ).strip()
    if normalized_status.lower() not in {"all", "active", "suspended"}:
        normalized_status = "all"
    normalized_plan = (request.query_params.get("plan") or (plan if isinstance(plan, str) else "")).strip()
    normalized_date_from = (
        request.query_params.get("date_from") or (date_from if isinstance(date_from, str) else "")
    ).strip()
    normalized_date_to = (request.query_params.get("date_to") or (date_to if isinstance(date_to, str) else "")).strip()

    rows = _postpaid_dashboard_rows(
        db,
        search=normalized_search,
        location=normalized_location,
        status=normalized_status,
        plan=normalized_plan,
        date_from=normalized_date_from,
        date_to=normalized_date_to,
        limit=10000,
    )
    all_customer_rows = billing_risk_cache.all_cached_rows(
        db,
        selected_segments=["active", "due_soon", "suspended"],
        search=normalized_search,
        location=normalized_location,
        limit=10000,
    )
    all_postpaid_rows = _postpaid_dashboard_rows(db, limit=10000)
    location_options = sorted(
        {
            str(row.get("location") or row.get("city") or "").strip()
            for row in all_postpaid_rows
            if str(row.get("location") or row.get("city") or "").strip()
        },
        key=str.casefold,
    )
    plan_options = sorted(
        {str(row.get("plan") or "").strip() for row in all_postpaid_rows if str(row.get("plan") or "").strip()},
        key=str.casefold,
    )
    customer_status_options = sorted(
        {
            str(row.get("subscriber_status") or "").strip()
            for row in all_postpaid_rows
            if str(row.get("subscriber_status") or "").strip()
        },
        key=str.casefold,
    )
    rows = sorted(rows, key=lambda row: (_money(row.get("revenue_owed")), _money(row.get("mrr_total"))), reverse=True)
    export_query = urlencode(
        {
            "search": normalized_search,
            "location": normalized_location,
            "customer_status": normalized_status,
            "plan": normalized_plan,
            "date_from": normalized_date_from,
            "date_to": normalized_date_to,
        }
    )
    table_rows = _postpaid_detail_table_rows(rows)[:250]
    all_prepaid_unpaid_rows = _prepaid_unpaid_balance_table_rows(
        all_customer_rows,
        status=normalized_status,
        plan=normalized_plan,
        date_from=normalized_date_from,
        date_to=normalized_date_to,
    )
    prepaid_unpaid_rows = all_prepaid_unpaid_rows[:250]
    latest_payments_by_customer: dict[str, dict] = {}
    _postpaid_enrich_detail_fields(db, table_rows, latest_payments_by_customer=latest_payments_by_customer)
    _postpaid_enrich_detail_fields(db, prepaid_unpaid_rows, latest_payments_by_customer=latest_payments_by_customer)
    _apply_prepaid_unpaid_invoice_summary(prepaid_unpaid_rows)
    kpis = _postpaid_dashboard_kpis(rows, all_customer_rows=all_customer_rows)
    kpis["prepaid_customers_with_unpaid_balances"] = len(all_prepaid_unpaid_rows)
    kpis["prepaid_unpaid_invoice_balance"] = round(
        sum(_money(row.get("detail_outstanding_balance")) for row in all_prepaid_unpaid_rows),
        2,
    )
    return templates.TemplateResponse(
        "admin/reports/postpaid_customers_dashboard.html",
        {
            "request": request,
            "current_user": user,
            "active_page": "postpaid-customers-dashboard",
            "active_menu": "reports",
            "sidebar_stats": get_sidebar_stats(db),
            "rows": table_rows,
            "total_rows": len(rows),
            "detail_total_rows": len(_postpaid_detail_table_rows(rows)),
            "prepaid_unpaid_rows": prepaid_unpaid_rows,
            "prepaid_unpaid_total_rows": len(all_prepaid_unpaid_rows),
            "kpis": kpis,
            "top_balance_customers": _postpaid_top_customer_balance_chart(rows),
            "customer_status_chart": _postpaid_customer_status_chart(rows),
            "payment_recency_chart": _postpaid_payment_recency_chart(rows),
            "payment_amount_trend": _postpaid_payment_amount_trend(rows),
            "invoice_segment_chart": _postpaid_invoice_segment_chart(rows),
            "invoice_aging_chart": _postpaid_invoice_aging_chart(rows),
            "location_options": location_options,
            "plan_options": plan_options,
            "customer_status_options": customer_status_options,
            "search": normalized_search,
            "selected_location": normalized_location,
            "selected_status": normalized_status.lower(),
            "selected_plan": normalized_plan,
            "date_from": normalized_date_from,
            "date_to": normalized_date_to,
            "export_query": export_query,
            "cache_metadata": billing_risk_cache.cache_metadata(db),
        },
    )


@router.get("/subscribers/postpaid-customers/export")
def postpaid_customers_dashboard_export(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = Query(None),
    location: str | None = Query(None),
    status: str | None = Query("all"),
    customer_status: str | None = Query(None),
    plan: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
):
    get_current_user(request)
    rows = _postpaid_dashboard_rows(
        db,
        search=(request.query_params.get("search") or (search if isinstance(search, str) else "")).strip(),
        location=(request.query_params.get("location") or (location if isinstance(location, str) else "")).strip(),
        status=(
            request.query_params.get("customer_status")
            or request.query_params.get("status")
            or (customer_status if isinstance(customer_status, str) else "")
            or (status if isinstance(status, str) else "all")
        ).strip(),
        plan=(request.query_params.get("plan") or (plan if isinstance(plan, str) else "")).strip(),
        date_from=(request.query_params.get("date_from") or (date_from if isinstance(date_from, str) else "")).strip(),
        date_to=(request.query_params.get("date_to") or (date_to if isinstance(date_to, str) else "")).strip(),
        limit=10000,
    )
    export_rows = [
        {
            "Name": row.get("name") or "",
            "Phone": row.get("phone") or "",
            "Email": row.get("email") or "",
            "Location": row.get("location") or row.get("city") or "",
            "Status": row.get("subscriber_status") or "",
            "Plan": row.get("plan") or "",
            "MRR": _money(row.get("mrr_total")),
            "Balance": _money(row.get("balance")),
            "Revenue Owed": _money(row.get("revenue_owed")),
            "Service Expiration Date": row.get("service_expiration_date") or "",
            "Days Past Due": row.get("days_past_due") or 0,
            "External ID": row.get("_external_id") or row.get("subscriber_id") or "",
        }
        for row in sorted(rows, key=lambda item: _money(item.get("revenue_owed")), reverse=True)
    ]
    return _csv_response(export_rows, f"postpaid_customers_{datetime.now(UTC).strftime('%Y%m%d')}.csv")


@customer_retention_router.get("/customer-retention", response_class=HTMLResponse)
def customer_retention_tracker(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
    search: str | None = Query(None),
):
    user = get_current_user(request)
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )
    search_text = (search.strip() if isinstance(search, str) else "") or None
    saved_customer_ids: list[str] = []
    if hasattr(db, "execute"):
        saved_customer_ids = _retention_active_customer_ids(db)
        if search_text:
            saved_customer_ids = list(
                dict.fromkeys(saved_customer_ids + _retention_search_customer_ids(db, search_text))
            )
    if saved_customer_ids:
        engagement_history = _retention_engagements_by_customer(db, saved_customer_ids)
        churn_rows = _retention_billing_rows_for_customer_ids(
            db,
            customer_ids=saved_customer_ids,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            segment=segment,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=None,
            limit=6000,
        )
        tracker_rows = _retention_tracker_rows(churn_rows, limit=6000)
        rendered_customer_ids = {_retention_customer_id(row) for row in tracker_rows}
        missing_customer_ids = [
            customer_id for customer_id in saved_customer_ids if customer_id not in rendered_customer_ids
        ]
        if missing_customer_ids:
            tracker_rows.extend(_retention_saved_only_rows(db, missing_customer_ids, engagement_history))
    else:
        tracker_rows = []
        engagement_history = {}
    tracker_rows = _retention_rows_with_history(tracker_rows, engagement_history)
    raw_search = request.query_params.get("search")
    search_term = (
        raw_search.strip() if isinstance(raw_search, str) else (search.strip() if isinstance(search, str) else "")
    )
    if search_term:
        matched_customer_ids = _retention_search_customer_ids(db, search_term)
        engagement_history.update(_retention_engagements_by_customer(db, matched_customer_ids))
        search_casefold = search_term.casefold()
        filtered_rows: list[dict] = []
        for row in tracker_rows:
            customer_id = _retention_customer_id(row)
            latest_engagements = engagement_history.get(customer_id) or []
            haystacks = [
                str(row.get("name") or ""),
                str(row.get("phone") or ""),
                str(row.get("email") or ""),
                customer_id,
            ]
            if latest_engagements:
                latest = latest_engagements[0]
                haystacks.extend(
                    [
                        str(latest.get("outcome") or ""),
                        str(latest.get("note") or ""),
                        str(latest.get("rep") or ""),
                    ]
                )
            if any(search_casefold in value.casefold() for value in haystacks if value):
                filtered_rows.append(row)
        tracker_rows = filtered_rows
    tracker_rows = _retention_rows_with_pipeline(tracker_rows, engagement_history)
    tracker_rows = _filter_excluded_retention_rows(tracker_rows)
    follow_up_reminders = _retention_follow_up_reminders(tracker_rows, engagement_history)
    segment_breakdown = billing_risk_service.get_billing_risk_segment_breakdown(tracker_rows)
    filter_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "search": search_term,
        },
        doseq=True,
    )
    clear_search_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
        },
        doseq=True,
    )

    return templates.TemplateResponse(
        "admin/reports/customer_retention_tracker.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "customer-retention",
            "active_menu": "reports",
            "kpis": _retention_tracker_kpis(tracker_rows),
            "rep_options": _retention_rep_options(db),
            "tracker_rows": tracker_rows,
            "engagement_history": engagement_history,
            "follow_up_reminders": follow_up_reminders,
            "pipeline_steps": RETENTION_PIPELINE_STEPS,
            "segment_breakdown": segment_breakdown,
            "due_soon_days": due_soon_days,
            "high_balance_only": high_balance_only,
            "selected_segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "search": search_term,
            "filter_query": filter_query,
            "clear_search_query": clear_search_query,
            "last_synced_at": _latest_subscriber_sync_at(db),
            "outreach_channel_targets": outreach_channel_target_options(db),
            "outreach_error": request.query_params.get("outreach_error"),
            "customer_retention_route_mode": "cache"
            if settings.customer_retention_route_use_cache and _billing_risk_cache_available(db)
            else "live",
        },
    )


@customer_retention_router.get("/customer-retention/engagements")
def customer_retention_engagements(
    request: Request,
    db: Session = Depends(get_db),
    customer_id: list[str] = Query(default=[]),
):
    get_current_user(request)
    return JSONResponse({"engagements": _retention_engagements_by_customer(db, customer_id)})


@customer_retention_router.post("/customer-retention/engagements")
async def customer_retention_engagement_create(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    payload = await request.json()
    customer_id = str(payload.get("customerId") or "").strip()
    outcome = str(payload.get("outcome") or "").strip()
    if not customer_id or not outcome:
        raise HTTPException(status_code=400, detail="Customer and outcome are required")

    engagement = create_retention_engagement_and_sync(
        db,
        customer_id=customer_id,
        customer_name=str(payload.get("customerName") or "").strip() or None,
        outcome=outcome,
        note=str(payload.get("note") or "").strip() or None,
        follow_up=payload.get("followUp"),
        rep_person_id=str(payload.get("repPersonId") or "").strip(),
        rep=str(payload.get("rep") or "").strip() or None,
        created_by_person_id=_person_id_from_user(user),
    )
    return JSONResponse({"engagement": _retention_engagement_payload(engagement)})


@customer_retention_router.post("/customer-retention/{customer_id}/engagements")
def customer_retention_profile_engagement_create(
    customer_id: str,
    request: Request,
    db: Session = Depends(get_db),
    customer_name: str | None = Form(default=None),
    outcome: str = Form(...),
    note: str | None = Form(default=None),
    follow_up: str | None = Form(default=None),
    rep_person_id: str | None = Form(default=None),
    rep: str | None = Form(default=None),
    due_soon_days: int = Form(7),
):
    user = get_current_user(request)
    normalized_customer_id = str(customer_id or "").strip()
    normalized_outcome = str(outcome or "").strip()
    if not normalized_customer_id or not normalized_outcome:
        raise HTTPException(status_code=400, detail="Customer and outcome are required")

    create_retention_engagement_and_sync(
        db,
        customer_id=normalized_customer_id,
        customer_name=str(customer_name or "").strip() or None,
        outcome=normalized_outcome,
        note=str(note or "").strip() or None,
        follow_up=follow_up,
        rep_person_id=rep_person_id,
        rep=rep,
        created_by_person_id=_person_id_from_user(user),
    )

    return RedirectResponse(
        url=f"/admin/customer-retention/{customer_id}?due_soon_days={due_soon_days}&saved=1",
        status_code=303,
    )


@customer_retention_router.get("/customer-retention/{customer_id}", response_class=HTMLResponse)
def customer_retention_tracker_detail(
    customer_id: str,
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
):
    user = get_current_user(request)
    normalized_customer_id = str(customer_id or "").strip()
    if settings.customer_retention_route_use_cache and _billing_risk_cache_available(db):
        customer = billing_risk_cache.cached_row_by_external_id(
            db,
            normalized_customer_id,
            due_soon_days=due_soon_days,
        )
    else:
        customer_rows = _retention_billing_rows_for_customer_ids(
            db,
            customer_ids=[normalized_customer_id],
            due_soon_days=due_soon_days,
            limit=1,
        )
        customer = customer_rows[0] if customer_rows else None
    if customer is not None:
        visible_customer = dict(customer)
        billing_risk_service.enrich_billing_risk_rows([visible_customer])
    else:
        visible_customer = {
            "name": "Unknown customer",
            "_external_id": normalized_customer_id,
            "plan": "",
            "mrr_total": 0,
            "balance": 0,
            "risk_segment": "",
            "subscriber_status": "",
            "blocked_for_days": None,
        }

    engagement_history = _retention_engagements_by_customer(db, [normalized_customer_id]).get(
        normalized_customer_id, []
    )
    latest_engagement = engagement_history[0] if engagement_history else None
    pipeline_stage = _pipeline_stage_from_engagement(latest_engagement)
    follow_up_due_label = ""
    if latest_engagement:
        days_until_follow_up = _days_until_follow_up(str(latest_engagement.get("followUp") or ""))
        if days_until_follow_up is not None:
            if days_until_follow_up < 0:
                follow_up_due_label = f"{abs(days_until_follow_up)} days overdue"
            elif days_until_follow_up == 0:
                follow_up_due_label = "Due today"
            else:
                follow_up_due_label = f"Due in {days_until_follow_up} days"

    return templates.TemplateResponse(
        "admin/reports/customer_retention_profile.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "customer-retention",
            "active_menu": "reports",
            "customer": visible_customer,
            "customer_id": normalized_customer_id,
            "engagement_history": engagement_history,
            "pipeline_steps": RETENTION_PIPELINE_STEPS,
            "pipeline_stage": pipeline_stage,
            "follow_up_due_label": follow_up_due_label,
            "rep_options": _retention_rep_options(db),
            "saved": request.query_params.get("saved"),
            "back_url": "/admin/reports/subscribers/billing-risk",
            "customer_retention_route_mode": "cache"
            if settings.customer_retention_route_use_cache and _billing_risk_cache_available(db)
            else "live",
        },
    )


@customer_retention_router.post("/customer-retention/outreach")
def customer_retention_create_outreach(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form("Retention Outreach"),
    channel: str = Form("whatsapp"),
    channel_target_id: str = Form(""),
    subscriber_id: list[str] = Form(default=[]),
    retention_customer_id: list[str] = Form(default=[]),
    due_soon_days: int = Form(7),
    high_balance_only: bool = Form(False),
    segments: list[str] = Form(default=[]),
    days_past_due: str = Form(""),
    next_url: str = Form("/admin/customer-retention"),
):
    user = get_current_user(request)
    if not next_url.startswith("/admin/customer-retention"):
        next_url = "/admin/customer-retention"

    selected_customer_ids = [str(value).strip() for value in retention_customer_id if str(value).strip()]
    if not selected_customer_ids:
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", "no_selection"),
            status_code=303,
        )

    selected_segments = _normalize_segment_filters(segments, None)
    churn_rows = _retention_billing_rows_for_customer_ids(
        db,
        customer_ids=selected_customer_ids,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=days_past_due or None,
        limit=len(selected_customer_ids),
    )
    tracker_rows = _retention_tracker_rows(churn_rows, limit=6000)
    engagement_history = _retention_engagements_by_customer(db, [_retention_customer_id(row) for row in tracker_rows])
    tracker_rows = _retention_rows_with_history(tracker_rows, engagement_history)
    tracker_rows = _retention_rows_with_pipeline(tracker_rows, engagement_history)
    tracker_rows = _filter_excluded_retention_rows(tracker_rows)
    row_by_customer_id = {_retention_customer_id(row): row for row in tracker_rows}

    selected_subscriber_ids: list[str] = []
    filtered_retention_customer_ids: list[str] = []
    for customer_id in selected_customer_ids:
        row = row_by_customer_id.get(customer_id)
        if not row:
            continue
        subscriber_value = str(row.get("subscriber_id") or "").strip()
        if not subscriber_value:
            continue
        selected_subscriber_ids.append(subscriber_value)
        filtered_retention_customer_ids.append(customer_id)

    if not selected_subscriber_ids:
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", "No valid subscribers in selection"),
            status_code=303,
        )

    try:
        campaign = create_billing_risk_outreach_campaign(
            db,
            name=name,
            channel=channel,
            channel_target_id=channel_target_id,
            subscriber_ids=selected_subscriber_ids,
            retention_customer_ids=filtered_retention_customer_ids,
            created_by_id=_person_id_from_user(user),
            source_filters={
                "retention_queue": True,
                "selected_count": len(selected_subscriber_ids),
                "due_soon_days": due_soon_days,
                "high_balance_only": bool(high_balance_only),
                "days_past_due": days_past_due or None,
                "query": str(request.url),
            },
        )
    except HTTPException as exc:
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", str(exc.detail)),
            status_code=303,
        )
    except Exception:
        logger.exception("Failed to create billing risk outreach campaign")
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", "Unable to create outreach draft"),
            status_code=303,
        )

    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign.id}", status_code=303)


@router.post("/subscribers/billing-risk/refresh")
def subscriber_billing_risk_refresh(
    request: Request,
    next_url: str = Form("/admin/reports/subscribers/billing-risk"),
    _admin: dict = Depends(require_web_role("admin")),
):
    if not next_url.startswith("/admin/reports/subscribers/billing-risk"):
        next_url = "/admin/reports/subscribers/billing-risk"

    try:
        sync_subscribers_from_selfcare.delay()
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_started", "1"), status_code=303)
    except Exception:
        logger.exception("Failed to enqueue Selfcare subscriber sync")
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_error", "queue_unavailable"), status_code=303)


@router.post("/subscribers/billing-risk/outreach")
def subscriber_billing_risk_create_outreach(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form("Billing Risk Outreach"),
    channel: str = Form("whatsapp"),
    channel_target_id: str = Form(""),
    subscriber_id: list[str] = Form(default=[]),
    retention_customer_id: list[str] = Form(default=[]),
    next_url: str = Form("/admin/reports/subscribers/billing-risk"),
):
    user = get_current_user(request)
    if not next_url.startswith("/admin/reports/subscribers/billing-risk") or next_url.startswith(
        "/admin/reports/subscribers/billing-risk/rows"
    ):
        next_url = "/admin/reports/subscribers/billing-risk"

    selected_subscriber_ids = _resolve_subscriber_ids(
        db, [str(value).strip() for value in subscriber_id if str(value).strip()]
    )
    if not selected_subscriber_ids:
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", "no_selection"),
            status_code=303,
        )

    try:
        campaign = create_billing_risk_outreach_campaign(
            db,
            name=name,
            channel=channel,
            channel_target_id=channel_target_id,
            subscriber_ids=selected_subscriber_ids,
            retention_customer_ids=retention_customer_id,
            created_by_id=_person_id_from_user(user),
            source_filters={
                "query": request.headers.get("referer", ""),
                "selected_count": len(selected_subscriber_ids),
            },
        )
    except HTTPException as exc:
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", str(exc.detail)),
            status_code=303,
        )

    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign.id}", status_code=303)


@router.get("/subscribers/billing-risk/blocked-dates")
def subscriber_billing_risk_blocked_dates(
    request: Request,
    external_id: list[str] = Query(default=[]),
    blocked_like_external_id: list[str] = Query(default=[]),
):
    get_current_user(request)
    blocked_dates = _safe_live_blocked_dates(
        external_id,
        force_live=False,
        blocking_only_external_ids=blocked_like_external_id,
    )
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
    enterprise_only: bool = Query(False),
    customer_segment: str | None = Query(None),
    location: str | None = Query(None),
    mrr_sort: str | None = Query(None),
    customer_status: str | None = Query("all"),
    billing_type: str | None = Query("all"),
):
    get_current_user(request)
    query_days_past_due = request.query_params.get("days_past_due")
    query_search = request.query_params.get("search")
    normalized_search = query_search if query_search is not None else (search if isinstance(search, str) else None)
    query_location = request.query_params.get("location")
    normalized_location = (
        query_location if query_location is not None else (location if isinstance(location, str) else "")
    )
    normalized_location = normalized_location.strip()
    query_bucket = request.query_params.get("bucket")
    normalized_bucket = (
        query_bucket if query_bucket is not None else (bucket if isinstance(bucket, str) else "all")
    ).strip() or "all"
    normalized_customer_segment = "all"
    normalized_enterprise_only = False
    normalized_location = (
        request.query_params.get("location") or (location if isinstance(location, str) else "")
    ).strip()
    query_mrr_sort = request.query_params.get("mrr_sort")
    normalized_mrr_sort = (
        (query_mrr_sort if query_mrr_sort is not None else (mrr_sort if isinstance(mrr_sort, str) else ""))
        .strip()
        .lower()
    )
    normalized_customer_status = _normalize_billing_risk_customer_status(
        request.query_params.get("customer_status") or customer_status
    )
    normalized_billing_type = _normalize_billing_type_filter(request.query_params.get("billing_type") or billing_type)
    selected_segments = _billing_risk_segments_for_customer_status(normalized_customer_status)
    if (
        settings.billing_risk_route_use_cache
        and _billing_risk_cache_available(db)
        and not normalized_enterprise_only
        and normalized_customer_segment == "all"
    ):
        page_rows, page_metrics, has_next = _billing_risk_cached_page_rows(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            page=page,
            page_size=page_size,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            location=normalized_location,
            customer_status=normalized_customer_status,
            billing_type=normalized_billing_type,
        )
    else:
        page_rows, page_metrics, has_next = _billing_risk_page_rows(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            segment=segment,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            page=page,
            page_size=page_size,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            enterprise_only=normalized_enterprise_only,
            customer_segment=normalized_customer_segment,
            location=normalized_location,
            mrr_sort=normalized_mrr_sort,
            customer_status=normalized_customer_status,
            billing_type=normalized_billing_type,
        )
    kpis = _billing_risk_unfiltered_kpis(
        db,
        due_soon_days=due_soon_days,
        segment=segment,
        selected_segments=selected_segments,
    )
    return templates.TemplateResponse(
        "admin/reports/_subscriber_billing_risk_results.html",
        {
            "request": request,
            "churn_rows": page_rows,
            "page_metrics": page_metrics,
            "kpis": kpis,
            "aging_buckets": _billing_risk_unfiltered_blocked_buckets(
                db,
                due_soon_days=due_soon_days,
                segment=segment,
            ),
            "page": page,
            "page_size": page_size,
            "has_prev": page > 1,
            "has_next": has_next,
            "enterprise_mrr_threshold": ENTERPRISE_MRR_THRESHOLD,
            "outreach_channel_targets": outreach_channel_target_options(db),
            "csrf_token": get_csrf_token(request),
            "customer_status": normalized_customer_status,
            "billing_type": normalized_billing_type,
        },
    )


@router.get("/subscribers/billing-risk/blocked-date-cell", response_class=HTMLResponse)
def subscriber_billing_risk_blocked_date_cell(
    request: Request,
    external_id: str = Query(...),
):
    get_current_user(request)
    blocked_dates = _safe_live_blocked_dates([external_id], force_live=False)
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
    search: str | None = Query(None),
    bucket: str | None = Query("all"),
    enterprise_only: bool = Query(False),
    customer_segment: str | None = Query(None),
    location: str | None = Query(None),
    mrr_sort: str | None = Query(None),
    customer_status: str | None = Query("all"),
    billing_type: str | None = Query("all"),
):
    query_days_past_due = request.query_params.get("days_past_due")
    query_search = request.query_params.get("search")
    normalized_search = query_search if query_search is not None else (search if isinstance(search, str) else None)
    query_location = request.query_params.get("location")
    normalized_location = (
        query_location if query_location is not None else (location if isinstance(location, str) else "")
    )
    normalized_location = normalized_location.strip()
    query_bucket = request.query_params.get("bucket")
    normalized_bucket = (
        query_bucket if query_bucket is not None else (bucket if isinstance(bucket, str) else "all")
    ).strip() or "all"
    normalized_customer_segment = "all"
    normalized_enterprise_only = False
    query_mrr_sort = request.query_params.get("mrr_sort")
    normalized_mrr_sort = (
        (query_mrr_sort if query_mrr_sort is not None else (mrr_sort if isinstance(mrr_sort, str) else ""))
        .strip()
        .lower()
    )
    normalized_customer_status = _normalize_billing_risk_customer_status(
        request.query_params.get("customer_status") or customer_status
    )
    normalized_billing_type = _normalize_billing_type_filter(request.query_params.get("billing_type") or billing_type)
    selected_segments = _billing_risk_segments_for_customer_status(normalized_customer_status)

    churn_rows, _route_state = _billing_risk_rows_source(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        selected_segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        search=normalized_search,
        overdue_bucket=normalized_bucket,
        enterprise_only=normalized_enterprise_only,
        customer_segment=normalized_customer_segment,
        location=normalized_location,
        mrr_sort=normalized_mrr_sort,
        limit=6000,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    churn_rows = _billing_risk_status_rows(churn_rows, normalized_customer_status)
    churn_rows = _active_toggle_uptime_rows(db, churn_rows, normalized_customer_status)
    churn_rows = _billing_risk_billing_type_rows(churn_rows, normalized_billing_type)
    _enrich_missing_plan_fields(db, churn_rows)
    _enrich_unknown_billing_type_fields(db, churn_rows)
    _enrich_missing_blocked_fields(churn_rows, force_live=False)
    _enrich_account_balance_deposit(db, churn_rows)
    _enrich_expiration_fields(churn_rows)
    export_data = _billing_risk_visible_export_rows(db, churn_rows)
    filename = f"subscriber_billing_risk_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)
