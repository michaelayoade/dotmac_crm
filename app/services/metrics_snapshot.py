"""Bounded snapshots consumed synchronously by the Prometheus scrape path."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import redis

logger = logging.getLogger(__name__)

_DATABASE_PRESSURE_KEY = "observability:state:database_pressure"
_DATABASE_PRESSURE_TTL_SECONDS = 86_400
_REDIS_TIMEOUT_SECONDS = 0.25
_client: redis.Redis | None = None


def _redis_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
            socket_timeout=_REDIS_TIMEOUT_SECONDS,
        )
    return _client


def publish_database_pressure_snapshot(
    values: dict[str, int | float],
    *,
    now: datetime | None = None,
) -> bool:
    """Store one bounded, numeric PostgreSQL pressure snapshot."""
    allowed = {
        "active",
        "idle",
        "idle_in_transaction",
        "total",
        "oldest_xact_age_seconds",
    }
    normalized = {
        key: float(value) for key, value in values.items() if key in allowed and isinstance(value, (int, float))
    }
    if set(normalized) != allowed:
        return False
    payload = {
        "domain": "database_pressure",
        "observed_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "values": normalized,
    }
    try:
        return bool(
            _redis_client().setex(
                _DATABASE_PRESSURE_KEY,
                _DATABASE_PRESSURE_TTL_SECONDS,
                json.dumps(payload, separators=(",", ":")),
            )
        )
    except (redis.RedisError, TypeError, ValueError):
        logger.warning("database_pressure_snapshot_publish_failed", exc_info=True)
        return False


def load_database_pressure_snapshot() -> dict[str, Any] | None:
    """Read the latest snapshot without falling back to PostgreSQL."""
    try:
        raw: Any = _redis_client().get(_DATABASE_PRESSURE_KEY)
        payload = json.loads(raw) if raw else None
    except (redis.RedisError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("domain") != "database_pressure":
        return None
    values = payload.get("values")
    if not isinstance(values, dict):
        return None
    return payload
