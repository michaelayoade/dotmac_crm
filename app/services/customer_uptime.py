"""Customer uptime polling and reporting backed by Selfcare online status."""

from __future__ import annotations

import contextlib
import os
import threading
from calendar import monthrange
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import get_logger
from app.models.customer_uptime import CustomerUptimePeriod, CustomerUptimeSnapshot
from app.models.subscriber import Subscriber, SubscriberStatus
from app.services import selfcare
from app.services.external_systems import EXTERNAL_SUBSCRIBER_SYSTEMS

logger = get_logger(__name__)

SOURCE_POLLING = "selfcare_polling"
CONFIDENCE_OBSERVED = "observed"
STATUS_ONLINE = "online"
STATUS_OFFLINE = "offline"
DEFAULT_POLL_INTERVAL_SECONDS = 300

_poller_thread: threading.Thread | None = None
_poller_stop = threading.Event()
_poller_lock = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    with contextlib.suppress(TypeError, ValueError):
        parsed = int(os.getenv(name, ""))
        if parsed >= 0:
            return parsed
    return default


def _parse_selfcare_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.startswith("0000"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        with contextlib.suppress(ValueError):
            parsed = datetime.strptime(text[:19], fmt)
            return parsed.replace(tzinfo=UTC)
    return None


def _int_or_none(value: Any) -> int | None:
    with contextlib.suppress(TypeError, ValueError):
        return int(value)
    return None


def _pair_key(customer_id: Any, service_id: Any) -> tuple[str, str | None]:
    customer_key = str(customer_id or "").strip()
    service_key = str(service_id or "").strip() or None
    return customer_key, service_key


def _online_map(rows: list[dict[str, Any]]) -> dict[tuple[str, str | None], dict[str, Any]]:
    online: dict[tuple[str, str | None], dict[str, Any]] = {}
    for row in rows:
        key = _pair_key(row.get("customer_id"), row.get("service_id"))
        if key[0]:
            online[key] = row
    return online


def _active_service_pairs(
    db: Session, online: dict[tuple[str, str | None], dict[str, Any]]
) -> dict[tuple[str, str | None], dict[str, Any]]:
    online_customer_ids = {key[0] for key in online}
    pairs: dict[tuple[str, str | None], dict[str, Any]] = {
        key: {
            "customer_id": key[0],
            "service_id": key[1],
            "login": row.get("login"),
            "raw_payload": row,
        }
        for key, row in online.items()
    }

    max_subscribers = _env_int("CUSTOMER_UPTIME_ACTIVE_SUBSCRIBER_LIMIT", 5000)
    stmt = (
        select(Subscriber)
        .where(
            Subscriber.external_system.in_(EXTERNAL_SUBSCRIBER_SYSTEMS),
            Subscriber.external_id.is_not(None),
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.active,
        )
        .order_by(desc(Subscriber.last_synced_at), desc(Subscriber.created_at))
        .limit(max_subscribers)
    )
    for subscriber in db.execute(stmt).scalars():
        if str(subscriber.external_id or "") in online_customer_ids:
            continue
        key = _pair_key(subscriber.external_id, None)
        if not key[0] or key in pairs:
            continue
        pairs[key] = {
            "customer_id": key[0],
            "service_id": key[1],
            "login": subscriber.subscriber_number,
            "raw_payload": {
                "subscriber_id": str(subscriber.id),
                "service_name": subscriber.service_name,
                "service_plan": subscriber.service_plan,
                "sync_metadata": subscriber.sync_metadata,
            },
        }
    return pairs


def _latest_open_period(
    db: Session,
    *,
    customer_id: str,
    service_id: str | None,
) -> CustomerUptimePeriod | None:
    stmt = select(CustomerUptimePeriod).where(
        CustomerUptimePeriod.customer_id == customer_id,
        CustomerUptimePeriod.ended_at.is_(None),
    )
    if service_id is None:
        stmt = stmt.where(CustomerUptimePeriod.service_id.is_(None))
    else:
        stmt = stmt.where(CustomerUptimePeriod.service_id == service_id)
    return db.execute(stmt.order_by(desc(CustomerUptimePeriod.started_at)).limit(1)).scalar_one_or_none()


def _record_snapshot(
    db: Session,
    *,
    observed_at: datetime,
    pair: dict[str, Any],
    is_online: bool,
    online_payload: dict[str, Any] | None,
) -> None:
    raw_payload = online_payload or pair.get("raw_payload") or {}
    db.add(
        CustomerUptimeSnapshot(
            customer_id=str(pair["customer_id"]),
            service_id=str(pair["service_id"]) if pair.get("service_id") else None,
            login=str(pair.get("login") or raw_payload.get("login") or "") or None,
            is_online=is_online,
            observed_at=observed_at,
            start_session=_parse_selfcare_datetime(raw_payload.get("start_session")),
            last_change=_parse_selfcare_datetime(raw_payload.get("last_change")),
            time_on=_int_or_none(raw_payload.get("time_on")),
            in_bytes=_int_or_none(raw_payload.get("in_bytes")),
            out_bytes=_int_or_none(raw_payload.get("out_bytes")),
            source=SOURCE_POLLING,
            raw_payload=raw_payload,
        )
    )


def _upsert_period(
    db: Session,
    *,
    observed_at: datetime,
    pair: dict[str, Any],
    status: str,
    raw_payload: dict[str, Any] | None,
) -> bool:
    customer_id = str(pair["customer_id"])
    service_id = str(pair["service_id"]) if pair.get("service_id") else None
    login = str(pair.get("login") or (raw_payload or {}).get("login") or "") or None
    current = _latest_open_period(db, customer_id=customer_id, service_id=service_id)
    if current is None:
        db.add(
            CustomerUptimePeriod(
                customer_id=customer_id,
                service_id=service_id,
                login=login,
                status=status,
                started_at=observed_at,
                source=SOURCE_POLLING,
                confidence=CONFIDENCE_OBSERVED,
                raw_payload=raw_payload,
            )
        )
        return True

    if current.status == status:
        return False

    current.ended_at = observed_at
    current.duration_seconds = max(int((observed_at - current.started_at).total_seconds()), 0)
    db.add(
        CustomerUptimePeriod(
            customer_id=customer_id,
            service_id=service_id,
            login=login or current.login,
            status=status,
            started_at=observed_at,
            source=SOURCE_POLLING,
            confidence=CONFIDENCE_OBSERVED,
            raw_payload=raw_payload,
        )
    )
    return True


def poll_splynx_uptime_once(db: Session, *, observed_at: datetime | None = None) -> dict[str, int]:
    observed_at = observed_at or datetime.now(UTC)
    online_rows = selfcare.fetch_online_customers(db)
    online = _online_map(online_rows)
    active_pairs = _active_service_pairs(db, online)

    snapshots = 0
    changes = 0
    online_count = 0
    offline_count = 0
    for key, pair in active_pairs.items():
        online_payload = online.get(key)
        is_online = online_payload is not None
        status = STATUS_ONLINE if is_online else STATUS_OFFLINE
        online_count += int(is_online)
        offline_count += int(not is_online)
        _record_snapshot(
            db,
            observed_at=observed_at,
            pair=pair,
            is_online=is_online,
            online_payload=online_payload,
        )
        snapshots += 1
        if _upsert_period(
            db,
            observed_at=observed_at,
            pair=pair,
            status=status,
            raw_payload=online_payload or pair.get("raw_payload"),
        ):
            changes += 1

    db.commit()
    return {
        "snapshots": snapshots,
        "online": online_count,
        "offline": offline_count,
        "period_changes": changes,
    }


def _poller_loop() -> None:
    interval = (
        _env_int("CUSTOMER_UPTIME_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
        or DEFAULT_POLL_INTERVAL_SECONDS
    )
    while not _poller_stop.is_set():
        db = SessionLocal()
        try:
            result = poll_splynx_uptime_once(db)
            logger.info("customer_uptime_poll_complete result=%s", result)
        except Exception as exc:
            db.rollback()
            logger.warning("customer_uptime_poll_failed error=%s", str(exc))
        finally:
            db.close()
        _poller_stop.wait(interval)


def start_uptime_poller() -> None:
    global _poller_thread
    if not _env_bool("CUSTOMER_UPTIME_POLLING_ENABLED", True):
        logger.info("customer_uptime_poller_disabled")
        return
    with _poller_lock:
        if _poller_thread and _poller_thread.is_alive():
            return
        _poller_stop.clear()
        _poller_thread = threading.Thread(target=_poller_loop, name="customer-uptime-poller", daemon=True)
        _poller_thread.start()
        logger.info("customer_uptime_poller_started")


def stop_uptime_poller() -> None:
    _poller_stop.set()


def _month_bounds(year: int | None = None, month: int | None = None) -> tuple[datetime, datetime]:
    today = datetime.now(UTC).date()
    selected_year = year or today.year
    selected_month = month or today.month
    start = datetime(selected_year, selected_month, 1, tzinfo=UTC)
    if selected_month == 12:
        end = datetime(selected_year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(selected_year, selected_month + 1, 1, tzinfo=UTC)
    return start, end


def _clip_seconds(period: CustomerUptimePeriod, start: datetime, end: datetime) -> int:
    period_end = period.ended_at or datetime.now(UTC)
    clipped_start = max(period.started_at, start)
    clipped_end = min(period_end, end)
    return max(int((clipped_end - clipped_start).total_seconds()), 0)


def _latest_snapshot_prices(db: Session, customer_ids: set[str]) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {}
    for customer_id in customer_ids:
        stmt = (
            select(CustomerUptimeSnapshot)
            .where(CustomerUptimeSnapshot.customer_id == customer_id)
            .order_by(desc(CustomerUptimeSnapshot.observed_at))
            .limit(1)
        )
        snapshot = db.execute(stmt).scalar_one_or_none()
        raw = snapshot.raw_payload if snapshot and isinstance(snapshot.raw_payload, dict) else {}
        with contextlib.suppress(Exception):
            prices[customer_id] = Decimal(str(raw.get("services_internet_unit_price") or raw.get("mrr_total") or "0"))
            continue
        price = Decimal("0")
        prices[customer_id] = price

    missing = {customer_id for customer_id, price in prices.items() if price <= 0}
    if missing:
        for customer in selfcare.fetch_customers(db):
            customer_id = str(customer.get("id") or "")
            if customer_id not in missing:
                continue
            with contextlib.suppress(Exception):
                prices[customer_id] = Decimal(str(customer.get("mrr_total") or "0"))
    return prices


def build_monthly_uptime_report(
    db: Session,
    *,
    year: int | None = None,
    month: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    month_start, month_end = _month_bounds(year, month)
    stmt = (
        select(CustomerUptimePeriod)
        .where(
            CustomerUptimePeriod.started_at < month_end,
            or_(CustomerUptimePeriod.ended_at.is_(None), CustomerUptimePeriod.ended_at > month_start),
        )
        .order_by(CustomerUptimePeriod.customer_id, CustomerUptimePeriod.service_id, CustomerUptimePeriod.started_at)
    )
    periods = list(db.execute(stmt).scalars())
    grouped: dict[tuple[str, str | None], dict[str, Any]] = {}
    for period in periods:
        key = (period.customer_id, period.service_id)
        row = grouped.setdefault(
            key,
            {
                "customer_id": period.customer_id,
                "service_id": period.service_id,
                "login": period.login,
                "online_seconds": 0,
                "offline_seconds": 0,
            },
        )
        seconds = _clip_seconds(period, month_start, month_end)
        if period.status == STATUS_ONLINE:
            row["online_seconds"] += seconds
        else:
            row["offline_seconds"] += seconds
        row["login"] = row.get("login") or period.login

    prices = _latest_snapshot_prices(db, {key[0] for key in grouped})
    rows: list[dict[str, Any]] = []
    for row in grouped.values():
        total_seconds = int(row["online_seconds"]) + int(row["offline_seconds"])
        if total_seconds <= 0:
            continue
        uptime_percent = int(row["online_seconds"]) / total_seconds * 100
        downtime_percent = int(row["offline_seconds"]) / total_seconds * 100
        monthly_price = prices.get(str(row["customer_id"]), Decimal("0"))
        service_received = monthly_price * Decimal(row["online_seconds"]) / Decimal(total_seconds)
        downtime_value = monthly_price * Decimal(row["offline_seconds"]) / Decimal(total_seconds)
        rows.append(
            {
                **row,
                "total_service_seconds": total_seconds,
                "uptime_percent": uptime_percent,
                "downtime_percent": downtime_percent,
                "service_unit_price": float(monthly_price),
                "service_received_value": float(service_received),
                "downtime_value": float(downtime_value),
                "confidence": CONFIDENCE_OBSERVED,
                "source": SOURCE_POLLING,
            }
        )

    rows.sort(key=lambda item: (float(item["downtime_percent"]), int(item["offline_seconds"])), reverse=True)
    total_online = sum(int(row["online_seconds"]) for row in rows)
    total_offline = sum(int(row["offline_seconds"]) for row in rows)
    total_service = total_online + total_offline
    uptime = total_online / total_service * 100 if total_service else 0
    return {
        "summary": {
            "tracked_services": len(rows),
            "average_uptime_percent": uptime,
            "online_hours": total_online / 3600,
            "offline_hours": total_offline / 3600,
            "service_received_value": sum(float(row["service_received_value"]) for row in rows),
            "downtime_value": sum(float(row["downtime_value"]) for row in rows),
            "month_days": monthrange(month_start.year, month_start.month)[1],
        },
        "rows": rows[:limit],
    }
