"""Splynx integration helpers (customer creation)."""

from __future__ import annotations

import contextlib
import logging
import re
from calendar import monthrange
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.person import PartyStatus, Person
from app.models.subscriber import SubscriberStatus
from app.services import settings_spec

logger = logging.getLogger(__name__)


def _get_config(db: Session) -> dict[str, Any] | None:
    enabled = settings_spec.resolve_value(
        db, SettingDomain.integration, "splynx_customer_sync_enabled", use_cache=False
    )
    if not enabled:
        return None

    auth_type = (
        settings_spec.resolve_value(db, SettingDomain.integration, "splynx_auth_type", use_cache=False) or "basic"
    )
    base_url = settings_spec.resolve_value(db, SettingDomain.integration, "splynx_base_url", use_cache=False)
    customer_url = settings_spec.resolve_value(db, SettingDomain.integration, "splynx_customer_url", use_cache=False)
    invoice_url = settings_spec.resolve_value(db, SettingDomain.integration, "splynx_invoice_url", use_cache=False)
    basic_token = settings_spec.resolve_value(db, SettingDomain.integration, "splynx_basic_auth_token", use_cache=False)
    timeout_value = (
        settings_spec.resolve_value(db, SettingDomain.integration, "splynx_timeout_seconds", use_cache=False) or 30
    )

    if not base_url:
        logger.warning("splynx_config_incomplete")
        return None
    if auth_type == "basic" and not basic_token:
        logger.warning("splynx_config_incomplete")
        return None

    if isinstance(timeout_value, int | str):
        timeout_seconds = int(timeout_value)
    else:
        timeout_seconds = 30

    return {
        "auth_type": str(auth_type),
        "base_url": str(base_url).rstrip("/"),
        "customer_url": str(customer_url).rstrip("/") if customer_url else None,
        "invoice_url": str(invoice_url).rstrip("/") if invoice_url else None,
        "basic_token": str(basic_token) if basic_token else None,
        "timeout_seconds": timeout_seconds,
    }


def _build_customer_payload(person: Person) -> dict[str, Any]:
    name = person.display_name or f"{person.first_name} {person.last_name}".strip()
    return {
        "name": name or "Customer",
        "email": person.email or "",
        "phone": person.phone or "",
        "street_1": person.address_line1 or "",
        "city": person.city or "",
        "status": "new",
    }


def create_customer(db: Session, person: Person) -> str | None:
    """Create a customer in Splynx and return the Splynx ID."""
    config = _get_config(db)
    if not config:
        return None

    headers = {"Content-Type": "application/json"}
    headers["Authorization"] = f"Basic {config['basic_token']}"

    import requests

    payload = _build_customer_payload(person)
    url = _resolve_customer_url(config)
    try:
        response = requests.post(  # nosec B113 — timeout via config dict
            url,
            json=payload,
            headers=headers,
            timeout=config["timeout_seconds"],
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.error("splynx_create_failed error=%s", str(exc))
        return None

    splynx_id = data.get("id")
    if not splynx_id:
        logger.error("splynx_create_no_id response=%s", data)
        return None
    return str(splynx_id)


def create_installation_invoice(
    db: Session,
    *,
    splynx_id: str,
    amount: Decimal,
    description: str,
    external_ref: str | None = None,
) -> str | None:
    """Create a one-time Splynx invoice for installation cost and return invoice ID."""
    config = _get_config(db)
    if not config:
        return None

    normalized_amount = _safe_decimal(amount)
    if normalized_amount is None or normalized_amount <= 0:
        logger.info("splynx_invoice_skip_invalid_amount splynx_id=%s amount=%s", splynx_id, amount)
        return None

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {config['basic_token']}",
    }
    base_payload = _build_installation_invoice_payload(
        splynx_id=splynx_id,
        amount=normalized_amount,
        description=description,
        external_ref=external_ref,
    )

    import requests

    for url in _resolve_invoice_urls(config):
        try:
            response = requests.post(  # nosec B113 — timeout via config dict
                url,
                json=base_payload,
                headers=headers,
                timeout=config["timeout_seconds"],
            )
            response.raise_for_status()
            payload = response.json()
            invoice_id = payload.get("id") if isinstance(payload, dict) else None
            if invoice_id is None:
                logger.warning("splynx_invoice_create_no_id splynx_id=%s response=%s", splynx_id, payload)
                return None
            return str(invoice_id)
        except Exception as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            response_text = None
            if response is not None:
                with contextlib.suppress(Exception):
                    response_text = response.text
            logger.warning(
                "splynx_invoice_create_failed splynx_id=%s url=%s status=%s error=%s response=%s",
                splynx_id,
                url,
                status_code,
                str(exc),
                response_text,
            )
    return None


def ensure_person_customer(db: Session, person: Person, splynx_id: str | None) -> None:
    """Persist Splynx ID and upgrade contact type to customer when applicable."""
    if splynx_id:
        if person.metadata_ is None or not isinstance(person.metadata_, dict):
            person.metadata_ = {}
        person.metadata_["splynx_id"] = splynx_id

    if person.party_status in {PartyStatus.lead, PartyStatus.contact}:
        person.party_status = PartyStatus.customer

    db.add(person)
    db.commit()
    db.refresh(person)


def test_connection(db: Session) -> tuple[bool, str]:
    """Validate configured Splynx credentials."""
    config = _get_config(db)
    if not config:
        return False, "Splynx settings are incomplete. Fill in base URL, API key, and API secret."

    import requests

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {config['basic_token']}",
    }
    url = _resolve_customer_url(config)
    try:
        response = requests.get(  # nosec B113 — timeout via config dict
            url,
            headers=headers,
            timeout=config["timeout_seconds"],
            params={"limit": 1},
        )
        response.raise_for_status()
    except Exception as exc:
        logger.error("splynx_auth_failed error=%s", str(exc))
        return False, "Authentication failed. Verify Basic auth credentials and permissions."
    return True, "Authentication succeeded. Credentials are valid for API access."


def fetch_customers(db: Session) -> list[dict[str, Any]]:
    """Fetch all customers from Splynx using Basic auth from domain settings."""
    config = _get_config(db)
    if not config:
        return []

    import requests

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {config['basic_token']}",
    }
    url = _resolve_customer_url(config)
    try:
        response = requests.get(  # nosec B113 — timeout via config dict
            url,
            headers=headers,
            timeout=config["timeout_seconds"],
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.error("splynx_fetch_customers_failed error=%s", str(exc))
        return []


def fetch_customer(db: Session, splynx_id: str) -> dict[str, Any] | None:
    """Fetch a single customer from Splynx by ID."""
    config = _get_config(db)
    if not config:
        return None

    import requests

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {config['basic_token']}",
    }
    url = f"{_resolve_customer_url(config)}/{splynx_id}"
    try:
        response = requests.get(  # nosec B113 — timeout via config dict
            url,
            headers=headers,
            timeout=config["timeout_seconds"],
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.error("splynx_fetch_customer_failed splynx_id=%s error=%s", splynx_id, str(exc))
        return None


def fetch_customer_internet_services(db: Session, splynx_id: str) -> list[dict[str, Any]]:
    """Fetch internet services for a single Splynx customer."""
    config = _get_config(db)
    if not config:
        return []

    import requests

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {config['basic_token']}",
    }
    url = f"{_resolve_customer_url(config)}/{splynx_id}/internet-services"
    try:
        response = requests.get(  # nosec B113 — timeout via config dict
            url,
            headers=headers,
            timeout=config["timeout_seconds"],
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []
    except Exception as exc:
        logger.warning("splynx_fetch_internet_services_failed splynx_id=%s error=%s", splynx_id, str(exc))
        return []


def fetch_customer_billing(db: Session, splynx_id: str) -> dict[str, Any] | None:
    """Fetch billing information for a single Splynx customer."""
    config = _get_config(db)
    if not config:
        return None

    import requests

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {config['basic_token']}",
    }
    url = f"{_resolve_customer_url(config)}/{splynx_id}/billing"
    try:
        response = requests.get(  # nosec B113 — timeout via config dict
            url,
            headers=headers,
            timeout=config["timeout_seconds"],
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return None
    except Exception as exc:
        logger.warning("splynx_fetch_billing_failed splynx_id=%s error=%s", splynx_id, str(exc))
        return None


def map_customer_to_subscriber_data(
    db: Session,
    customer: dict[str, Any],
    *,
    include_remote_details: bool = True,
) -> dict[str, Any]:
    """
    Map a Splynx customer payload into subscriber sync data.

    Returns only non-empty fields so sync can perform partial updates safely.
    """
    external_id = str(customer.get("id") or "").strip()
    internet_services: list[dict[str, Any]] = []
    billing: dict[str, Any] | None = None

    if include_remote_details and external_id:
        internet_services = fetch_customer_internet_services(db, external_id)
        billing = fetch_customer_billing(db, external_id)
    else:
        raw_services = customer.get("internet_services") or customer.get("internet-services")
        if isinstance(raw_services, list):
            internet_services = [row for row in raw_services if isinstance(row, dict)]
        raw_billing = customer.get("billing")
        if isinstance(raw_billing, dict):
            billing = raw_billing

    primary_service = _select_primary_service(internet_services)
    description = _coalesce_str(
        customer.get("tariff_name"),
        primary_service.get("description") if primary_service else None,
    )
    status_value = _map_splynx_status(customer.get("status"))
    speed_value = _extract_speed(primary_service if primary_service else customer, description)
    balance_value = _extract_balance(customer, billing, primary_service)
    next_bill_date = _extract_next_bill_date(customer, billing, primary_service)

    candidate_fields: dict[str, Any] = {
        "subscriber_number": _coalesce_str(
            customer.get("login"), primary_service.get("login") if primary_service else None
        ),
        "status": status_value,
        "service_name": description,
        "service_plan": description,
        "service_speed": speed_value,
        "balance": balance_value,
        "currency": _coalesce_str(customer.get("currency"), customer.get("currency_code")) or "USD",
        "service_address_line1": _coalesce_str(customer.get("street"), customer.get("street_1")),
        "service_city": _coalesce_str(customer.get("city")),
        "service_region": _coalesce_str(customer.get("state"), customer.get("region")),
        "service_postal_code": _coalesce_str(customer.get("zip"), customer.get("zip_code")),
        "next_bill_date": next_bill_date,
    }
    return {key: value for key, value in candidate_fields.items() if _has_value(value)}


def _map_splynx_status(status: str | int | None) -> str:
    status_map: dict[str | int, str] = {
        "active": SubscriberStatus.active.value,
        "blocked": SubscriberStatus.suspended.value,
        "disabled": SubscriberStatus.suspended.value,
        "inactive": SubscriberStatus.terminated.value,
        "terminated": SubscriberStatus.terminated.value,
        "new": SubscriberStatus.pending.value,
        1: SubscriberStatus.active.value,
        2: SubscriberStatus.suspended.value,
        0: SubscriberStatus.terminated.value,
    }
    if status is None:
        return SubscriberStatus.active.value
    mapped = status_map.get(status)
    if mapped is None:
        logger.warning("splynx_unknown_status value=%s — defaulting to active", status)
        return SubscriberStatus.active.value
    return mapped


def _status_rank(status: object) -> int:
    value = str(status or "").lower().strip()
    order = {
        "active": 0,
        "new": 1,
        "blocked": 2,
        "disabled": 3,
        "pending": 4,
        "hidden": 5,
    }
    return order.get(value, 9)


def _parse_splynx_date(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text or text == "0000-00-00":
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _select_primary_service(services: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not services:
        return None

    def sort_key(service: dict[str, Any]):
        # Prefer "primary" status, then most recently started/ended service.
        start = _parse_splynx_date(service.get("start_date"))
        end = _parse_splynx_date(service.get("end_date"))
        service_id = int(service.get("id") or 0)
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
    down = _coalesce_str(
        source.get("speed_download"),
        source.get("speed_download_mbps"),
        source.get("download_speed"),
    )
    up = _coalesce_str(
        source.get("speed_upload"),
        source.get("speed_upload_mbps"),
        source.get("upload_speed"),
    )
    if down and up:
        return f"{down}/{up} Mbps"
    if down:
        return f"{down} Mbps"
    if up:
        return f"{up} Mbps"

    text = description or ""
    pair_match = _SPEED_PAIR_RE.search(text)
    if pair_match:
        return f"{pair_match.group('down')}/{pair_match.group('up')} Mbps"
    single_match = _SINGLE_SPEED_RE.search(text)
    if single_match:
        return f"{single_match.group('speed')} Mbps"
    return None


def _extract_balance(
    customer: dict[str, Any],
    billing: dict[str, Any] | None,
    primary_service: dict[str, Any] | None,
) -> str | None:
    for candidate in (
        customer.get("balance"),
        billing.get("balance") if billing else None,
        billing.get("deposit") if billing else None,
        customer.get("account_balance"),
        customer.get("mrr_total"),
        primary_service.get("unit_price") if primary_service else None,
    ):
        normalized = _normalize_decimal_str(candidate)
        if normalized is not None:
            return normalized
    return None


def _extract_next_bill_date(
    customer: dict[str, Any],
    billing: dict[str, Any] | None,
    primary_service: dict[str, Any] | None,
) -> datetime | None:
    expire_in_raw = _coalesce_str(customer.get("expire_in"))
    if expire_in_raw:
        inferred = _parse_expire_in(expire_in_raw)
        if inferred is not None:
            return inferred

    for candidate in (
        customer.get("next_bill_date"),
        customer.get("next_billing_date"),
        customer.get("expire"),
        primary_service.get("end_date") if primary_service else None,
    ):
        parsed = _parse_splynx_date(candidate)
        if parsed is not None:
            return parsed

    if billing:
        billing_day = billing.get("billing_date")
        derived = _derive_next_billing_date(billing_day)
        if derived is not None:
            return derived

        parsed_blocking = _parse_splynx_date(billing.get("blocking_date"))
        if parsed_blocking is not None:
            return parsed_blocking

    return None


def _normalize_decimal_str(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return f"{float(text):.2f}"
    except (TypeError, ValueError):
        return text


def _coalesce_str(*values: object) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _parse_expire_in(value: str) -> datetime | None:
    text = value.strip().lower()
    if not text:
        return None
    now = datetime.now(UTC)
    day_match = re.search(r"(\d+)\s*day", text)
    if day_match:
        days = int(day_match.group(1))
        return now.replace(microsecond=0) + timedelta(days=days)
    hour_match = re.search(r"(\d+)\s*hour", text)
    if hour_match:
        hours = int(hour_match.group(1))
        return now.replace(microsecond=0) + timedelta(hours=hours)
    return None


def _derive_next_billing_date(billing_day: object) -> datetime | None:
    try:
        day = int(str(billing_day).strip())
    except (TypeError, ValueError):
        return None
    if day <= 0:
        return None

    now = datetime.now(UTC)
    year = now.year
    month = now.month
    current_month_day = min(day, monthrange(year, month)[1])
    if now.day <= current_month_day:
        return datetime(year, month, current_month_day, tzinfo=UTC)

    if month == 12:
        year += 1
        month = 1
    else:
        month += 1
    next_month_day = min(day, monthrange(year, month)[1])
    return datetime(year, month, next_month_day, tzinfo=UTC)


def _resolve_customer_url(config: dict[str, Any]) -> str:
    if config.get("customer_url"):
        return str(config["customer_url"])
    base_url = str(config["base_url"]).rstrip("/")
    if "/admin/customers/customer" in base_url:
        return base_url
    return f"{base_url}/admin/customers/customer"


def _resolve_invoice_urls(config: dict[str, Any]) -> list[str]:
    """Return candidate invoice endpoints, preferring explicit config."""
    configured = str(config.get("invoice_url") or "").strip()
    if configured:
        return [configured]
    base_url = _resolve_api_base_for_invoices(config)
    candidates = [
        f"{base_url}/admin/finance/invoices",
        f"{base_url}/admin/finance/invoice",
    ]
    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _resolve_api_base_for_invoices(config: dict[str, Any]) -> str:
    """
    Resolve API base URL for invoice endpoints.

    Handles legacy deployments where `splynx_base_url` is mistakenly set
    to the customer endpoint path.
    """
    customer_url = str(config.get("customer_url") or "").rstrip("/")
    base_url = str(config.get("base_url") or "").rstrip("/")

    for candidate in (customer_url, base_url):
        if candidate.endswith("/admin/customers/customer"):
            return candidate[: -len("/admin/customers/customer")]
    return base_url


def _safe_decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _build_installation_invoice_payload(
    *,
    splynx_id: str,
    amount: Decimal,
    description: str,
    external_ref: str | None,
) -> dict[str, Any]:
    today = datetime.now(UTC).date().isoformat()
    payload: dict[str, Any] = {
        "customer_id": int(splynx_id) if str(splynx_id).isdigit() else str(splynx_id),
        "date_created": today,
        "items": [
            {
                "description": description,
                "quantity": 1,
                "price": float(amount),
                "tax": 0,
            }
        ],
    }
    if external_ref:
        payload["note"] = external_ref
    return payload
