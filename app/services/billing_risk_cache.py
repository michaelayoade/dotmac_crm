"""Cached billing-risk report storage and query helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.models.subscriber import Subscriber, SubscriberBillingRiskSnapshot
from app.services import billing_risk_reports as live_billing_risk

SEGMENT_LABELS = {
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


def _snapshot_to_dict(row: SubscriberBillingRiskSnapshot) -> dict[str, Any]:
    balance = float(row.balance or 0)
    mrr_total = float(row.mrr_total or 0)
    total_paid = float(row.total_paid or 0)
    return {
        "subscriber_id": row.external_id,
        "name": row.name,
        "email": row.email or "",
        "phone": row.phone or "",
        "city": row.city or "",
        "location": row.location or "",
        "mrr_total": mrr_total,
        "subscriber_status": row.subscriber_status or "",
        "area": row.area or "",
        "plan": row.plan or "",
        "billing_start_date": _date_text(row.billing_start_date),
        "billing_end_date": _date_text(row.billing_end_date),
        "next_bill_date": _date_text(row.next_bill_date),
        "balance": balance,
        "billing_cycle": row.billing_cycle or "",
        "blocked_date": _date_text(row.blocked_date),
        "blocked_for_days": row.blocked_for_days,
        "last_transaction_date": _date_text(row.last_transaction_date),
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
        query = query.filter(SubscriberBillingRiskSnapshot.location == normalized_location)
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
        .with_entities(SubscriberBillingRiskSnapshot.location)
        .filter(SubscriberBillingRiskSnapshot.location.isnot(None))
        .filter(SubscriberBillingRiskSnapshot.location != "")
        .distinct()
        .order_by(SubscriberBillingRiskSnapshot.location.asc())
        .all()
    )
    return [str(location).strip() for (location,) in rows if str(location or "").strip()]


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
    return {
        "id": uuid.uuid4(),
        "external_system": "splynx",
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
            "source": "billing_risk_live_builder",
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
            .filter(Subscriber.external_system == "splynx")
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
