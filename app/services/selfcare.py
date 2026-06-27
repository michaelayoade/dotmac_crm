"""Selfcare integration helpers for customer creation."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.person import PartyStatus, Person
from app.models.subscriber import Subscriber, SubscriberStatus
from app.services import settings_spec
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)

DEFAULT_CUSTOMER_WEBHOOK_PATH = "/api/v1/webhooks/crm/customers"
_CUSTOMER_LAST_SYNC_KEY = "selfcare_sync:customer:last"
_CUSTOMER_HISTORY_KEY = "selfcare_sync:customer:history"
_CUSTOMER_DAILY_STATS_PREFIX = "selfcare_sync:customer:stats:"
_HISTORY_MAX_SIZE = 30

if TYPE_CHECKING:
    from redis import Redis

_redis_client: Redis | None = None


@dataclass(frozen=True)
class SelfcareCustomerIdentity:
    """Identity values returned by selfcare for a created customer."""

    selfcare_id: str | None
    subscriber_number: str

    @property
    def external_id(self) -> str:
        return self.selfcare_id or self.subscriber_number


class SelfcareProviderError(RuntimeError):
    """Raised when the Selfcare CRM API cannot serve a requested operation."""


RETENTION_DEACTIVATED_STATUS = "disabled"


def _get_redis() -> Redis | None:
    global _redis_client
    if _redis_client is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        try:
            import redis

            _redis_client = redis.from_url(redis_url, decode_responses=True)
            _redis_client.ping()
        except Exception as exc:
            logger.debug("selfcare_stats_redis_unavailable error=%s", exc)
            return None
    return _redis_client


def _today_stats_key() -> str:
    return f"{_CUSTOMER_DAILY_STATS_PREFIX}{datetime.now(UTC).strftime('%Y-%m-%d')}"


def _get_config(db: Session) -> dict[str, Any] | None:
    enabled = settings_spec.resolve_value(
        db, SettingDomain.integration, "selfcare_customer_sync_enabled", use_cache=False
    )
    if not enabled:
        return None

    base_url = settings_spec.resolve_value(db, SettingDomain.integration, "selfcare_base_url", use_cache=False)
    webhook_path = (
        settings_spec.resolve_value(db, SettingDomain.integration, "selfcare_customer_webhook_path", use_cache=False)
        or DEFAULT_CUSTOMER_WEBHOOK_PATH
    )
    webhook_secret = settings_spec.resolve_value(
        db, SettingDomain.integration, "selfcare_customer_webhook_secret", use_cache=False
    )
    timeout_value = (
        settings_spec.resolve_value(db, SettingDomain.integration, "selfcare_timeout_seconds", use_cache=False) or 30
    )

    if not base_url or not webhook_secret:
        logger.warning("selfcare_config_incomplete")
        return None

    try:
        timeout_seconds = int(timeout_value if isinstance(timeout_value, int) else str(timeout_value))
    except (TypeError, ValueError):
        timeout_seconds = 30

    return {
        "base_url": str(base_url).rstrip("/"),
        "webhook_path": str(webhook_path or DEFAULT_CUSTOMER_WEBHOOK_PATH),
        "webhook_secret": str(webhook_secret),
        "timeout_seconds": timeout_seconds,
    }


def _get_api_config(db: Session) -> dict[str, Any]:
    enabled = settings_spec.resolve_value(
        db, SettingDomain.integration, "selfcare_customer_sync_enabled", use_cache=False
    )
    if not enabled:
        raise SelfcareProviderError("Selfcare sync is disabled.")

    base_url = settings_spec.resolve_value(db, SettingDomain.integration, "selfcare_base_url", use_cache=False)
    api_token = settings_spec.resolve_value(db, SettingDomain.integration, "selfcare_api_token", use_cache=False)
    timeout_value = (
        settings_spec.resolve_value(db, SettingDomain.integration, "selfcare_timeout_seconds", use_cache=False) or 30
    )
    if not base_url:
        raise SelfcareProviderError("Selfcare base URL is not configured.")
    if not api_token:
        raise SelfcareProviderError("Selfcare API token is not configured.")
    try:
        timeout_seconds = int(timeout_value if isinstance(timeout_value, int) else str(timeout_value))
    except (TypeError, ValueError):
        timeout_seconds = 30
    return {
        "base_url": str(base_url).rstrip("/"),
        "api_token": str(api_token),
        "timeout_seconds": timeout_seconds,
    }


def _crm_url(config: dict[str, Any], path: str) -> str:
    normalized = path if path.startswith("/") else f"/{path}"
    if normalized.startswith("/api/v1/crm/") or normalized == "/api/v1/crm":
        return f"{config['base_url']}{normalized}"
    return f"{config['base_url']}/api/v1/crm{normalized}"


def _api_headers(config: dict[str, Any]) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['api_token']}",
    }


def _unwrap_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data")
    return payload


def _rows(payload: Any) -> list[dict[str, Any]]:
    data = _unwrap_data(payload)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("data", "items", "results", "rows"):
            nested = data.get(key)
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, dict)]
        return [data]
    return []


def _request_json(
    db: Session,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    config = _get_api_config(db)
    import requests

    url = _crm_url(config, path)
    timeout_seconds = int(config.get("timeout_seconds") or 30)
    request_started = time.monotonic()
    logger.info(
        "SELFCARE_API_REQUEST_START method=%s path=%s timeout=%s params=%s",
        method.upper(),
        path,
        timeout_seconds,
        params or {},
    )
    try:
        response = requests.request(  # nosec B113 - timeout is config-driven.
            method.upper(),
            url,
            headers=_api_headers(config),
            params=params or {},
            json=json_body,
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        duration = time.monotonic() - request_started
        logger.exception(
            "SELFCARE_API_REQUEST_ERROR method=%s path=%s duration=%.3fs error=%s",
            method.upper(),
            path,
            duration,
            exc,
        )
        raise SelfcareProviderError(f"Selfcare request failed for {url}: {exc}") from exc
    duration = time.monotonic() - request_started
    logger.info(
        "SELFCARE_API_REQUEST_COMPLETE method=%s path=%s status_code=%s duration=%.3fs",
        method.upper(),
        path,
        response.status_code,
        duration,
    )
    if response.status_code < 200 or response.status_code >= 300:
        body = response.text[:500]
        raise SelfcareProviderError(f"Selfcare request failed for {url}: HTTP {response.status_code} {body}")
    try:
        return response.json()
    except ValueError as exc:
        raise SelfcareProviderError(f"Selfcare returned invalid JSON for {url}") from exc


def ping(db: Session) -> bool:
    _request_json(db, "GET", "/ping")
    return True


def create_account_credit(
    db: Session,
    *,
    subscriber_id: str,
    amount: Any,
    reason: str = "Referral reward",
    external_ref: str | None = None,
    currency: str = "NGN",
) -> str:
    """Issue an account credit on a subscriber's dotmac_sub billing account
    (used to pay out referral rewards). Returns the new credit id.

    Raises ``SelfcareProviderError`` when sync is disabled or the call fails, so
    the caller can decide whether to mark the reward issued. Idempotent on
    ``external_ref`` server-side.
    """
    from decimal import Decimal, InvalidOperation

    try:
        amt = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SelfcareProviderError(f"Invalid credit amount: {amount!r}") from exc
    if amt <= 0:
        raise SelfcareProviderError("Credit amount must be greater than 0.")

    body = {
        "subscriber_id": str(subscriber_id),
        "amount": str(amt),
        "reason": reason,
        "external_ref": external_ref,
        "currency": currency,
    }
    data = _request_json(db, "POST", "/credits", json_body=body)
    row = _unwrap_data(data) or {}
    credit_id = str(row.get("id") or "").strip() if isinstance(row, dict) else ""
    if not credit_id:
        raise SelfcareProviderError("Credit response did not include an id.")
    return credit_id


def _list_paginated(db: Session, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    page = 1
    rows: list[dict[str, Any]] = []
    base_params = dict(params or {})
    max_pages = 10000
    while True:
        logger.info("SELFCARE_API_PAGE_START path=%s page=%d params=%s", path, page, base_params)
        payload = _request_json(db, "GET", path, params={**base_params, "page": page})
        batch = _rows(payload)
        rows.extend(batch)
        meta = payload.get("meta") if isinstance(payload, dict) else {}
        total = int((meta or {}).get("total") or 0)
        meta_page = (meta or {}).get("page") if isinstance(meta, dict) else None
        logger.info(
            "SELFCARE_API_PAGE_COMPLETE path=%s requested_page=%d meta_page=%s batch=%d accumulated=%d total=%d",
            path,
            page,
            meta_page,
            len(batch),
            len(rows),
            total,
        )
        if not batch:
            break
        if total and len(rows) >= total:
            break
        if len(batch) == 1 and not total:
            break
        if page >= max_pages:
            raise SelfcareProviderError(f"Selfcare pagination exceeded {max_pages} pages for {path}")
        page += 1
    logger.info("SELFCARE_API_PAGINATION_COMPLETE path=%s pages=%d rows=%d", path, page, len(rows))
    return rows


def fetch_customers(
    db: Session, *, include: str | None = "services,billing", per_page: int = 500
) -> list[dict[str, Any]]:
    """Fetch all Selfcare subscribers using the CRM API envelope."""
    params: dict[str, Any] = {"per_page": max(1, min(int(per_page or 500), 1000))}
    if include:
        params["include"] = include
    return _list_paginated(db, "/subscribers", params)


def fetch_customer(db: Session, subscriber_id: str) -> dict[str, Any] | None:
    payload = _request_json(db, "GET", f"/subscribers/{subscriber_id}")
    data = _unwrap_data(payload)
    return data if isinstance(data, dict) else None


def fetch_customer_internet_services(db: Session, subscriber_id: str) -> list[dict[str, Any]]:
    payload = _request_json(db, "GET", f"/subscribers/{subscriber_id}/services")
    return _rows(payload)


def fetch_customer_billing(db: Session, subscriber_id: str) -> dict[str, Any] | None:
    payload = _request_json(db, "GET", f"/subscribers/{subscriber_id}/billing")
    data = _unwrap_data(payload)
    return data if isinstance(data, dict) else None


def fetch_locations(db: Session) -> list[dict[str, Any]]:
    payload = _request_json(db, "GET", "/locations")
    return _rows(payload)


def fetch_billing_risk_source(db: Session) -> list[dict[str, Any]]:
    return _list_paginated(db, "/billing-risk-source")


def fetch_online_customers(db: Session) -> list[dict[str, Any]]:
    return _list_paginated(db, "/subscribers/online")


def fetch_transactions(db: Session, *, offset: int = 0, limit: int = 5000) -> list[dict[str, Any]]:
    return _rows(_request_json(db, "GET", "/finance/transactions", params={"offset": offset, "limit": limit}))


def fetch_payments(db: Session, *, offset: int = 0, limit: int = 5000) -> list[dict[str, Any]]:
    return _rows(_request_json(db, "GET", "/finance/payments", params={"offset": offset, "limit": limit}))


def fetch_customer_payments(
    db: Session,
    customer_id: str,
    *,
    page: int = 1,
    per_page: int = 1,
) -> list[dict[str, Any]]:
    return _rows(
        _request_json(
            db,
            "GET",
            "/finance/payments",
            params={"customer_id": customer_id, "page": page, "per_page": per_page},
        )
    )


def fetch_customer_sessions(db: Session, subscriber_id: str, *, limit: int = 10000) -> list[dict[str, Any]]:
    return _rows(_request_json(db, "GET", f"/subscribers/{subscriber_id}/sessions", params={"limit": limit}))


def search_subscribers(db: Session, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
    return _rows(_request_json(db, "GET", "/subscribers/search", params={"q": query, "limit": limit}))


def patch_subscriber_status(db: Session, subscriber_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = _unwrap_data(_request_json(db, "PATCH", f"/subscribers/{subscriber_id}/status", json_body=payload))
    return data if isinstance(data, dict) else {}


def _coalesce_str(*values: object) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text and text != "0000-00-00":
            return text
    return None


def _parse_selfcare_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text or text == "0000-00-00":
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19] if " " in text else text[:10], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _normalize_decimal_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return None


def _map_selfcare_status(status: str | int | None) -> str:
    normalized = str(status or "").strip().lower()
    status_map = {
        "active": SubscriberStatus.active.value,
        "online": SubscriberStatus.active.value,
        "blocked": SubscriberStatus.suspended.value,
        "suspended": SubscriberStatus.suspended.value,
        "nonpayment_suspended": SubscriberStatus.suspended.value,
        "disabled": SubscriberStatus.terminated.value,
        "terminated": SubscriberStatus.terminated.value,
        "inactive": SubscriberStatus.terminated.value,
        "new": SubscriberStatus.pending.value,
        "pending": SubscriberStatus.pending.value,
    }
    return status_map.get(normalized, SubscriberStatus.active.value)


def _status_rank(status: object) -> int:
    order = {"active": 0, "new": 1, "pending": 2, "blocked": 3, "suspended": 4, "disabled": 5}
    return order.get(str(status or "").lower().strip(), 9)


def _select_primary_service(services: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not services:
        return None

    def sort_key(service: dict[str, Any]):
        start = _parse_selfcare_datetime(service.get("start_date") or service.get("activated_at"))
        end = _parse_selfcare_datetime(service.get("end_date") or service.get("terminated_at"))
        service_id = int(service.get("id") or 0) if str(service.get("id") or "").isdigit() else 0
        return (
            _status_rank(service.get("status")),
            -(start.timestamp() if start else 0),
            -(end.timestamp() if end else 0),
            -service_id,
        )

    return sorted(services, key=sort_key)[0]


_SPEED_PAIR_RE = re.compile(r"(?P<down>\d+(?:\.\d+)?)\s*[/xX]\s*(?P<up>\d+(?:\.\d+)?)")
_SINGLE_SPEED_RE = re.compile(r"(?P<speed>\d+(?:\.\d+)?)\s*(?:mbps|mb|m)", re.IGNORECASE)


def _extract_speed(source: dict[str, Any], description: str | None) -> str | None:
    down = _coalesce_str(source.get("speed_download"), source.get("download_speed"), source.get("download_mbps"))
    up = _coalesce_str(source.get("speed_upload"), source.get("upload_speed"), source.get("upload_mbps"))
    if down and up:
        return f"{down}/{up} Mbps"
    if down:
        return f"{down} Mbps"
    text = description or ""
    pair = _SPEED_PAIR_RE.search(text)
    if pair:
        return f"{pair.group('down')}/{pair.group('up')} Mbps"
    single = _SINGLE_SPEED_RE.search(text)
    return f"{single.group('speed')} Mbps" if single else None


def customer_base_station(customer: dict[str, Any] | None) -> str:
    if not isinstance(customer, dict):
        return ""
    raw_attrs = customer.get("metadata")
    attrs: dict[str, Any] = raw_attrs if isinstance(raw_attrs, dict) else {}
    return str(
        customer.get("base_station")
        or customer.get("base_station_name")
        or customer.get("router_name")
        or customer.get("nas_name")
        or attrs.get("base_station")
        or attrs.get("nas_name")
        or ""
    ).strip()


def map_customer_to_subscriber_data(
    db: Session,
    customer: dict[str, Any],
    *,
    include_remote_details: bool = True,
    existing_sync_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map a Selfcare subscriber payload into local Subscriber sync data."""
    external_id = str(customer.get("id") or customer.get("uuid") or customer.get("subscriber_id") or "").strip()
    services = (
        [row for row in customer.get("services", []) if isinstance(row, dict)]
        if isinstance(customer.get("services"), list)
        else []
    )
    billing = customer.get("billing") if isinstance(customer.get("billing"), dict) else None
    if include_remote_details and external_id and (not services or billing is None):
        if not services:
            services = fetch_customer_internet_services(db, external_id)
        if billing is None:
            billing = fetch_customer_billing(db, external_id)

    primary_service = _select_primary_service(services)
    description = _coalesce_str(
        customer.get("service_plan"),
        customer.get("tariff_name"),
        customer.get("plan"),
        primary_service.get("description") if primary_service else None,
        primary_service.get("name") if primary_service else None,
    )
    status_value = _map_selfcare_status(customer.get("status"))
    balance = _normalize_decimal_str(
        customer.get("balance")
        if customer.get("balance") is not None
        else (billing or {}).get("balance")
        if billing
        else None
    )
    next_bill_date = _parse_selfcare_datetime(
        customer.get("next_bill_date")
        or customer.get("next_billing_date")
        or (billing or {}).get("next_bill_date")
        or (billing or {}).get("next_billing_date")
        or (primary_service or {}).get("end_date")
    )
    metadata = dict(existing_sync_metadata or {})
    selfcare_metadata = {
        "selfcare_id": external_id or None,
        "selfcare_subscriber_number": _coalesce_str(
            customer.get("subscriber_number"), customer.get("login"), customer.get("account_number")
        ),
        "invoiced_until": _coalesce_str(customer.get("invoiced_until"), (billing or {}).get("invoiced_until")),
        "total_paid": _normalize_decimal_str(customer.get("total_paid") or (billing or {}).get("total_paid")),
        "last_transaction_date": _coalesce_str(
            customer.get("last_payment_date"),
            customer.get("last_transaction_date"),
            (billing or {}).get("last_payment_date"),
            (billing or {}).get("last_transaction_date"),
        ),
        "last_payment_amount": _normalize_decimal_str(
            customer.get("last_payment_amount") or (billing or {}).get("last_payment_amount")
        ),
        "blocked_date": _coalesce_str(customer.get("blocked_date"), (billing or {}).get("blocked_date")),
        "source": "selfcare",
    }
    metadata.update({key: value for key, value in selfcare_metadata.items() if value is not None and value != ""})

    data: dict[str, Any] = {
        "subscriber_number": _coalesce_str(customer.get("subscriber_number"), customer.get("login")),
        "account_number": _coalesce_str(customer.get("account_number")),
        "status": status_value,
        "service_name": description,
        "service_plan": description,
        "service_speed": _extract_speed(primary_service or customer, description),
        "balance": balance,
        "currency": _coalesce_str(customer.get("currency"), customer.get("currency_code")) or "NGN",
        "service_address_line1": _coalesce_str(
            customer.get("street"), customer.get("street_1"), customer.get("address_line1")
        ),
        "service_address_line2": _coalesce_str(customer.get("address_line2")),
        "service_city": _coalesce_str(customer.get("city"), customer.get("service_city")),
        "service_region": _coalesce_str(
            customer.get("state"),
            customer.get("region"),
            customer.get("service_region"),
            customer_base_station(customer),
        ),
        "service_postal_code": _coalesce_str(customer.get("zip"), customer.get("postal_code")),
        "service_country_code": _coalesce_str(customer.get("country_code")),
        "next_bill_date": next_bill_date,
        "activated_at": _parse_selfcare_datetime(
            customer.get("activated_at") or customer.get("created_at") or (primary_service or {}).get("start_date")
        ),
        "suspended_at": _parse_selfcare_datetime(
            customer.get("suspended_at") or customer.get("blocked_date") or (billing or {}).get("blocked_date")
        ),
        "terminated_at": _parse_selfcare_datetime(customer.get("terminated_at")),
        "last_synced_at": datetime.now(UTC),
        "sync_metadata": metadata,
    }
    return {key: value for key, value in data.items() if value is not None and value != ""}


def sync_subscribers_from_selfcare_data(
    db: Session,
    *,
    include_remote_details: bool = False,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Pull Selfcare subscribers and upsert local Subscriber records."""
    sync_logger = logger or globals()["logger"]
    from app.services.subscriber import subscriber as subscriber_service

    sync_logger.info("SELFCARE_SYNC_STEP step=ping_selfcare")
    ping(db)
    sync_logger.info("SELFCARE_SYNC_STEP_COMPLETE step=ping_selfcare")

    sync_logger.info("SELFCARE_SYNC_STEP step=fetch_subscribers include=services,billing")
    customers_data = fetch_customers(db, include="services,billing")
    if not isinstance(customers_data, list):
        raise TypeError(f"Selfcare subscribers response must be a list, got {type(customers_data).__name__}")
    sync_logger.info("SELFCARE_SYNC_STEP_COMPLETE step=fetch_subscribers count=%d", len(customers_data))

    results: dict[str, Any] = {"created": 0, "updated": 0, "errors": []}
    if not customers_data:
        sync_logger.info("selfcare_sync_no_data")
        return results

    person_by_email: dict[str, Person] = {}
    all_emails = [
        str(customer.get("email") or "").lower().strip()
        for customer in customers_data
        if isinstance(customer, dict) and customer.get("email")
    ]
    if all_emails:
        persons = db.query(Person).filter(Person.email.in_(all_emails)).all()
        person_by_email = {person.email.lower(): person for person in persons if person.email}
    sync_logger.info(
        "SELFCARE_SYNC_STEP_COMPLETE step=build_person_email_index emails=%d matched_people=%d",
        len(all_emails),
        len(person_by_email),
    )

    for index, customer in enumerate(customers_data, start=1):
        external_id = ""
        try:
            if not isinstance(customer, dict):
                raise TypeError(f"Selfcare subscriber row must be a dict, got {type(customer).__name__}")
            external_id = str(customer.get("id") or customer.get("uuid") or customer.get("subscriber_id") or "").strip()
            sync_logger.info(
                "SELFCARE_SYNC_ITEM_START index=%d count=%d external_id=%s subscriber_number=%s",
                index,
                len(customers_data),
                external_id or "<missing>",
                customer.get("subscriber_number") or customer.get("login") or "",
            )
            if not external_id:
                raise ValueError("missing selfcare subscriber id")

            subscriber_number = str(customer.get("subscriber_number") or customer.get("login") or "").strip()
            existing_by_number = (
                subscriber_service.get_by_subscriber_number(db, subscriber_number) if subscriber_number else None
            )
            existing_by_external_id = subscriber_service.get_by_external_id(db, "selfcare", external_id)
            existing = existing_by_number or existing_by_external_id
            if existing_by_number is not None:
                if existing_by_external_id is not None and existing_by_external_id.id != existing_by_number.id:
                    sync_logger.warning(
                        "SELFCARE_SYNC_DUPLICATE_EXTERNAL_MATCH index=%d external_id=%s "
                        "subscriber_number=%s subscriber_number_match_id=%s external_match_id=%s",
                        index,
                        external_id,
                        subscriber_number,
                        existing_by_number.id,
                        existing_by_external_id.id,
                    )
                if existing_by_external_id is None or existing_by_external_id.id != existing_by_number.id:
                    sync_logger.info(
                        "SELFCARE_SYNC_MATCH_BY_SUBSCRIBER_NUMBER index=%d external_id=%s subscriber_id=%s "
                        "subscriber_number=%s previous_external_system=%s previous_external_id=%s",
                        index,
                        external_id,
                        existing_by_number.id,
                        subscriber_number,
                        existing_by_number.external_system,
                        existing_by_number.external_id,
                    )

            data = map_customer_to_subscriber_data(
                db,
                customer,
                include_remote_details=include_remote_details,
                existing_sync_metadata=dict(existing.sync_metadata or {}) if existing else None,
            )

            email = str(customer.get("email") or "").lower().strip()
            if email and email in person_by_email:
                person = person_by_email[email]
                data["person_id"] = person.id
                data["organization_id"] = person.organization_id

            if existing:
                subscriber_service.update(
                    db,
                    existing,
                    {
                        "external_system": "selfcare",
                        "external_id": external_id,
                        "sync_error": None,
                        **data,
                    },
                )
                results["updated"] += 1
            else:
                subscriber_service.sync_from_external(db, "selfcare", external_id, data)
                results["created"] += 1
            sync_logger.info(
                "SELFCARE_SYNC_ITEM_COMPLETE index=%d external_id=%s action=%s created=%d updated=%d errors=%d",
                index,
                external_id,
                "updated" if existing else "created",
                results["created"],
                results["updated"],
                len(results["errors"]),
            )
        except Exception as exc:
            db.rollback()
            raw_customer_id = customer.get("id") if isinstance(customer, dict) else ""
            results["errors"].append({"external_id": external_id or raw_customer_id, "error": str(exc)})
            sync_logger.exception(
                "SELFCARE_SYNC_ITEM_ERROR index=%d external_id=%s error=%s",
                index,
                external_id or raw_customer_id,
                exc,
            )

    return results


def deactivate_customer_if_blocked(
    db: Session,
    *,
    customer_id: str,
    engagement_id: str,
    subscriber_id: str | None = None,
) -> dict[str, Any]:
    """Disable a Selfcare subscriber after verifying a suspended/blocked status."""
    selfcare_id = str(customer_id or "").strip()
    result: dict[str, Any] = {
        "customer_id": selfcare_id,
        "subscriber_id": str(subscriber_id or "").strip() or None,
        "selfcare_id": selfcare_id,
        "engagement_id": str(engagement_id or "").strip(),
        "success": False,
        "skipped": False,
    }
    subscriber = None
    if subscriber_id:
        with contextlib.suppress(ValueError):
            subscriber = db.get(Subscriber, coerce_uuid(subscriber_id))
    if subscriber is None and selfcare_id:
        subscriber = (
            db.query(Subscriber)
            .filter(Subscriber.external_system == "selfcare")
            .filter(Subscriber.external_id == selfcare_id)
            .first()
        )
    if subscriber is not None:
        result["subscriber_id"] = str(subscriber.id)

    try:
        customer = fetch_customer(db, selfcare_id)
    except SelfcareProviderError as exc:
        if subscriber is not None:
            subscriber.sync_error = str(exc)[:500]
            db.add(subscriber)
            db.commit()
        result.update({"error": str(exc)})
        return result

    if not customer:
        result.update({"error": "selfcare_subscriber_not_found"})
        return result
    previous_status = str(customer.get("status") or "").strip().lower()
    if previous_status in {"disabled", "terminated"}:
        result.update(
            {
                "success": True,
                "skipped": True,
                "reason": "selfcare_already_deactivated",
                "previous_status": previous_status,
            }
        )
        if subscriber is not None and subscriber.status != SubscriberStatus.terminated:
            subscriber.status = SubscriberStatus.terminated
            subscriber.terminated_at = subscriber.terminated_at or datetime.now(UTC)
            subscriber.sync_error = None
            db.add(subscriber)
            db.commit()
        return result
    if previous_status not in {"blocked", "suspended", "nonpayment_suspended"}:
        result.update({"skipped": True, "reason": "selfcare_status_not_suspended", "previous_status": previous_status})
        return result

    try:
        patch_subscriber_status(
            db,
            selfcare_id,
            {"status": RETENTION_DEACTIVATED_STATUS, "reason": "retention_lost", "source": "crm"},
        )
    except SelfcareProviderError as exc:
        if subscriber is not None:
            subscriber.sync_error = str(exc)[:500]
            db.add(subscriber)
            db.commit()
        result.update({"error": str(exc), "previous_status": previous_status})
        return result

    if subscriber is not None:
        metadata = dict(subscriber.sync_metadata or {})
        marker = dict(metadata.get("retention_selfcare_deactivation") or {})
        marker.update({"engagement_id": str(engagement_id), "selfcare_id": selfcare_id, "status": "success"})
        metadata["retention_selfcare_deactivation"] = marker
        subscriber.sync_metadata = metadata
        subscriber.status = SubscriberStatus.terminated
        subscriber.terminated_at = subscriber.terminated_at or datetime.now(UTC)
        subscriber.sync_error = None
        db.add(subscriber)
        db.commit()
    result.update({"success": True, "previous_status": previous_status, "new_status": RETENTION_DEACTIVATED_STATUS})
    return result


def _split_name(person: Person) -> tuple[str, str]:
    first_name = (person.first_name or "").strip()
    last_name = (person.last_name or "").strip()
    if first_name and last_name:
        return first_name, last_name

    display_name = (person.display_name or "").strip()
    if display_name:
        parts = display_name.split(maxsplit=1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return parts[0], "Customer"

    return first_name or "Customer", last_name or "Customer"


def build_customer_payload(
    person: Person,
    *,
    project_id: str | None = None,
    quote_id: str | None = None,
    sales_order_id: str | None = None,
) -> dict[str, Any]:
    """Build the selfcare subscriber-create webhook payload."""
    first_name, last_name = _split_name(person)
    metadata = {
        "source": "dotmac_omni",
        "crm_person_id": str(person.id),
        "crm_project_id": project_id,
        "crm_quote_id": quote_id,
        "crm_sales_order_id": sales_order_id,
        "synced_at": datetime.now(UTC).isoformat(),
    }
    return {
        "crm_person_id": str(person.id),
        "crm_project_id": project_id,
        "crm_quote_id": quote_id,
        "crm_sales_order_id": sales_order_id,
        "first_name": first_name,
        "last_name": last_name,
        "display_name": person.display_name or f"{first_name} {last_name}".strip(),
        "email": person.email,
        "phone": person.phone or "",
        "address_line1": person.address_line1 or "",
        "address_line2": person.address_line2 or "",
        "city": person.city or "",
        "region": person.region or "",
        "postal_code": person.postal_code or "",
        "country_code": person.country_code or "",
        "status": "new",
        "metadata": metadata,
    }


def _sign_payload(secret: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def _customer_url(config: dict[str, Any]) -> str:
    path = str(config["webhook_path"])
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{config['base_url']}{path}"


def create_customer(
    db: Session,
    person: Person,
    *,
    project_id: str | None = None,
    quote_id: str | None = None,
    sales_order_id: str | None = None,
) -> SelfcareCustomerIdentity | None:
    """Create or reuse a subscriber/customer in selfcare and return its ID."""
    config = _get_config(db)
    if not config:
        return None

    payload = build_customer_payload(
        person,
        project_id=project_id,
        quote_id=quote_id,
        sales_order_id=sales_order_id,
    )
    raw_body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": "customer.accepted",
        "X-Webhook-Signature-256": _sign_payload(config["webhook_secret"], raw_body),
    }

    import requests

    try:
        response = requests.post(  # nosec B113 - timeout is config-driven.
            _customer_url(config),
            data=raw_body,
            headers=headers,
            timeout=config["timeout_seconds"],
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.error("selfcare_create_customer_failed error=%s", str(exc))
        return None

    identity = _parse_customer_identity(data)
    if not identity:
        logger.error("selfcare_create_customer_no_id response=%s", data)
        return None
    return identity


def _parse_customer_identity(data: dict[str, Any]) -> SelfcareCustomerIdentity | None:
    selfcare_id = str(data.get("id") or "").strip() or None
    subscriber_number = str(data.get("subscriber_id") or data.get("subscriber_number") or "").strip()
    if not subscriber_number and selfcare_id:
        subscriber_number = selfcare_id
    if not subscriber_number:
        return None
    return SelfcareCustomerIdentity(selfcare_id=selfcare_id, subscriber_number=subscriber_number)


def record_customer_sync_result(
    *,
    success: bool,
    mode: str,
    person_id: str,
    selfcare_subscriber_id: str | None = None,
    selfcare_id: str | None = None,
    project_id: str | None = None,
    quote_id: str | None = None,
    sales_order_id: str | None = None,
    action: str = "created",
    error: str | None = None,
) -> None:
    """Record selfcare customer sync status for admin UI history."""
    redis = _get_redis()
    if not redis:
        return

    try:
        now_iso = datetime.now(UTC).isoformat()
        entry = {
            "timestamp": now_iso,
            "mode": mode,
            "person_id": person_id,
            "selfcare_id": selfcare_id,
            "selfcare_subscriber_id": selfcare_subscriber_id,
            "project_id": project_id,
            "quote_id": quote_id,
            "sales_order_id": sales_order_id,
            "action": action,
            "success": success,
            "error": error,
        }
        pipe = redis.pipeline()
        stats_key = _today_stats_key()
        pipe.hincrby(stats_key, "successes" if success else "errors", 1)
        pipe.hincrby(stats_key, "sync_count", 1)
        pipe.expire(stats_key, 7 * 24 * 60 * 60)
        pipe.set(_CUSTOMER_LAST_SYNC_KEY, json.dumps(entry))
        pipe.lpush(_CUSTOMER_HISTORY_KEY, json.dumps(entry))
        pipe.ltrim(_CUSTOMER_HISTORY_KEY, 0, _HISTORY_MAX_SIZE - 1)
        pipe.execute()
    except Exception as exc:
        logger.warning("selfcare_customer_sync_stats_failed error=%s", exc)


def get_customer_sync_history(limit: int = 10) -> list[dict[str, Any]]:
    """Return recent selfcare customer sync history, most recent first."""
    redis = _get_redis()
    if not redis:
        return []
    try:
        entries = cast(list[str], redis.lrange(_CUSTOMER_HISTORY_KEY, 0, limit - 1))
        return [json.loads(entry) for entry in entries]
    except Exception as exc:
        logger.warning("selfcare_customer_sync_history_failed error=%s", exc)
        return []


def get_last_customer_sync() -> dict[str, Any] | None:
    """Return the most recent selfcare customer sync result."""
    redis = _get_redis()
    if not redis:
        return None
    try:
        data = cast(str | None, redis.get(_CUSTOMER_LAST_SYNC_KEY))
        return json.loads(data) if data else None
    except Exception as exc:
        logger.warning("selfcare_customer_last_sync_failed error=%s", exc)
        return None


def get_customer_daily_stats() -> dict[str, int]:
    """Return today's selfcare customer sync counters."""
    redis = _get_redis()
    if not redis:
        return {"successes": 0, "errors": 0, "sync_count": 0}
    try:
        stats = cast(dict[str, str], redis.hgetall(_today_stats_key()))
        return {
            "successes": int(stats.get("successes", 0)),
            "errors": int(stats.get("errors", 0)),
            "sync_count": int(stats.get("sync_count", 0)),
        }
    except Exception as exc:
        logger.warning("selfcare_customer_daily_stats_failed error=%s", exc)
        return {"successes": 0, "errors": 0, "sync_count": 0}


def ensure_person_customer(
    db: Session,
    person: Person,
    identity: SelfcareCustomerIdentity | str | None,
) -> None:
    """Persist selfcare subscriber ID and mark the party as a customer."""
    if identity:
        if person.metadata_ is None or not isinstance(person.metadata_, dict):
            person.metadata_ = {}
        if isinstance(identity, SelfcareCustomerIdentity):
            if identity.selfcare_id:
                person.metadata_["selfcare_id"] = identity.selfcare_id
            person.metadata_["selfcare_subscriber_id"] = identity.subscriber_number
        else:
            person.metadata_["selfcare_subscriber_id"] = str(identity)

    if person.party_status in {PartyStatus.lead, PartyStatus.contact}:
        person.party_status = PartyStatus.customer

    db.add(person)
    db.commit()
    db.refresh(person)
