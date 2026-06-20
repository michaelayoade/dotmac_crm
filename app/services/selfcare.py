"""Selfcare integration helpers for customer creation."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.person import PartyStatus, Person
from app.services import settings_spec

logger = logging.getLogger(__name__)

DEFAULT_CUSTOMER_WEBHOOK_PATH = "/api/v1/webhooks/crm/customers"
_CUSTOMER_LAST_SYNC_KEY = "selfcare_sync:customer:last"
_CUSTOMER_HISTORY_KEY = "selfcare_sync:customer:history"
_CUSTOMER_DAILY_STATS_PREFIX = "selfcare_sync:customer:stats:"
_HISTORY_MAX_SIZE = 30

if TYPE_CHECKING:
    from redis import Redis

_redis_client: Redis | None = None


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
) -> str | None:
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

    subscriber_id = data.get("subscriber_id") or data.get("id")
    if not subscriber_id:
        logger.error("selfcare_create_customer_no_id response=%s", data)
        return None
    return str(subscriber_id)


def record_customer_sync_result(
    *,
    success: bool,
    mode: str,
    person_id: str,
    selfcare_subscriber_id: str | None = None,
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


def ensure_person_customer(db: Session, person: Person, selfcare_subscriber_id: str | None) -> None:
    """Persist selfcare subscriber ID and mark the party as a customer."""
    if selfcare_subscriber_id:
        if person.metadata_ is None or not isinstance(person.metadata_, dict):
            person.metadata_ = {}
        person.metadata_["selfcare_subscriber_id"] = str(selfcare_subscriber_id)

    if person.party_status in {PartyStatus.lead, PartyStatus.contact}:
        person.party_status = PartyStatus.customer

    db.add(person)
    db.commit()
    db.refresh(person)
