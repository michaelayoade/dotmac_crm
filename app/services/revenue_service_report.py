"""Revenue and service report helpers backed by live Selfcare data."""

from __future__ import annotations

import contextlib
import logging
import os
import time
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services import selfcare

logger = logging.getLogger(__name__)

DEFAULT_PAGE_LIMIT = 5000
DEFAULT_TRANSACTION_START_OFFSET = 210000
DEFAULT_TRANSACTION_END_OFFSET = 240000
DEFAULT_LOG_MAX_ROWS = 60
DEFAULT_PAYMENT_START_OFFSET = 85000
DEFAULT_PAYMENT_END_OFFSET = 100000
DEFAULT_PAYMENT_CLASSIFICATION_MAX_CUSTOMERS = 1000
PAYMENT_TOLERANCE = Decimal("0.15")
DEFAULT_VAT_RATE = Decimal("0.075")
UPTIME_CACHE_TTL_SECONDS = 300
_UPTIME_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}


class SelfcareReportError(RuntimeError):
    """Raised when live Selfcare report data cannot be loaded."""



def _env_int(name: str, default: int) -> int:
    with contextlib.suppress(TypeError, ValueError):
        value = int(os.getenv(name, ""))
        if value >= 0:
            return value
    return default


def _config(db: Session) -> dict[str, Any]:
    try:
        config = selfcare._get_api_config(db)
        config["_db"] = db
        return config
    except selfcare.SelfcareProviderError as exc:
        raise SelfcareReportError(str(exc)) from exc


def _headers(config: dict[str, Any]) -> dict[str, str]:
    token = str(config.get("api_token") or "").strip()
    if not token:
        raise SelfcareReportError("Selfcare API token is missing.")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "rows"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, dict)]
        return [payload]
    return []


def _get_json(config: dict[str, Any], url: str, params: dict[str, Any] | None = None) -> Any:
    raise SelfcareReportError("_get_json is not used by the Selfcare-backed report provider.")


def _api_base_url(config: dict[str, Any]) -> str:
    return f"{config['base_url'].rstrip('/')}/api/v1/crm"


def _customer_url(config: dict[str, Any]) -> str:
    return f"{_api_base_url(config)}/subscribers"


def _page_limit() -> int:
    return _env_int("SUBSCRIBER_REPORT_PAGE_LIMIT", DEFAULT_PAGE_LIMIT)


def _transaction_start_offset() -> int:
    return _env_int("SUBSCRIBER_REPORT_TXN_SCAN_START_OFFSET", DEFAULT_TRANSACTION_START_OFFSET)


def _transaction_end_offset() -> int:
    return _env_int("SUBSCRIBER_REPORT_TXN_SCAN_END_OFFSET", DEFAULT_TRANSACTION_END_OFFSET)


def _payment_start_offset() -> int:
    return _env_int("SUBSCRIBER_REPORT_PAYMENT_SCAN_START_OFFSET", DEFAULT_PAYMENT_START_OFFSET)


def _payment_end_offset() -> int:
    return _env_int("SUBSCRIBER_REPORT_PAYMENT_SCAN_END_OFFSET", DEFAULT_PAYMENT_END_OFFSET)


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _vat_rate() -> Decimal:
    raw = os.getenv("PAYMENT_CLASSIFICATION_VAT_RATE", str(DEFAULT_VAT_RATE))
    with contextlib.suppress(InvalidOperation, ValueError):
        rate = Decimal(str(raw))
        if rate >= 0:
            return rate
    return DEFAULT_VAT_RATE


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text or text.startswith("0000"):
        return None
    with contextlib.suppress(ValueError):
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    return None


def _extension_days(period_from: Any, period_to: Any) -> int:
    start = _parse_date(period_from)
    end = _parse_date(period_to)
    if not start or not end:
        return 0
    return max((end - start).days, 0)


def _selected_month(year: int | None = None, month: int | None = None) -> tuple[int, int]:
    today = datetime.now(UTC).date()
    return year or today.year, month or today.month


def _month_bounds(year: int | None = None, month: int | None = None) -> tuple[date, date]:
    selected_year, selected_month = _selected_month(year, month)
    month_start = date(selected_year, selected_month, 1)
    month_end_exclusive = date(
        selected_year + (1 if selected_month == 12 else 0),
        1 if selected_month == 12 else selected_month + 1,
        1,
    )
    return month_start, month_end_exclusive


def _extension_days_in_month(
    period_from: Any, period_to: Any, year: int | None = None, month: int | None = None
) -> int:
    start = _parse_date(period_from)
    end = _parse_date(period_to)
    if not start or not end:
        return 0
    month_start, month_end_exclusive = _month_bounds(year, month)
    overlap_start = max(start, month_start)
    overlap_end = min(end, month_end_exclusive)
    return max((overlap_end - overlap_start).days, 0)


def _days_in_billing_month(period_from: Any) -> int:
    start = _parse_date(period_from)
    if not start:
        return 30
    return monthrange(start.year, start.month)[1]


def _is_extension_transaction(row: dict[str, Any]) -> bool:
    description = str(row.get("description") or "").lower()
    service_type = str(row.get("service_type") or "").lower()
    return "extending expiration" in description and (not service_type or service_type == "internet")


def _is_transaction_in_month(row: dict[str, Any], year: int | None = None, month: int | None = None) -> bool:
    transaction_date = _parse_date(row.get("date"))
    if not transaction_date:
        return False
    selected_year, selected_month = _selected_month(year, month)
    return transaction_date.year == selected_year and transaction_date.month == selected_month


def _select_active_service(services: list[dict[str, Any]]) -> dict[str, Any] | None:
    return (
        next(
            (
                service
                for service in services
                if str(service.get("status") or "").lower() == "active"
                and str(service.get("type") or "").lower() == "internet"
            ),
            None,
        )
        or next((service for service in services if str(service.get("status") or "").lower() == "active"), None)
        or next((service for service in services if str(service.get("type") or "").lower() == "internet"), None)
        or (services[0] if services else None)
    )


def _service_for_transaction(
    services: list[dict[str, Any]],
    transaction: dict[str, Any],
) -> dict[str, Any] | None:
    service_id = str(transaction.get("service_id") or "").strip()
    if service_id:
        match = next((service for service in services if str(service.get("id") or "") == service_id), None)
        if match:
            return match
    return _select_active_service(services)


def _fetch_customers_page(config: dict[str, Any], offset: int, limit: int) -> list[dict[str, Any]]:
    db = config.get("_db")
    if not isinstance(db, Session):
        return []
    return selfcare._rows(
        selfcare._request_json(
            db, "GET", "/subscribers", params={"offset": offset, "limit": limit, "include": "services,billing"}
        )
    )


def _fetch_transactions_page(config: dict[str, Any], offset: int, limit: int) -> list[dict[str, Any]]:
    db = config.get("_db")
    if not isinstance(db, Session):
        return []
    return selfcare.fetch_transactions(db, offset=offset, limit=limit)


def _fetch_payments_page(config: dict[str, Any], offset: int, limit: int) -> list[dict[str, Any]]:
    db = config.get("_db")
    if not isinstance(db, Session):
        return []
    return selfcare.fetch_payments(db, offset=offset, limit=limit)


def _cached(cache_key: tuple[Any, ...], loader):
    now = time.monotonic()
    cached = _UPTIME_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]
    value = loader()
    _UPTIME_CACHE[cache_key] = (now + UPTIME_CACHE_TTL_SECONDS, value)
    return value


def _parse_month(value: str | None) -> tuple[int, int]:
    if value:
        with contextlib.suppress(ValueError):
            parsed = datetime.strptime(value[:7], "%Y-%m")
            return parsed.year, parsed.month
    today = datetime.now(UTC).date()
    return today.year, today.month


def _month_datetime_bounds(month_value: str | None) -> tuple[datetime, datetime, datetime, str]:
    year, month = _parse_month(month_value)
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1, tzinfo=UTC)
    now = datetime.now(UTC)
    effective_end = min(end, now) if year == now.year and month == now.month else end
    return start, end, effective_end, f"{year:04d}-{month:02d}"


def _parse_session_datetime(row: dict[str, Any], date_key: str, time_key: str) -> datetime | None:
    combined_key = "start_at" if date_key.startswith("start") else "end_at"
    text = str(row.get(combined_key) or row.get(date_key) or "").strip()
    if row.get(time_key):
        text = f"{row.get(date_key) or ''} {row.get(time_key) or ''}".strip()
    if not text or text.startswith("0000"):
        return None
    with contextlib.suppress(ValueError):
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    with contextlib.suppress(ValueError):
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return None


def _fetch_customer_statistics(db: Session, customer_id: str, limit: int = 10000) -> list[dict[str, Any]]:
    config = _config(db)
    return _fetch_customer_statistics_with_config(config, customer_id, limit)


def _fetch_customer_statistics_with_config(
    config: dict[str, Any], customer_id: str, limit: int = 10000
) -> list[dict[str, Any]]:
    db = config.get("_db")
    if not isinstance(db, Session):
        return []
    return _cached(
        ("customer_statistics", customer_id, limit),
        lambda: selfcare.fetch_customer_sessions(db, customer_id, limit=limit),
    )


def _active_service_payload(db: Session, customer_id: str) -> dict[str, Any]:
    services = selfcare.fetch_customer_internet_services(db, customer_id)
    return _select_active_service(services) or {}


def _format_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _seconds_to_duration(seconds: int) -> str:
    hours, remainder = divmod(max(seconds, 0), 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m"


def _byte_label(value: Any) -> str:
    amount = float(_money(value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:,.1f} {unit}" if unit != "B" else f"{amount:,.0f} {unit}"
        amount /= 1024
    return "0 B"


def search_uptime_customers(db: Session, query: str) -> list[dict[str, Any]]:
    search = query.strip()
    if len(search) < 2:
        return []
    customer = find_customer(db, search)
    matches = [customer] if customer else []
    words = [word for word in search.lower().split() if word]
    if len(matches) < 8:
        with contextlib.suppress(SelfcareReportError, selfcare.SelfcareProviderError):
            for row in selfcare.search_subscribers(db, search, limit=12):
                if _customer_matches(row, words) and all(str(row.get("id")) != str(item.get("id")) for item in matches):
                    matches.append(row)
    return [
        {
            "customer_id": row.get("id"),
            "login": row.get("login") or "",
            "customer_name": row.get("name") or f"Customer {row.get('id')}",
            "email": row.get("email") or "",
            "phone": row.get("phone") or "",
            "status": row.get("status") or "",
        }
        for row in matches[:8]
        if row
    ]


def _clip_session_rows_for_month(
    raw_rows: list[dict[str, Any]],
    month: str | None,
) -> tuple[list[dict[str, Any]], datetime, datetime, datetime, str]:
    month_start, month_end, effective_end, selected_month = _month_datetime_bounds(month)
    clipped: list[dict[str, Any]] = []
    for row in raw_rows:
        session_start = _parse_session_datetime(row, "start_date", "start_time")
        session_end = _parse_session_datetime(row, "end_date", "end_time")
        if not session_start or not session_end or session_end <= session_start:
            continue
        clip_start = max(session_start, month_start)
        clip_end = min(session_end, effective_end)
        clipped_seconds = max(int((clip_end - clip_start).total_seconds()), 0)
        if clipped_seconds <= 0:
            continue
        clipped.append(
            {
                **row,
                "session_start": session_start,
                "session_end": session_end,
                "clipped_start": clip_start,
                "clipped_end": clip_end,
                "duration_seconds": int((session_end - session_start).total_seconds()),
                "clipped_duration_seconds": clipped_seconds,
                "crosses_month_boundary": session_start < month_start or session_end > effective_end,
            }
        )
    clipped.sort(key=lambda item: item["session_start"])
    return clipped, month_start, month_end, effective_end, selected_month


def _session_rows_for_month(
    db: Session, customer_id: str, month: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], datetime, datetime, datetime, str]:
    raw_rows = _fetch_customer_statistics(db, customer_id)
    clipped, month_start, month_end, effective_end, selected_month = _clip_session_rows_for_month(raw_rows, month)
    return raw_rows, clipped, month_start, month_end, effective_end, selected_month


def _daily_breakdown(
    sessions: list[dict[str, Any]], month_start: datetime, effective_end: datetime
) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    cursor = month_start
    while cursor < effective_end:
        day_end = min(cursor + timedelta(days=1), effective_end)
        days.append(
            {
                "date": cursor.date().isoformat(),
                "online_seconds": 0,
                "session_count": 0,
                "first_session_start": "",
                "last_session_end": "",
                "_starts": [],
                "_ends": [],
            }
        )
        cursor = day_end

    day_map = {row["date"]: row for row in days}
    for session in sessions:
        cursor = session["clipped_start"]
        while cursor < session["clipped_end"]:
            next_day = datetime(cursor.year, cursor.month, cursor.day, tzinfo=UTC) + timedelta(days=1)
            segment_end = min(session["clipped_end"], next_day)
            seconds = int((segment_end - cursor).total_seconds())
            key = cursor.date().isoformat()
            row = day_map.get(key)
            if row:
                row["online_seconds"] += seconds
                row["session_count"] += 1
                row["_starts"].append(cursor)
                row["_ends"].append(segment_end)
            cursor = segment_end

    for row in days:
        day_start = datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=UTC)
        day_end = min(day_start + timedelta(days=1), effective_end)
        total_seconds = max(int((day_end - day_start).total_seconds()), 0)
        online_seconds = min(int(row["online_seconds"]), total_seconds)
        offline_seconds = max(total_seconds - online_seconds, 0)
        uptime = online_seconds / total_seconds * 100 if total_seconds else 0
        row.update(
            {
                "online_hours": online_seconds / 3600,
                "offline_hours": offline_seconds / 3600,
                "uptime_percent": uptime,
                "first_session_start": _format_dt(min(row["_starts"])) if row["_starts"] else "",
                "last_session_end": _format_dt(max(row["_ends"])) if row["_ends"] else "",
                "status": "Fully online"
                if total_seconds and online_seconds >= total_seconds
                else "Partial"
                if online_seconds > 0
                else "Offline",
                "has_session_data": bool(row["session_count"]),
            }
        )
        row.pop("_starts", None)
        row.pop("_ends", None)
    return days


def build_customer_uptime_profile(db: Session, customer_id: str, month: str | None = None) -> dict[str, Any]:
    customer = selfcare.fetch_customer(db, customer_id) or {"id": customer_id}
    service = _active_service_payload(db, customer_id)
    _, sessions, month_start, _month_end, effective_end, selected_month = _session_rows_for_month(
        db, customer_id, month
    )
    total_service_seconds = max(int((effective_end - month_start).total_seconds()), 0)
    online_seconds = min(sum(int(row["clipped_duration_seconds"]) for row in sessions), total_service_seconds)
    offline_seconds = max(total_service_seconds - online_seconds, 0)
    uptime_percent = online_seconds / total_service_seconds * 100 if total_service_seconds else 0
    downtime_percent = offline_seconds / total_service_seconds * 100 if total_service_seconds else 0
    unit_price = _money(service.get("unit_price") or customer.get("mrr_total"))
    service_received_value = (
        unit_price * Decimal(online_seconds) / Decimal(total_service_seconds) if total_service_seconds else Decimal("0")
    )
    downtime_value = (
        unit_price * Decimal(offline_seconds) / Decimal(total_service_seconds)
        if total_service_seconds
        else Decimal("0")
    )
    daily = _daily_breakdown(sessions, month_start, effective_end)
    return {
        "customer_id": customer.get("id") or customer_id,
        "login": customer.get("login") or (sessions[0].get("login") if sessions else ""),
        "customer_name": customer.get("name") or f"Customer {customer_id}",
        "customer_status": customer.get("status") or "",
        "service_id": service.get("id") or (sessions[-1].get("service_id") if sessions else ""),
        "service_name": service.get("description") or service.get("name") or "",
        "monthly_service_value": float(unit_price),
        "selected_month": selected_month,
        "online_hours": online_seconds / 3600,
        "offline_hours": offline_seconds / 3600,
        "total_service_hours": total_service_seconds / 3600,
        "uptime_percent": uptime_percent,
        "downtime_percent": downtime_percent,
        "service_received_value": float(service_received_value),
        "downtime_value": float(downtime_value),
        "session_count": len(sessions),
        "first_session_start": _format_dt(sessions[0]["session_start"]) if sessions else "",
        "last_session_end": _format_dt(sessions[-1]["session_end"]) if sessions else "",
        "daily_breakdown": daily,
        "source": "selfcare_subscriber_sessions",
        "confidence": "imported",
    }


def _monthly_session_uptime(
    db: Session,
    customer_id: str,
    year: int,
    month: int,
    cache: dict[tuple[str, int, int], dict[str, Any] | None],
) -> dict[str, Any] | None:
    """Calculate month-to-date uptime from Selfcare subscriber sessions for downtime rows."""
    key = (customer_id, year, month)
    if key in cache:
        return cache[key]

    month_value = f"{year:04d}-{month:02d}"
    try:
        raw_rows = _fetch_customer_statistics(db, customer_id)
    except SelfcareReportError:
        cache[key] = None
        return None

    payload = _monthly_session_uptime_from_rows(raw_rows, month_value)
    cache[key] = payload
    return payload


def _monthly_session_uptime_from_rows(raw_rows: list[dict[str, Any]], month_value: str) -> dict[str, Any]:
    sessions, month_start, _month_end, effective_end, _selected_month = _clip_session_rows_for_month(
        raw_rows, month_value
    )
    total_service_seconds = max(int((effective_end - month_start).total_seconds()), 0)
    online_seconds = min(sum(int(row["clipped_duration_seconds"]) for row in sessions), total_service_seconds)
    offline_seconds = max(total_service_seconds - online_seconds, 0)
    uptime_percent = online_seconds / total_service_seconds * 100 if total_service_seconds else 0
    payload = {
        "uptime_percent": uptime_percent,
        "online_hours": online_seconds / 3600,
        "offline_hours": offline_seconds / 3600,
        "total_service_hours": total_service_seconds / 3600,
        "session_count": len(sessions),
        "source": "selfcare_subscriber_sessions",
        "confidence": "imported",
    }
    return payload


def _session_uptime_map_for_customers(
    config: dict[str, Any],
    customer_ids: set[str],
    year: int,
    month: int,
) -> dict[str, dict[str, Any] | None]:
    if not customer_ids:
        return {}

    month_value = f"{year:04d}-{month:02d}"
    max_workers = max(1, min(_env_int("DOWNTIME_UPTIME_LOOKUP_WORKERS", 8), 16))
    results: dict[str, dict[str, Any] | None] = {}

    def load(customer_id: str) -> tuple[str, dict[str, Any] | None]:
        try:
            raw_rows = _fetch_customer_statistics_with_config(config, customer_id)
            return customer_id, _monthly_session_uptime_from_rows(raw_rows, month_value)
        except SelfcareReportError:
            return customer_id, None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(load, customer_id) for customer_id in customer_ids]
        for future in as_completed(futures):
            customer_id, payload = future.result()
            results[customer_id] = payload
    return results


def build_customer_uptime_sessions(db: Session, customer_id: str, month: str | None = None) -> dict[str, Any]:
    _, sessions, _month_start, _month_end, _effective_end, selected_month = _session_rows_for_month(
        db, customer_id, month
    )
    return {
        "selected_month": selected_month,
        "rows": [
            {
                "session_id": row.get("session_id") or "",
                "start_at": _format_dt(row["session_start"]),
                "end_at": _format_dt(row["session_end"]),
                "duration": _seconds_to_duration(int(row["duration_seconds"])),
                "duration_seconds": int(row["duration_seconds"]),
                "clipped_duration_seconds": int(row["clipped_duration_seconds"]),
                "in_bytes": _byte_label(row.get("in_bytes")),
                "out_bytes": _byte_label(row.get("out_bytes")),
                "nas_id": row.get("nas_id") or "",
                "terminate_cause": row.get("terminate_cause"),
                "crosses_month_boundary": bool(row.get("crosses_month_boundary")),
            }
            for row in sessions
        ],
    }


def build_customer_uptime_trend(db: Session, customer_id: str) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    months: list[tuple[int, int]] = []
    year, month = today.year, today.month
    for _ in range(6):
        months.append((year, month))
        if month == 1:
            year, month = year - 1, 12
        else:
            month -= 1
    months.reverse()
    rows = []
    for year, month in months:
        month_value = f"{year:04d}-{month:02d}"
        profile = build_customer_uptime_profile(db, customer_id, month_value)
        rows.append(
            {
                "month": month_value,
                "label": date(year, month, 1).strftime("%b %Y"),
                "uptime_percent": profile["uptime_percent"],
            }
        )
    return {"rows": rows}


def build_customer_uptime_compensation(db: Session, customer_id: str, month: str | None = None) -> dict[str, Any]:
    profile = build_customer_uptime_profile(db, customer_id, month)
    return {
        key: profile[key]
        for key in (
            "customer_id",
            "login",
            "customer_name",
            "monthly_service_value",
            "service_received_value",
            "downtime_value",
            "uptime_percent",
            "downtime_percent",
            "selected_month",
            "source",
            "confidence",
        )
    }


def _fetch_recent_transactions(db: Session) -> list[dict[str, Any]]:
    config = _config(db)
    limit = _page_limit()
    rows: list[dict[str, Any]] = []
    for offset in range(_transaction_start_offset(), _transaction_end_offset() + 1, limit):
        batch = _fetch_transactions_page(config, offset, limit)
        rows.extend(batch)
        if len(batch) < limit:
            break
    return rows


def _fetch_recent_payments(db: Session) -> list[dict[str, Any]]:
    config = _config(db)
    limit = _page_limit()
    rows: list[dict[str, Any]] = []
    for offset in range(_payment_start_offset(), _payment_end_offset() + 1, limit):
        batch = _fetch_payments_page(config, offset, limit)
        rows.extend(batch)
        if len(batch) < limit:
            break
    return rows


def _month_extension_transactions(
    transactions: list[dict[str, Any]],
    *,
    year: int | None = None,
    month: int | None = None,
) -> list[dict[str, Any]]:
    return sorted(
        [
            row
            for row in transactions
            if _is_extension_transaction(row)
            and _is_transaction_in_month(row, year, month)
            and _extension_days_in_month(row.get("period_from"), row.get("period_to"), year, month) > 0
        ],
        key=lambda row: (str(row.get("date") or ""), int(row.get("id") or 0)),
        reverse=True,
    )


def _transaction_service_prices(transactions: list[dict[str, Any]]) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {}
    for row in sorted(transactions, key=lambda item: (str(item.get("date") or ""), int(item.get("id") or 0))):
        service_id = str(row.get("service_id") or "").strip()
        price = _money(row.get("price"))
        if service_id and price > 0 and not _is_extension_transaction(row):
            prices[service_id] = price
    return prices


def _fetch_customer_map(config: dict[str, Any], customer_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not customer_ids:
        return {}

    found: dict[str, dict[str, Any]] = {}
    max_pages = _env_int("SUBSCRIBER_REPORT_CUSTOMER_MAP_MAX_PAGES", 8)
    limit = _page_limit()
    for page in range(max_pages):
        batch = _fetch_customers_page(config, page * limit, limit)
        if not batch:
            break
        for customer in batch:
            customer_id = str(customer.get("id") or "")
            if customer_id in customer_ids:
                found[customer_id] = customer
        if customer_ids.issubset(found.keys()):
            break
    return found


def _fallback_service_price(db: Session, customer_id: str, service_id: Any) -> Decimal:
    service_key = str(service_id or "").strip()
    if not service_key:
        return Decimal("0")
    try:
        services = selfcare.fetch_customer_internet_services(db, customer_id)
    except Exception:
        logger.warning("revenue_service_report_selfcare_price_lookup_failed customer_id=%s", customer_id)
        return Decimal("0")
    match = next((service for service in services if str(service.get("id") or "") == service_key), None)
    return _money((match or {}).get("unit_price"))


def _row_from_extension_transaction(
    *,
    db: Session,
    transaction: dict[str, Any],
    customer_map: dict[str, dict[str, Any]],
    service_prices: dict[str, Decimal],
    service_price_cache: dict[tuple[str, str], Decimal],
    uptime_map: dict[str, dict[str, Any] | None],
    year: int | None = None,
    month: int | None = None,
) -> dict[str, Any] | None:
    customer_id = str(transaction.get("customer_id") or "")
    if not customer_id:
        return None

    service_id = str(transaction.get("service_id") or "").strip()
    monthly_fee = service_prices.get(service_id, Decimal("0"))
    if service_id and monthly_fee <= 0:
        cache_key = (customer_id, service_id)
        if cache_key not in service_price_cache:
            service_price_cache[cache_key] = _fallback_service_price(db, customer_id, service_id)
        monthly_fee = service_price_cache[cache_key]

    days = _extension_days_in_month(transaction.get("period_from"), transaction.get("period_to"), year, month)
    selected_year, selected_month = _selected_month(year, month)
    days_in_month = monthrange(selected_year, selected_month)[1]
    hours_down = days * 24
    credit = (monthly_fee * Decimal(days) / Decimal(days_in_month)) if days_in_month else Decimal("0")
    estimated_uptime = max(Decimal("0"), (Decimal(days_in_month * 24 - hours_down) / Decimal(days_in_month * 24)) * 100)
    uptime_payload = uptime_map.get(customer_id)
    uptime = Decimal(str(uptime_payload["uptime_percent"])) if uptime_payload else estimated_uptime
    status = "resolved" if uptime >= Decimal("99.5") else "investigating" if uptime >= Decimal("99") else "critical"
    customer = customer_map.get(customer_id, {})

    return {
        "incident_id": transaction.get("id"),
        "customer_id": customer_id,
        "customer_name": customer.get("name") or f"Customer {customer_id}",
        "login": customer.get("login") or "",
        "date": transaction.get("date"),
        "hours_down": hours_down,
        "monthly_fee": float(monthly_fee),
        "credit_note_amount": float(credit),
        "uptime_percent": float(uptime),
        "uptime_source": uptime_payload["source"] if uptime_payload else "extension_estimate",
        "online_hours": uptime_payload["online_hours"] if uptime_payload else None,
        "offline_hours": uptime_payload["offline_hours"] if uptime_payload else None,
        "status": status,
        "root_cause": "Service extension",
        "service_id": service_id,
        "service_name": transaction.get("description") or "",
        "period_from": transaction.get("period_from"),
        "period_to": transaction.get("period_to"),
    }


def _build_rows_from_transactions(
    db: Session,
    transactions: list[dict[str, Any]],
    *,
    year: int | None = None,
    month: int | None = None,
    max_rows: int | None = None,
) -> list[dict[str, Any]]:
    extension_transactions = _month_extension_transactions(transactions, year=year, month=month)
    selected_transactions = extension_transactions if max_rows is None else extension_transactions[:max_rows]
    config = _config(db)
    customer_ids = {str(row.get("customer_id") or "") for row in selected_transactions if row.get("customer_id")}
    customer_map = _fetch_customer_map(config, customer_ids)
    selected_year, selected_month = _selected_month(year, month)
    uptime_map = _session_uptime_map_for_customers(config, customer_ids, selected_year, selected_month)
    service_prices = _transaction_service_prices(transactions)
    service_price_cache: dict[tuple[str, str], Decimal] = {}
    rows: list[dict[str, Any]] = []

    for transaction in selected_transactions:
        row = _row_from_extension_transaction(
            db=db,
            transaction=transaction,
            customer_map=customer_map,
            service_prices=service_prices,
            service_price_cache=service_price_cache,
            uptime_map=uptime_map,
            year=year,
            month=month,
        )
        if row:
            rows.append(row)
    return sorted(rows, key=lambda row: float(row.get("hours_down") or 0), reverse=True)


def _customer_matches(row: dict[str, Any], words: list[str]) -> bool:
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("id", "login", "name", "email", "billing_email", "phone", "street_1", "city")
    ).lower()
    return all(word in haystack for word in words)


def find_customer(db: Session, query: str) -> dict[str, Any] | None:
    """Find the customer in live Selfcare by name, login, email, phone, or customer ID."""
    search = str(query or "").strip()
    if not search:
        return None

    if search.isdigit():
        customer = selfcare.fetch_customer(db, search)
        if customer and customer.get("id"):
            return customer

    config = _config(db)
    words = [word for word in search.lower().split() if word]
    with contextlib.suppress(SelfcareReportError, selfcare.SelfcareProviderError):
        matches = selfcare.search_subscribers(db, search, limit=50)
        match = next((row for row in matches if _customer_matches(row, words)), None)
        if match:
            return match

    max_pages = _env_int("SUBSCRIBER_REPORT_CUSTOMER_SEARCH_MAX_PAGES", 8)
    limit = _page_limit()
    for page in range(max_pages):
        batch = _fetch_customers_page(config, page * limit, limit)
        if not batch:
            break
        match = next((row for row in batch if _customer_matches(row, words)), None)
        if match:
            return match
    return None


def _latest_extension_for_customer(db: Session, customer_id: Any) -> dict[str, Any] | None:
    customer_key = str(customer_id)
    matches = [
        transaction
        for transaction in _fetch_recent_transactions(db)
        if str(transaction.get("customer_id") or "") == customer_key and _is_extension_transaction(transaction)
    ]
    return (
        sorted(
            matches,
            key=lambda row: (str(row.get("date") or ""), int(row.get("id") or 0)),
            reverse=True,
        )[0]
        if matches
        else None
    )


def _compensation_payload(
    *,
    customer: dict[str, Any],
    services: list[dict[str, Any]],
    billing: dict[str, Any] | None,
    transaction: dict[str, Any],
) -> dict[str, Any]:
    service = _service_for_transaction(services, transaction) or {}
    service_price = _money(service.get("unit_price") or (billing or {}).get("month_price"))
    days = _extension_days(transaction.get("period_from"), transaction.get("period_to"))
    month_days = _days_in_billing_month(transaction.get("period_from"))
    estimated = (service_price * Decimal(days) / Decimal(month_days)) if month_days else Decimal("0")

    return {
        "customer_id": customer.get("id"),
        "customer_name": customer.get("name") or "",
        "login": customer.get("login") or "",
        "customer_status": customer.get("status") or "",
        "active_service_name": service.get("description") or "",
        "service_id_used": service.get("id") or transaction.get("service_id"),
        "service_unit_price_used": float(service_price),
        "latest_extension_transaction_id": transaction.get("id"),
        "latest_extension_date": transaction.get("date"),
        "period_from": transaction.get("period_from"),
        "period_to": transaction.get("period_to"),
        "extension_days": days,
        "estimated_compensation_value": float(estimated),
        "last_online": customer.get("last_online"),
        "billing_blocking_date": (billing or {}).get("blocking_date"),
    }


def lookup_compensation(db: Session, search: str) -> dict[str, Any]:
    customer = find_customer(db, search)
    if not customer:
        return {"found": False, "message": "Customer not found"}

    profile = selfcare.fetch_customer(db, str(customer.get("id"))) or customer
    services = selfcare.fetch_customer_internet_services(db, str(customer.get("id")))
    billing = selfcare.fetch_customer_billing(db, str(customer.get("id")))
    latest = _latest_extension_for_customer(db, customer.get("id"))
    if not latest:
        return {
            "found": True,
            "has_extension": False,
            "message": "No extension compensation found",
            "customer_id": profile.get("id"),
            "customer_name": profile.get("name") or "",
            "login": profile.get("login") or "",
            "customer_status": profile.get("status") or "",
            "last_online": profile.get("last_online"),
            "billing_blocking_date": (billing or {}).get("blocking_date"),
        }

    return {
        "found": True,
        "has_extension": True,
        **_compensation_payload(customer=profile, services=services, billing=billing, transaction=latest),
    }


def build_downtime_log(db: Session, *, year: int | None = None, month: int | None = None) -> list[dict[str, Any]]:
    max_rows = _env_int("DOWNTIME_LOG_MAX_ROWS", DEFAULT_LOG_MAX_ROWS)
    return _build_rows_from_transactions(db, _fetch_recent_transactions(db), year=year, month=month, max_rows=max_rows)


def build_summary_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary_rows = rows
    affected = {row["customer_id"] for row in summary_rows}
    total_hours = sum(float(row["hours_down"]) for row in summary_rows)
    total_credit = sum(float(row["credit_note_amount"]) for row in summary_rows)
    average_uptime = (
        sum(float(row["uptime_percent"]) for row in summary_rows) / len(summary_rows) if summary_rows else 100.0
    )

    root_cause_totals: dict[str, float] = {}
    customer_totals: dict[str, dict[str, Any]] = {}
    for row in summary_rows:
        root_cause = str(row["root_cause"])
        root_cause_totals[root_cause] = root_cause_totals.get(root_cause, 0.0) + float(row["hours_down"])
        customer_id = str(row["customer_id"])
        customer_totals.setdefault(
            customer_id,
            {
                "customer_id": customer_id,
                "customer_name": row["customer_name"],
                "hours_down": 0.0,
                "credit_exposure": 0.0,
            },
        )
        customer_totals[customer_id]["hours_down"] += float(row["hours_down"])
        customer_totals[customer_id]["credit_exposure"] += float(row["credit_note_amount"])

    return {
        "total_downtime_hours": total_hours,
        "incident_count": len(summary_rows),
        "affected_customers_count": len(affected),
        "total_credit_exposure": total_credit,
        "average_uptime_percent": average_uptime,
        "root_cause_totals": root_cause_totals,
        "top_affected_customers": sorted(
            customer_totals.values(),
            key=lambda row: row["hours_down"],
            reverse=True,
        )[:8],
    }


def build_summary(db: Session, *, year: int | None = None, month: int | None = None) -> dict[str, Any]:
    return build_summary_from_rows(
        _build_rows_from_transactions(db, _fetch_recent_transactions(db), year=year, month=month)
    )


REPORT_CACHE_TTL_SECONDS = 300
_REPORT_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}


def _build_report_uncached(db: Session, *, year: int | None = None, month: int | None = None) -> dict[str, Any]:
    transactions = _fetch_recent_transactions(db)
    summary_rows = _build_rows_from_transactions(db, transactions, year=year, month=month)
    max_rows = _env_int("DOWNTIME_LOG_MAX_ROWS", DEFAULT_LOG_MAX_ROWS)
    return {
        "summary": build_summary_from_rows(summary_rows),
        "downtime_log": summary_rows[:max_rows],
    }


def build_report(db: Session, *, year: int | None = None, month: int | None = None) -> dict[str, Any]:
    # Cache successful reports briefly. The report performs many external Selfcare
    # calls per request; without this it is rebuilt on every page load, which is
    # slow and risks idle-in-transaction timeouts (BUG-131).
    key = (year, month)
    now = time.monotonic()
    cached = _REPORT_CACHE.get(key)
    if cached and cached[0] > now:
        return cached[1]
    result = _build_report_uncached(db, year=year, month=month)
    _REPORT_CACHE[key] = (now + REPORT_CACHE_TTL_SECONDS, result)
    return result


def _payment_customer_groups(payments: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for payment in payments:
        customer_id = str(payment.get("customer_id") or "").strip()
        amount = _money(payment.get("amount"))
        if not customer_id or amount <= 0:
            continue
        groups.setdefault(customer_id, []).append(payment)
    for rows in groups.values():
        rows.sort(key=lambda row: (str(row.get("date") or ""), int(row.get("id") or 0)), reverse=True)
    return groups


def _payment_month(payment: dict[str, Any]) -> str | None:
    payment_date = _parse_date(payment.get("date"))
    if not payment_date:
        return None
    return f"{payment_date.year:04d}-{payment_date.month:02d}"


def _active_plan_for_customer(
    db: Session,
    customer_id: str,
    customer: dict[str, Any],
    *,
    fetch_remote: bool = True,
) -> tuple[str, Decimal]:
    profile_price = _money(customer.get("mrr_total") or customer.get("monthly_fee") or customer.get("month_price"))
    if profile_price > 0:
        return str(customer.get("tariff_name") or customer.get("plan") or "Active internet service"), profile_price

    if not fetch_remote:
        return str(customer.get("tariff_name") or customer.get("plan") or "Active internet service"), Decimal("0")

    services = selfcare.fetch_customer_internet_services(db, customer_id)
    service = _select_active_service(services) or {}
    price = _money(service.get("unit_price"))
    if price <= 0:
        billing = selfcare.fetch_customer_billing(db, customer_id)
        price = _money((billing or {}).get("month_price"))
    return str(service.get("description") or service.get("name") or ""), price


def _is_close_to_monthly(amount: Decimal, monthly_price: Decimal) -> bool:
    if monthly_price <= 0:
        return False
    ratio = amount / monthly_price
    return Decimal("1") - PAYMENT_TOLERANCE <= ratio <= Decimal("1") + PAYMENT_TOLERANCE


def _net_service_payment_amount(amount: Decimal, monthly_price: Decimal) -> Decimal:
    if amount <= 0 or monthly_price <= 0:
        return amount

    ratio = amount / monthly_price
    if Decimal("1") - PAYMENT_TOLERANCE <= ratio <= Decimal("1") + PAYMENT_TOLERANCE:
        return monthly_price

    vat_multiplier = Decimal("1") + _vat_rate()
    net_amount = amount / vat_multiplier if vat_multiplier > 0 else amount
    nearest_months = max(int((net_amount / monthly_price).to_integral_value()), 1)
    expected_net = monthly_price * Decimal(nearest_months)
    if expected_net > 0 and abs(net_amount - expected_net) / expected_net <= PAYMENT_TOLERANCE:
        return expected_net
    return net_amount


def _classify_payment_group(payments: list[dict[str, Any]], monthly_price: Decimal) -> tuple[str, str, Decimal]:
    latest = payments[0]
    latest_amount = _money(latest.get("amount"))
    months_covered = latest_amount / monthly_price if monthly_price > 0 else Decimal("0")

    if monthly_price <= 0:
        return ("Other payer", "Monthly service price was not available for this customer.", months_covered)

    if months_covered >= Decimal("2"):
        return (
            "Advance payer",
            f"Latest payment covers {months_covered:.2f} months based on service unit price.",
            months_covered,
        )

    near_months = {
        month
        for payment in payments
        if (month := _payment_month(payment)) and _is_close_to_monthly(_money(payment.get("amount")), monthly_price)
    }
    if len(near_months) >= 3:
        return (
            "Monthly recurring payer",
            f"Has payments close to monthly price across {len(near_months)} different months.",
            months_covered,
        )

    return (
        "Other payer",
        "Payment amount or timing does not match advance or monthly recurring patterns.",
        months_covered,
    )


def _payment_row(
    db: Session,
    *,
    customer_id: str,
    customer: dict[str, Any],
    payments: list[dict[str, Any]],
    current_month_payments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    active_plan, monthly_price = _active_plan_for_customer(db, customer_id, customer, fetch_remote=False)
    if not payments or not current_month_payments:
        return None

    classification, reason, months_covered = _classify_payment_group(payments, monthly_price)
    latest = current_month_payments[0]
    gross_cash_received = sum((_money(payment.get("amount")) for payment in current_month_payments), Decimal("0"))
    cash_received = sum(
        (
            _net_service_payment_amount(_money(payment.get("amount")), monthly_price)
            for payment in current_month_payments
        ),
        Decimal("0"),
    )
    recognised_revenue = min(cash_received, monthly_price) if monthly_price > 0 else Decimal("0")
    recent = [
        {
            "date": payment.get("date"),
            "amount": float(_money(payment.get("amount"))),
        }
        for payment in payments[:6]
    ]
    return {
        "customer_id": customer_id,
        "login": customer.get("login") or "",
        "customer_name": customer.get("name") or f"Customer {customer_id}",
        "active_plan": active_plan,
        "monthly_service_price": float(monthly_price),
        "latest_payment_date": latest.get("date"),
        "latest_payment_amount": float(_money(latest.get("amount"))),
        "cash_received": float(cash_received),
        "gross_cash_received": float(gross_cash_received),
        "recognised_revenue": float(recognised_revenue),
        "estimated_months_covered": float(months_covered),
        "recent_payment_dates_and_amounts": recent,
        "classification": classification,
        "classification_reason": reason,
    }


def build_payment_classification(
    db: Session,
    *,
    search: str = "",
    classification: str = "all",
    year: int | None = None,
    month: int | None = None,
) -> dict[str, Any]:
    payments = _fetch_recent_payments(db)
    groups = _payment_customer_groups(payments)
    selected_month_groups = _payment_customer_groups(
        [payment for payment in payments if _is_transaction_in_month(payment, year, month)]
    )
    sorted_selected_month_customer_ids = sorted(
        selected_month_groups,
        key=lambda customer_id: (
            str(selected_month_groups[customer_id][0].get("date") or ""),
            int(selected_month_groups[customer_id][0].get("id") or 0),
        ),
        reverse=True,
    )
    max_customers = _env_int("PAYMENT_CLASSIFICATION_MAX_CUSTOMERS", DEFAULT_PAYMENT_CLASSIFICATION_MAX_CUSTOMERS)
    selected_customer_ids = sorted_selected_month_customer_ids[:max_customers]
    config = _config(db)
    customer_map = _fetch_customer_map(config, set(selected_customer_ids))
    rows: list[dict[str, Any]] = []

    for customer_id in selected_customer_ids:
        row = _payment_row(
            db,
            customer_id=customer_id,
            customer=customer_map.get(customer_id, {"id": customer_id}),
            payments=groups[customer_id],
            current_month_payments=selected_month_groups[customer_id],
        )
        if row:
            rows.append(row)

    search_text = search.strip().lower()
    if search_text:
        words = [word for word in search_text.split() if word]
        rows = [
            row
            for row in rows
            if all(
                word
                in " ".join(
                    str(row.get(key) or "") for key in ("customer_id", "login", "customer_name", "active_plan")
                ).lower()
                for word in words
            )
        ]

    selected_classification = classification.strip().lower()
    if selected_classification and selected_classification != "all":
        rows = [row for row in rows if row["classification"].lower() == selected_classification]

    counts = {
        "Advance payer": 0,
        "Monthly recurring payer": 0,
        "Other payer": 0,
    }
    prepaid_months_total = Decimal("0")
    total_cash_received = Decimal("0")
    total_recognised_revenue = Decimal("0")
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
        total_cash_received += Decimal(str(row["cash_received"]))
        total_recognised_revenue += Decimal(str(row["recognised_revenue"]))
        if row["classification"] == "Advance payer":
            prepaid_months_total += Decimal(str(row["estimated_months_covered"]))

    return {
        "summary": {
            "total_classified_customers": len(rows),
            "advance_payers": counts["Advance payer"],
            "monthly_recurring_payers": counts["Monthly recurring payer"],
            "other_payers": counts["Other payer"],
            "estimated_prepaid_months_total": float(prepaid_months_total),
            "total_cash_received": float(total_cash_received),
            "total_recognised_revenue": float(total_recognised_revenue),
        },
        "rows": rows,
    }


def _month_label(year: int, month: int) -> str:
    return date(year, month, 1).strftime("%B %Y")


def _month_key(value: date) -> tuple[int, int]:
    return value.year, value.month


def _add_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _uptime_first_month(db: Session) -> date | None:
    from app.models.customer_uptime import CustomerUptimePeriod

    first_started_at = db.execute(select(func.min(CustomerUptimePeriod.started_at))).scalar_one_or_none()
    if isinstance(first_started_at, datetime):
        return first_started_at.date()
    return None


def build_month_options(db: Session) -> dict[str, Any]:
    """Return report month options from first available report data to the current month."""
    dates: list[date] = []
    with contextlib.suppress(SelfcareReportError):
        dates.extend(
            parsed
            for row in _fetch_recent_transactions(db)
            if _is_extension_transaction(row) and (parsed := _parse_date(row.get("date")))
        )
    with contextlib.suppress(SelfcareReportError):
        dates.extend(parsed for row in _fetch_recent_payments(db) if (parsed := _parse_date(row.get("date"))))
    uptime_start = _uptime_first_month(db)
    if uptime_start:
        dates.append(uptime_start)

    today = datetime.now(UTC).date()
    current_year, current_month = today.year, today.month
    first = min(dates) if dates else today
    first_year, first_month = _month_key(first)
    if (first_year, first_month) > (current_year, current_month):
        first_year, first_month = current_year, current_month

    options: list[dict[str, Any]] = []
    year, month = first_year, first_month
    while (year, month) <= (current_year, current_month):
        options.append(
            {
                "year": year,
                "month": month,
                "value": f"{year:04d}-{month:02d}",
                "label": _month_label(year, month),
                "is_current": year == current_year and month == current_month,
            }
        )
        year, month = _add_month(year, month)

    return {
        "current": {
            "year": current_year,
            "month": current_month,
            "value": f"{current_year:04d}-{current_month:02d}",
            "label": _month_label(current_year, current_month),
        },
        "months": options,
    }
