"""Splynx integration helpers (customer creation)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.person import PartyStatus, Person
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
        response = requests.post(
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


def ensure_person_customer(db: Session, person: Person, splynx_id: str | None) -> None:
    """Persist Splynx ID and upgrade contact type to customer when applicable."""
    if splynx_id:
        if person.metadata_ is None or not isinstance(person.metadata_, dict):
            person.metadata_ = {}
        person.metadata_["splynx_id"] = splynx_id
        person.party_status = PartyStatus.subscriber

    if not splynx_id and person.party_status in {PartyStatus.lead, PartyStatus.contact}:
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
        response = requests.get(
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
        response = requests.get(
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
        response = requests.get(
            url,
            headers=headers,
            timeout=config["timeout_seconds"],
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.error("splynx_fetch_customer_failed splynx_id=%s error=%s", splynx_id, str(exc))
        return None


def _resolve_customer_url(config: dict[str, Any]) -> str:
    if config.get("customer_url"):
        return str(config["customer_url"])
    base_url = str(config["base_url"]).rstrip("/")
    if "/admin/customers/customer" in base_url:
        return base_url
    return f"{base_url}/admin/customers/customer"
