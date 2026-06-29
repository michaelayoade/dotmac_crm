"""Cached billing-risk report storage and query helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.models.subscriber import Subscriber, SubscriberBillingRiskSnapshot
from app.services import billing_risk_reports as live_billing_risk
from app.services import selfcare

SEGMENT_LABELS = {
    "active": "Active",
    "overdue": "Due Soon",
    "due_soon": "Due Soon",
    "suspended": "Suspended",
    "churned": "Churned",
    "pending": "Pending",
}


@dataclass(frozen=True)
class BillingRiskPage:
    rows: list[dict[str, Any]]
    page_metrics: dict[str, int | float]
    has_next: bool
    total_count: int = 0
    total_pages: int = 1
    page: int = 1


def _parse_date(value: object) -> date | None:
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


def _parse_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _parse_decimal(value: object) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0")


def _date_text(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


_GENERIC_LOCATION_VALUES = {"ng", "nga", "nigeria", "unknown", "none", "null", "n/a", "na", "-"}
_ADDRESS_LOCATION_KEYWORDS = (
    " avenue",
    " building",
    " center",
    " centre",
    " close",
    " court",
    " crescent",
    " drive",
    " estate",
    " expressway",
    " floor",
    " hotel",
    " layout",
    " road",
    " street",
    " terrace",
    " unit",
)


def _looks_like_address_or_identifier(text: str) -> bool:
    compact = text.replace(" ", "")
    if compact.isdigit():
        return True
    first_token = text.split(maxsplit=1)[0].strip(".,#")
    if first_token and first_token[0].isdigit():
        return True
    if len(first_token) <= 5 and any(character.isdigit() for character in first_token):
        return True
    lowered = f" {text.casefold()} "
    return any(keyword in lowered for keyword in _ADDRESS_LOCATION_KEYWORDS)


def _usable_location_text(value: object) -> str:
    text = str(value or "").strip().strip(",")
    if not text:
        return ""
    for suffix in (", NG", ", NGA", ", Nigeria"):
        if text.casefold().endswith(suffix.casefold()):
            text = text[: -len(suffix)].strip().strip(",")
            break
    if text.casefold().replace(".", "").strip() in _GENERIC_LOCATION_VALUES:
        return ""
    if _looks_like_address_or_identifier(text):
        return ""
    return text


def _display_location(*values: object) -> str:
    for value in values:
        if text := _usable_location_text(value):
            return text
    return ""


def _display_billing_type(
    billing_mode: object = None,
    subscription_billing_mode: object = None,
    billing_type: object = None,
) -> str:
    for value in (subscription_billing_mode, billing_mode):
        normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized.startswith("prepaid"):
            return "prepaid"
        if normalized.startswith("postpaid") or normalized.startswith("recurring"):
            return "postpaid"
    normalized_type = str(billing_type or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized_type.startswith("prepaid"):
        return "prepaid"
    if normalized_type.startswith("postpaid") or normalized_type.startswith("recurring"):
        return "postpaid"
    return "unknown"


def _normalized_customer_status(value: str | None) -> str:
    normalized = str(value or "all").strip().lower()
    if normalized in {"active", "suspended", "all"}:
        return normalized
    return "all"


def _normalized_billing_type(value: str | None) -> str:
    normalized = str(value or "all").strip().lower()
    if normalized in {"prepaid", "postpaid", "all"}:
        return normalized
    return "all"


def _billing_type_expr():
    source_type = func.lower(
        func.coalesce(
            SubscriberBillingRiskSnapshot.source_metadata["subscription_billing_mode"].as_string(),
            SubscriberBillingRiskSnapshot.source_metadata["billing_mode"].as_string(),
            SubscriberBillingRiskSnapshot.source_metadata["billing_type"].as_string(),
            "",
        )
    )
    billing_cycle = func.lower(func.coalesce(SubscriberBillingRiskSnapshot.billing_cycle, ""))
    return case(
        (or_(source_type.like("prepaid%"), billing_cycle.like("prepaid%")), "prepaid"),
        (
            or_(
                source_type.like("postpaid%"),
                source_type.like("recurring%"),
                billing_cycle.like("postpaid%"),
                billing_cycle.like("recurring%"),
            ),
            "postpaid",
        ),
        else_="unknown",
    )


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text != "0000-00-00":
            return text
    return ""


def _latest_payment_by_customer(payments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
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
                "amount": str(
                    _parse_decimal(payment.get("amount") or payment.get("paid_amount") or payment.get("total"))
                ),
            }
    return latest


def _billing_invoice_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in ("invoices", "active_invoices", "unpaid_invoices", "open_invoices"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    for key in ("billing", "account", "customer"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            rows.extend(_billing_invoice_rows(nested))
    return rows


def _invoice_balance_due(invoice: dict[str, Any]) -> Decimal:
    return _parse_decimal(
        invoice.get("balance_due")
        or invoice.get("balanceDue")
        or invoice.get("due_balance")
        or invoice.get("amount_due")
        or invoice.get("outstanding_balance")
        or invoice.get("balance")
    )


def _is_active_unpaid_invoice(invoice: dict[str, Any]) -> bool:
    if _invoice_balance_due(invoice) <= 0:
        return False
    if invoice.get("is_active") is False or invoice.get("active") is False:
        return False
    status = str(invoice.get("status") or invoice.get("invoice_status") or "").strip().casefold()
    payment_status = str(invoice.get("payment_status") or invoice.get("paid_status") or "").strip().casefold()
    excluded = {"paid", "cancelled", "canceled", "void", "voided", "deleted", "draft", "refunded"}
    return not (status in excluded or payment_status in excluded)


def _active_unpaid_invoice_summary(billing_payload: dict[str, Any] | None) -> dict[str, Any]:
    invoices = [invoice for invoice in _billing_invoice_rows(billing_payload) if _is_active_unpaid_invoice(invoice)]
    balance_due = sum((_invoice_balance_due(invoice) for invoice in invoices), Decimal("0.00")).quantize(
        Decimal("0.01")
    )
    return {
        "count": len(invoices),
        "balance_due": str(balance_due),
        "last_invoice_date": _first_text(
            *[
                _first_text(
                    invoice.get("invoice_date"),
                    invoice.get("date"),
                    invoice.get("created_at"),
                    invoice.get("issued_at"),
                )
                for invoice in invoices
            ]
        ),
        "next_due_date": _first_text(
            *[
                _first_text(
                    invoice.get("due_date"),
                    invoice.get("dueDate"),
                    invoice.get("payment_due_date"),
                )
                for invoice in invoices
            ]
        ),
    }


def _enrich_cached_payment_and_invoice_fields(db: Session, rows: list[dict[str, Any]]) -> None:
    try:
        latest_payments = _latest_payment_by_customer(selfcare.fetch_payments(db, limit=10000))
    except Exception:
        latest_payments = {}
    for row in rows:
        external_id = str(row.get("_external_id") or row.get("subscriber_id") or "").strip()
        latest_payment = latest_payments.get(external_id) or {}
        row["last_payment_date"] = _first_text(
            latest_payment.get("date"),
            row.get("last_payment_date"),
            row.get("last_transaction_date"),
        )
        row["last_payment_amount"] = _first_text(latest_payment.get("amount"), row.get("last_payment_amount"))
        billing_type = _display_billing_type(
            row.get("billing_mode"),
            row.get("subscription_billing_mode"),
            row.get("billing_type"),
        )
        if billing_type != "prepaid" or not external_id:
            continue
        try:
            billing_payload = selfcare.fetch_customer_billing(db, external_id)
        except Exception:
            billing_payload = None
        row["prepaid_unpaid_invoice_summary"] = _active_unpaid_invoice_summary(billing_payload)


def _snapshot_to_dict(row: SubscriberBillingRiskSnapshot) -> dict[str, Any]:
    balance = float(row.balance or 0)
    mrr_total = float(row.mrr_total or 0)
    total_paid = float(row.total_paid or 0)
    source_metadata = row.source_metadata if isinstance(row.source_metadata, dict) else {}
    return {
        "subscriber_id": row.external_id,
        "name": row.name,
        "email": row.email or "",
        "phone": row.phone or "",
        "city": row.city or "",
        "location": _display_location(row.location, row.city, row.area),
        "mrr_total": mrr_total,
        "subscriber_status": row.subscriber_status or "",
        "area": row.area or "",
        "plan": row.plan or "",
        "billing_start_date": _date_text(row.billing_start_date),
        "billing_end_date": _date_text(row.billing_end_date),
        "next_bill_date": _date_text(row.next_bill_date),
        "balance": balance,
        "account_balance_deposit": source_metadata.get("account_balance_deposit"),
        "billing_type": _display_billing_type(
            source_metadata.get("billing_mode"),
            source_metadata.get("subscription_billing_mode"),
            source_metadata.get("billing_type"),
        ),
        "billing_mode": str(source_metadata.get("billing_mode") or ""),
        "subscription_billing_mode": str(source_metadata.get("subscription_billing_mode") or ""),
        "account_billing_mode": str(source_metadata.get("account_billing_mode") or ""),
        "billing_cycle": row.billing_cycle or "",
        "blocked_date": _date_text(row.blocked_date),
        "blocked_for_days": row.blocked_for_days,
        "last_transaction_date": _date_text(row.last_transaction_date),
        "last_payment_date": str(source_metadata.get("last_payment_date") or ""),
        "last_payment_amount": source_metadata.get("last_payment_amount") or 0,
        "expires_in": row.expires_in or "",
        "invoiced_until": _date_text(row.invoiced_until),
        "days_since_last_payment": row.days_since_last_payment,
        "days_past_due": row.days_past_due,
        "total_paid": total_paid,
        "days_to_due": row.days_to_due,
        "risk_segment": row.risk_segment,
        "is_high_balance_risk": bool(row.is_high_balance_risk),
        "_person_id": str(row.person_id) if row.person_id else "",
        "_external_id": row.external_id,
        "_subscriber_number": row.subscriber_number or "",
        "_last_synced_at": row.refreshed_at.isoformat() if row.refreshed_at else "",
        "prepaid_unpaid_invoice_count": _parse_int(source_metadata.get("prepaid_unpaid_invoice_count")) or 0,
        "prepaid_unpaid_invoice_balance_due": float(
            _parse_decimal(source_metadata.get("prepaid_unpaid_invoice_balance_due"))
        ),
        "prepaid_unpaid_last_invoice_date": str(source_metadata.get("prepaid_unpaid_last_invoice_date") or ""),
        "prepaid_unpaid_next_due_date": str(source_metadata.get("prepaid_unpaid_next_due_date") or ""),
    }


def _selected_segment_labels(selected_segments: list[str] | None) -> set[str]:
    return {SEGMENT_LABELS[key] for key in selected_segments or [] if key in SEGMENT_LABELS}


def _days_past_due_bounds(value: str | None) -> tuple[int | None, int | None] | None:
    normalized = (value or "").strip().lower().replace("_", "-")
    if not normalized:
        return None
    if normalized in {"current", "0"}:
        return (None, 0)
    if normalized in {"1-7", "1-to-7", "1 to 7", "within-7", "within7"}:
        return (1, 7)
    if normalized in {"8-30", "8-to-30", "8 to 30"}:
        return (8, 30)
    if normalized in {"31+", "31-plus", "31-and-above", "over30", "over-30", "31"}:
        return (31, None)
    return None


def _blocked_days_bounds(value: str | None) -> tuple[int | None, int | None] | None:
    normalized = (value or "").strip().lower()
    if not normalized or normalized == "all":
        return None
    if normalized == "0-7":
        return (0, 7)
    if normalized == "8-30":
        return (8, 30)
    if normalized == "31-60":
        return (31, 60)
    if normalized == "61+":
        return (61, None)
    return None


def _base_query(
    db: Session,
    *,
    due_soon_days: int,
    high_balance_only: bool,
    selected_segments: list[str] | None,
    days_past_due: str | None,
    search: str | None,
    overdue_bucket: str | None,
    location: str | None = None,
    customer_status: str | None = None,
    billing_type: str | None = None,
):
    query = db.query(SubscriberBillingRiskSnapshot)
    due_soon_days = max(1, min(int(due_soon_days or 7), 30))
    query = query.filter(
        or_(
            SubscriberBillingRiskSnapshot.risk_segment != "Due Soon",
            SubscriberBillingRiskSnapshot.days_to_due.is_(None),
            SubscriberBillingRiskSnapshot.days_to_due <= due_soon_days,
        )
    )

    labels = _selected_segment_labels(selected_segments)
    if labels:
        query = query.filter(SubscriberBillingRiskSnapshot.risk_segment.in_(labels))
    if high_balance_only:
        query = query.filter(SubscriberBillingRiskSnapshot.is_high_balance_risk.is_(True))

    days_bounds = _days_past_due_bounds(days_past_due)
    if days_bounds:
        lower, upper = days_bounds
        if lower is not None:
            query = query.filter(SubscriberBillingRiskSnapshot.days_past_due >= lower)
        if upper is not None:
            query = query.filter(SubscriberBillingRiskSnapshot.days_past_due <= upper)

    blocked_bounds = _blocked_days_bounds(overdue_bucket)
    if blocked_bounds:
        lower, upper = blocked_bounds
        query = query.filter(SubscriberBillingRiskSnapshot.blocked_for_days.isnot(None))
        if lower is not None:
            query = query.filter(SubscriberBillingRiskSnapshot.blocked_for_days >= lower)
        if upper is not None:
            query = query.filter(SubscriberBillingRiskSnapshot.blocked_for_days <= upper)

    normalized_search = (search or "").strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        query = query.filter(
            or_(
                SubscriberBillingRiskSnapshot.name.ilike(pattern),
                SubscriberBillingRiskSnapshot.email.ilike(pattern),
                SubscriberBillingRiskSnapshot.phone.ilike(pattern),
                SubscriberBillingRiskSnapshot.city.ilike(pattern),
                SubscriberBillingRiskSnapshot.location.ilike(pattern),
                SubscriberBillingRiskSnapshot.area.ilike(pattern),
                SubscriberBillingRiskSnapshot.plan.ilike(pattern),
                SubscriberBillingRiskSnapshot.external_id.ilike(pattern),
                SubscriberBillingRiskSnapshot.subscriber_number.ilike(pattern),
            )
        )
    normalized_location = (location or "").strip()
    if normalized_location:
        query = query.filter(
            or_(
                SubscriberBillingRiskSnapshot.location == normalized_location,
                and_(
                    or_(
                        SubscriberBillingRiskSnapshot.location.is_(None),
                        SubscriberBillingRiskSnapshot.location == "",
                        func.lower(SubscriberBillingRiskSnapshot.location).in_(_GENERIC_LOCATION_VALUES),
                    ),
                    or_(
                        SubscriberBillingRiskSnapshot.city == normalized_location,
                        SubscriberBillingRiskSnapshot.area == normalized_location,
                    ),
                ),
            )
        )
    normalized_status = _normalized_customer_status(customer_status)
    if normalized_status == "active":
        query = query.filter(func.lower(func.coalesce(SubscriberBillingRiskSnapshot.subscriber_status, "")) == "active")
    elif normalized_status == "suspended":
        query = query.filter(
            func.lower(func.coalesce(SubscriberBillingRiskSnapshot.subscriber_status, "")) == "suspended"
        )

    normalized_billing_type = _normalized_billing_type(billing_type)
    if normalized_billing_type == "prepaid":
        query = query.filter(_billing_type_expr() == "prepaid")
    elif normalized_billing_type == "postpaid":
        query = query.filter(_billing_type_expr() == "postpaid")
    return query


def cache_metadata(db: Session) -> dict[str, Any]:
    row = db.query(
        func.count(SubscriberBillingRiskSnapshot.id).label("row_count"),
        func.max(SubscriberBillingRiskSnapshot.refreshed_at).label("refreshed_at"),
    ).one()
    refreshed_at = row.refreshed_at
    if refreshed_at is not None and refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=UTC)
    return {"row_count": int(row.row_count or 0), "refreshed_at": refreshed_at}


def list_cached_rows(
    db: Session,
    *,
    due_soon_days: int = 7,
    high_balance_only: bool = False,
    selected_segments: list[str] | None = None,
    days_past_due: str | None = None,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    overdue_bucket: str | None = None,
    location: str | None = None,
    customer_status: str | None = None,
    billing_type: str | None = None,
) -> BillingRiskPage:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    query = _base_query(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=days_past_due,
        search=search,
        overdue_bucket=overdue_bucket,
        location=location,
        customer_status=customer_status,
        billing_type=billing_type,
    )
    total_count = int(query.count())
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(page, total_pages)
    rows = (
        query.order_by(
            SubscriberBillingRiskSnapshot.is_high_balance_risk.desc(),
            SubscriberBillingRiskSnapshot.balance.desc(),
            SubscriberBillingRiskSnapshot.days_to_due.asc().nulls_last(),
            SubscriberBillingRiskSnapshot.name.asc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size + 1)
        .all()
    )
    visible_rows = [_snapshot_to_dict(row) for row in rows[:page_size]]
    return BillingRiskPage(
        rows=visible_rows,
        page_metrics=page_metrics(visible_rows),
        has_next=len(rows) > page_size,
        total_count=total_count,
        total_pages=total_pages,
        page=page,
    )


def all_cached_rows(
    db: Session,
    *,
    due_soon_days: int = 7,
    high_balance_only: bool = False,
    selected_segments: list[str] | None = None,
    days_past_due: str | None = None,
    search: str | None = None,
    overdue_bucket: str | None = None,
    location: str | None = None,
    customer_status: str | None = None,
    billing_type: str | None = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    rows = (
        _base_query(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=days_past_due,
            search=search,
            overdue_bucket=overdue_bucket,
            location=location,
            customer_status=customer_status,
            billing_type=billing_type,
        )
        .order_by(
            SubscriberBillingRiskSnapshot.is_high_balance_risk.desc(),
            SubscriberBillingRiskSnapshot.balance.desc(),
            SubscriberBillingRiskSnapshot.days_to_due.asc().nulls_last(),
            SubscriberBillingRiskSnapshot.name.asc(),
        )
        .limit(max(1, int(limit)))
        .all()
    )
    return [_snapshot_to_dict(row) for row in rows]


def cached_rows_by_external_ids(
    db: Session,
    external_ids: list[str],
    *,
    due_soon_days: int = 7,
    high_balance_only: bool = False,
    selected_segments: list[str] | None = None,
    days_past_due: str | None = None,
    search: str | None = None,
    overdue_bucket: str | None = None,
    location: str | None = None,
    customer_status: str | None = None,
    billing_type: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    normalized_ids = sorted({str(value or "").strip() for value in external_ids if str(value or "").strip()})
    if not normalized_ids:
        return []
    query = _base_query(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=days_past_due,
        search=search,
        overdue_bucket=overdue_bucket,
        location=location,
        customer_status=customer_status,
        billing_type=billing_type,
    ).filter(SubscriberBillingRiskSnapshot.external_id.in_(normalized_ids))
    rows = query.order_by(
        SubscriberBillingRiskSnapshot.is_high_balance_risk.desc(),
        SubscriberBillingRiskSnapshot.balance.desc(),
        SubscriberBillingRiskSnapshot.days_to_due.asc().nulls_last(),
        SubscriberBillingRiskSnapshot.name.asc(),
    )
    if limit is not None:
        rows = rows.limit(max(1, int(limit)))
    return [_snapshot_to_dict(row) for row in rows.all()]


def cached_row_by_external_id(
    db: Session,
    external_id: str,
    *,
    due_soon_days: int = 7,
) -> dict[str, Any] | None:
    rows = cached_rows_by_external_ids(
        db,
        [external_id],
        due_soon_days=due_soon_days,
        limit=1,
    )
    return rows[0] if rows else None


def page_metrics(rows: list[dict[str, Any]]) -> dict[str, int | float]:
    total_balance = round(sum(float(row.get("balance") or 0) for row in rows), 2)
    overdue_values = [int(row["days_past_due"]) for row in rows if isinstance(row.get("days_past_due"), int)]
    avg_days_overdue = round(sum(overdue_values) / len(overdue_values)) if overdue_values else 0
    return {
        "total_count": len(rows),
        "total_balance": total_balance,
        "avg_days_overdue": avg_days_overdue,
    }


def _filtered_snapshot_query(
    db: Session,
    *,
    due_soon_days: int = 7,
    high_balance_only: bool = False,
    selected_segments: list[str] | None = None,
    days_past_due: str | None = None,
    search: str | None = None,
    overdue_bucket: str | None = None,
    location: str | None = None,
    customer_status: str | None = None,
    billing_type: str | None = None,
):
    return _base_query(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=days_past_due,
        search=search,
        overdue_bucket=overdue_bucket,
        location=location,
        customer_status=customer_status,
        billing_type=billing_type,
    )


def location_options_cached(
    db: Session,
    *,
    due_soon_days: int = 7,
    high_balance_only: bool = False,
    selected_segments: list[str] | None = None,
    days_past_due: str | None = None,
    search: str | None = None,
    overdue_bucket: str | None = None,
) -> list[str]:
    """Return distinct cached billing-risk locations for the current filters."""
    rows = (
        _base_query(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=days_past_due,
            search=search,
            overdue_bucket=overdue_bucket,
        )
        .with_entities(
            SubscriberBillingRiskSnapshot.location,
            SubscriberBillingRiskSnapshot.city,
            SubscriberBillingRiskSnapshot.area,
        )
        .distinct()
        .order_by(SubscriberBillingRiskSnapshot.location.asc())
        .all()
    )
    locations = {_display_location(location, city, area) for location, city, area in rows}
    return sorted((location for location in locations if location), key=str.casefold)


def summary_cached(
    db: Session,
    *,
    due_soon_days: int = 7,
    high_balance_only: bool = False,
    selected_segments: list[str] | None = None,
    days_past_due: str | None = None,
    search: str | None = None,
    overdue_bucket: str | None = None,
    location: str | None = None,
    overdue_invoice_balance: float = 0,
) -> dict[str, float | int]:
    """Compute billing-risk KPIs from cached snapshot rows without loading all rows."""
    query = _filtered_snapshot_query(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=days_past_due,
        search=search,
        overdue_bucket=overdue_bucket,
        location=location,
    )
    row = query.with_entities(
        func.count(SubscriberBillingRiskSnapshot.id).label("total_at_risk"),
        func.coalesce(func.sum(SubscriberBillingRiskSnapshot.balance), 0).label("total_balance_exposure"),
        func.coalesce(
            func.sum(case((SubscriberBillingRiskSnapshot.is_high_balance_risk.is_(True), 1), else_=0)),
            0,
        ).label("high_balance_risk_count"),
        func.coalesce(
            func.sum(case((SubscriberBillingRiskSnapshot.risk_segment == "Due Soon", 1), else_=0)),
            0,
        ).label("overdue_count"),
        func.coalesce(
            func.sum(
                case(
                    (SubscriberBillingRiskSnapshot.risk_segment == "Due Soon", SubscriberBillingRiskSnapshot.balance),
                    else_=0,
                )
            ),
            0,
        ).label("overdue_balance_exposure"),
    ).one()
    total_at_risk = int(row.total_at_risk or 0)
    high_balance_risk_count = int(row.high_balance_risk_count or 0)
    return {
        "total_at_risk": total_at_risk,
        "total_balance_exposure": round(float(row.total_balance_exposure or 0), 2),
        "high_balance_risk_count": high_balance_risk_count,
        "high_balance_risk_pct": round((high_balance_risk_count / total_at_risk) * 100, 1) if total_at_risk else 0,
        "overdue_count": int(row.overdue_count or 0),
        "overdue_balance_exposure": round(float(row.overdue_balance_exposure or 0), 2),
        "overdue_invoice_balance": round(float(overdue_invoice_balance or 0), 2),
        "recent_churned_count": 0,
        "recent_churn_rate": 0,
        "recent_revenue_lost": 0,
    }


def segment_breakdown_cached(
    db: Session,
    *,
    due_soon_days: int = 7,
    high_balance_only: bool = False,
    selected_segments: list[str] | None = None,
    days_past_due: str | None = None,
    search: str | None = None,
    overdue_bucket: str | None = None,
    location: str | None = None,
) -> list[dict[str, float | int | str]]:
    """Build segment breakdown from SQL aggregates instead of Python-side full rows."""
    query = _filtered_snapshot_query(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=days_past_due,
        search=search,
        overdue_bucket=overdue_bucket,
        location=location,
    )
    total_count = int(query.count())
    grouped_rows = (
        query.with_entities(
            SubscriberBillingRiskSnapshot.risk_segment.label("segment"),
            func.count(SubscriberBillingRiskSnapshot.id).label("count"),
            func.coalesce(func.sum(SubscriberBillingRiskSnapshot.balance), 0).label("balance"),
            func.coalesce(
                func.sum(case((SubscriberBillingRiskSnapshot.is_high_balance_risk.is_(True), 1), else_=0)),
                0,
            ).label("high_balance_count"),
            func.avg(SubscriberBillingRiskSnapshot.days_since_last_payment).label("avg_payment_days"),
        )
        .group_by(SubscriberBillingRiskSnapshot.risk_segment)
        .all()
    )
    segment_order = ["Due Soon", "Suspended", "Churned", "Pending"]
    results: list[dict[str, float | int | str]] = []
    for row in grouped_rows:
        count = int(row.count or 0)
        if count <= 0:
            continue
        balance = round(float(row.balance or 0), 2)
        avg_payment_days = row.avg_payment_days
        billing_mix = (
            f"Avg {round(float(avg_payment_days))}d since payment ({count} accounts)"
            if avg_payment_days is not None
            else "No billing cycle data"
        )
        results.append(
            {
                "segment": row.segment or "Unknown",
                "count": count,
                "balance": balance,
                "high_balance_count": int(row.high_balance_count or 0),
                "avg_balance": round(balance / count, 2) if count else 0,
                "share_pct": round((count / total_count) * 100, 1) if total_count else 0,
                "billing_mix": billing_mix,
            }
        )
    results.sort(
        key=lambda item: (
            segment_order.index(str(item["segment"])) if str(item["segment"]) in segment_order else len(segment_order),
            -int(item["count"]),
        )
    )
    return results


def aging_buckets_cached(
    db: Session,
    *,
    due_soon_days: int = 7,
    high_balance_only: bool = False,
    selected_segments: list[str] | None = None,
    days_past_due: str | None = None,
    search: str | None = None,
    overdue_bucket: str | None = None,
    location: str | None = None,
) -> list[dict[str, int | str]]:
    """Build aging buckets from cached blocked-day values."""
    query = _filtered_snapshot_query(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=days_past_due,
        search=search,
        overdue_bucket=overdue_bucket,
        location=location,
    )
    buckets = {
        "Blocked 0-7 Days": query.filter(SubscriberBillingRiskSnapshot.blocked_for_days.between(0, 7)).count(),
        "Blocked 8-30 Days": query.filter(SubscriberBillingRiskSnapshot.blocked_for_days.between(8, 30)).count(),
        "Blocked 31-60 Days": query.filter(SubscriberBillingRiskSnapshot.blocked_for_days.between(31, 60)).count(),
        "Blocked 61+ Days": query.filter(SubscriberBillingRiskSnapshot.blocked_for_days >= 61).count(),
        "No Blocked Date": query.filter(SubscriberBillingRiskSnapshot.blocked_for_days.is_(None)).count(),
    }
    return [{"label": label, "count": int(count or 0)} for label, count in buckets.items()]


def summary(rows: list[dict[str, Any]], overdue_invoices: list[dict[str, Any]]) -> dict[str, float | int]:
    return live_billing_risk.get_billing_risk_summary(rows, overdue_invoices)


def segment_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return live_billing_risk.get_billing_risk_segment_breakdown(rows)


def aging_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return live_billing_risk.get_billing_risk_aging_buckets(rows)


def _snapshot_values(
    row: dict[str, Any], *, refreshed_at: datetime, subscribers_by_external: dict[str, Subscriber]
) -> dict:
    external_id = str(row.get("_external_id") or row.get("subscriber_id") or "").strip()
    subscriber = subscribers_by_external.get(external_id)
    raw_prepaid_invoice_summary = row.get("prepaid_unpaid_invoice_summary")
    prepaid_invoice_summary: dict[str, Any] = (
        raw_prepaid_invoice_summary if isinstance(raw_prepaid_invoice_summary, dict) else {}
    )
    return {
        "id": uuid.uuid4(),
        "external_system": "selfcare",
        "external_id": external_id,
        "subscriber_number": str(row.get("_subscriber_number") or "").strip() or None,
        "person_id": subscriber.person_id if subscriber else None,
        "subscriber_id": subscriber.id if subscriber else None,
        "name": str(row.get("name") or "Unknown")[:200],
        "email": str(row.get("email") or "").strip()[:255] or None,
        "phone": str(row.get("phone") or "").strip()[:120] or None,
        "city": str(row.get("city") or "").strip()[:120] or None,
        "location": str(row.get("location") or "").strip()[:160] or None,
        "area": str(row.get("area") or "").strip()[:160] or None,
        "plan": str(row.get("plan") or "").strip()[:200] or None,
        "subscriber_status": str(row.get("subscriber_status") or "").strip()[:80] or None,
        "risk_segment": str(row.get("risk_segment") or "Pending")[:40],
        "is_high_balance_risk": bool(row.get("is_high_balance_risk")),
        "mrr_total": _parse_decimal(row.get("mrr_total")),
        "balance": _parse_decimal(row.get("balance")),
        "total_paid": _parse_decimal(row.get("total_paid")),
        "billing_cycle": str(row.get("billing_cycle") or "").strip()[:80] or None,
        "billing_start_date": _parse_date(row.get("billing_start_date")),
        "billing_end_date": _parse_date(row.get("billing_end_date")),
        "next_bill_date": _parse_date(row.get("next_bill_date")),
        "blocked_date": _parse_date(row.get("blocked_date")),
        "last_transaction_date": _parse_date(row.get("last_transaction_date")),
        "invoiced_until": _parse_date(row.get("invoiced_until")),
        "days_to_due": _parse_int(row.get("days_to_due")),
        "days_past_due": _parse_int(row.get("days_past_due")),
        "days_since_last_payment": _parse_int(row.get("days_since_last_payment")),
        "blocked_for_days": _parse_int(row.get("blocked_for_days")),
        "expires_in": str(row.get("expires_in") or "").strip()[:80] or None,
        "source_metadata": {
            "last_synced_at": row.get("_last_synced_at") or "",
            "billing_type": row.get("billing_type") or "",
            "billing_mode": row.get("billing_mode") or "",
            "subscription_billing_mode": row.get("subscription_billing_mode") or "",
            "account_billing_mode": row.get("account_billing_mode") or "",
            "account_balance_deposit": row.get("account_balance_deposit"),
            "last_payment_date": _first_text(row.get("last_payment_date"), row.get("last_transaction_date")),
            "last_payment_amount": str(_parse_decimal(row.get("last_payment_amount"))),
            "prepaid_unpaid_invoice_count": prepaid_invoice_summary.get("count") or 0,
            "prepaid_unpaid_invoice_balance_due": str(_parse_decimal(prepaid_invoice_summary.get("balance_due"))),
            "prepaid_unpaid_last_invoice_date": prepaid_invoice_summary.get("last_invoice_date") or "",
            "prepaid_unpaid_next_due_date": prepaid_invoice_summary.get("next_due_date") or "",
            "source": row.get("_source") or "billing_risk_live_builder",
        },
        "refreshed_at": refreshed_at,
        "created_at": refreshed_at,
        "updated_at": refreshed_at,
    }


def refresh_cache(
    db: Session,
    *,
    due_soon_days: int = 30,
    limit: int = 10000,
) -> dict[str, Any]:
    """Rebuild the cached report from the existing live billing-risk builder."""
    started_at = datetime.now(UTC)
    rows = live_billing_risk.get_billing_risk_table(
        db,
        due_soon_days=max(1, min(int(due_soon_days or 30), 30)),
        limit=max(1, int(limit)),
        enrich_visible_rows=False,
    )
    live_billing_risk.enrich_billing_risk_rows(rows)
    _enrich_cached_payment_and_invoice_fields(db, rows)
    external_ids = {
        str(row.get("_external_id") or row.get("subscriber_id") or "").strip()
        for row in rows
        if str(row.get("_external_id") or row.get("subscriber_id") or "").strip()
    }
    subscribers_by_external: dict[str, Subscriber] = {}
    if external_ids:
        subscribers_by_external = {
            str(sub.external_id): sub
            for sub in db.query(Subscriber)
            .filter(Subscriber.external_system == "selfcare")
            .filter(Subscriber.external_id.in_(external_ids))
            .all()
            if sub.external_id
        }

    db.query(SubscriberBillingRiskSnapshot).delete(synchronize_session=False)
    values = [
        _snapshot_values(row, refreshed_at=started_at, subscribers_by_external=subscribers_by_external)
        for row in rows
        if str(row.get("_external_id") or row.get("subscriber_id") or "").strip()
    ]
    if values:
        db.bulk_insert_mappings(SubscriberBillingRiskSnapshot.__mapper__, values)
    db.commit()
    return {"rows": len(values), "refreshed_at": started_at.isoformat()}
