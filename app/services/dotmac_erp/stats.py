"""Stats tracking for DotMac ERP sync operations.

Uses Redis for storing sync statistics and history.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from app.services.dotmac_erp.sync import SyncResult

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)

# Redis key patterns
_STATS_KEY_PREFIX = "erp_sync:stats:"
_LAST_SYNC_KEY = "erp_sync:last_sync"
_HISTORY_KEY = "erp_sync:history"
_HISTORY_MAX_SIZE = 20

# Inventory sync keys
_INV_STATS_KEY_PREFIX = "erp_sync:inv_stats:"
_INV_LAST_SYNC_KEY = "erp_sync:inv_last_sync"
_INV_HISTORY_KEY = "erp_sync:inv_history"

_redis_client: "Redis | None" = None


def _get_redis() -> "Redis | None":
    """Get Redis client, return None if not available."""
    global _redis_client
    if _redis_client is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        try:
            import redis
            _redis_client = redis.from_url(redis_url, decode_responses=True)
            _redis_client.ping()
        except Exception as e:
            logger.debug(f"erp_stats_redis_unavailable error={e}")
            return None
    return _redis_client


def _today_key() -> str:
    """Get Redis key for today's stats."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{_STATS_KEY_PREFIX}{today}"


def record_sync_result(result: SyncResult, mode: str = "recently_updated") -> None:
    """
    Record sync result to Redis for dashboard display.

    Args:
        result: SyncResult from sync operation
        mode: Sync mode ("recently_updated", "all_active", "entity")
    """
    redis = _get_redis()
    if not redis:
        logger.debug("Redis not available, skipping sync stats recording")
        return

    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Update daily stats (hash)
        daily_key = _today_key()
        pipe = redis.pipeline()

        # Increment counters
        pipe.hincrby(daily_key, "projects", result.projects_synced)
        pipe.hincrby(daily_key, "tickets", result.tickets_synced)
        pipe.hincrby(daily_key, "work_orders", result.work_orders_synced)
        pipe.hincrby(daily_key, "errors", len(result.errors))
        pipe.hincrby(daily_key, "sync_count", 1)

        # Set expiry for 7 days
        pipe.expire(daily_key, 7 * 24 * 60 * 60)

        # Store last sync info
        last_sync = {
            "timestamp": now_iso,
            "mode": mode,
            "projects": result.projects_synced,
            "tickets": result.tickets_synced,
            "work_orders": result.work_orders_synced,
            "total": result.total_synced,
            "errors": len(result.errors),
            "duration_seconds": result.duration_seconds,
            "success": not result.has_errors,
        }
        pipe.set(_LAST_SYNC_KEY, json.dumps(last_sync))

        # Add to history (capped list)
        history_entry = {
            "timestamp": now_iso,
            "mode": mode,
            "projects": result.projects_synced,
            "tickets": result.tickets_synced,
            "work_orders": result.work_orders_synced,
            "total": result.total_synced,
            "errors": len(result.errors),
            "error_details": result.errors[:5] if result.errors else [],  # Keep first 5 errors
            "duration_seconds": result.duration_seconds,
            "success": not result.has_errors,
        }
        pipe.lpush(_HISTORY_KEY, json.dumps(history_entry))
        pipe.ltrim(_HISTORY_KEY, 0, _HISTORY_MAX_SIZE - 1)

        pipe.execute()
        logger.debug(f"Recorded ERP sync stats: {result.total_synced} items synced")

    except Exception as e:
        logger.warning(f"Failed to record sync stats to Redis: {e}")


def get_daily_stats(date: datetime | None = None) -> dict:
    """
    Get sync stats for a specific day.

    Args:
        date: Date to get stats for (defaults to today)

    Returns:
        Dict with projects, tickets, work_orders, errors, sync_count
    """
    redis = _get_redis()
    if not redis:
        return {
            "projects": 0,
            "tickets": 0,
            "work_orders": 0,
            "errors": 0,
            "sync_count": 0,
        }

    try:
        if date:
            key = f"{_STATS_KEY_PREFIX}{date.strftime('%Y-%m-%d')}"
        else:
            key = _today_key()

        stats = cast(dict[str, str], redis.hgetall(key))
        return {
            "projects": int(stats.get("projects", 0)),
            "tickets": int(stats.get("tickets", 0)),
            "work_orders": int(stats.get("work_orders", 0)),
            "errors": int(stats.get("errors", 0)),
            "sync_count": int(stats.get("sync_count", 0)),
        }

    except Exception as e:
        logger.warning(f"Failed to get daily stats from Redis: {e}")
        return {
            "projects": 0,
            "tickets": 0,
            "work_orders": 0,
            "errors": 0,
            "sync_count": 0,
        }


def get_last_sync() -> dict | None:
    """
    Get last sync timestamp and status.

    Returns:
        Dict with timestamp, mode, counts, and success status, or None if no sync recorded
    """
    redis = _get_redis()
    if not redis:
        return None

    try:
        data = cast(str | None, redis.get(_LAST_SYNC_KEY))
        if not data:
            return None
        return json.loads(data)
    except Exception as e:
        logger.warning(f"Failed to get last sync from Redis: {e}")
        return None


def get_sync_history(limit: int = 10) -> list[dict]:
    """
    Get recent sync history.

    Args:
        limit: Maximum number of history entries to return

    Returns:
        List of sync result dicts, most recent first
    """
    redis = _get_redis()
    if not redis:
        return []

    try:
        entries = cast(list[str], redis.lrange(_HISTORY_KEY, 0, limit - 1))
        return [json.loads(entry) for entry in entries]
    except Exception as e:
        logger.warning(f"Failed to get sync history from Redis: {e}")
        return []


def clear_stats() -> None:
    """Clear all sync stats (for testing)."""
    redis = _get_redis()
    if not redis:
        return

    try:
        # Find and delete all stats keys
        keys = cast(list[str], redis.keys(f"{_STATS_KEY_PREFIX}*"))
        if keys:
            redis.delete(*keys)
        redis.delete(_LAST_SYNC_KEY, _HISTORY_KEY)
        # Also clear inventory stats
        inv_keys = cast(list[str], redis.keys(f"{_INV_STATS_KEY_PREFIX}*"))
        if inv_keys:
            redis.delete(*inv_keys)
        redis.delete(_INV_LAST_SYNC_KEY, _INV_HISTORY_KEY)
        logger.info("Cleared all ERP sync stats")
    except Exception as e:
        logger.warning(f"Failed to clear stats from Redis: {e}")


# ============ Inventory Sync Stats ============

def record_inventory_sync_result(
    items_created: int,
    items_updated: int,
    locations_created: int,
    locations_updated: int,
    stock_updated: int,
    errors: list[str],
    duration_seconds: float,
) -> None:
    """
    Record inventory sync result to Redis for dashboard display.

    Args:
        items_created: Number of new items created
        items_updated: Number of existing items updated
        locations_created: Number of new locations created
        locations_updated: Number of existing locations updated
        stock_updated: Number of stock records updated
        errors: List of error messages
        duration_seconds: Sync duration in seconds
    """
    redis = _get_redis()
    if not redis:
        logger.debug("Redis not available, skipping inventory sync stats recording")
        return

    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        today = now.strftime("%Y-%m-%d")
        daily_key = f"{_INV_STATS_KEY_PREFIX}{today}"

        total = items_created + items_updated + locations_created + locations_updated + stock_updated
        success = len(errors) == 0

        pipe = redis.pipeline()

        # Update daily stats (hash)
        pipe.hincrby(daily_key, "items_created", items_created)
        pipe.hincrby(daily_key, "items_updated", items_updated)
        pipe.hincrby(daily_key, "locations_created", locations_created)
        pipe.hincrby(daily_key, "locations_updated", locations_updated)
        pipe.hincrby(daily_key, "stock_updated", stock_updated)
        pipe.hincrby(daily_key, "errors", len(errors))
        pipe.hincrby(daily_key, "sync_count", 1)

        # Set expiry for 7 days
        pipe.expire(daily_key, 7 * 24 * 60 * 60)

        # Store last sync info
        last_sync = {
            "timestamp": now_iso,
            "items_created": items_created,
            "items_updated": items_updated,
            "locations_created": locations_created,
            "locations_updated": locations_updated,
            "stock_updated": stock_updated,
            "total": total,
            "errors": len(errors),
            "duration_seconds": duration_seconds,
            "success": success,
        }
        pipe.set(_INV_LAST_SYNC_KEY, json.dumps(last_sync))

        # Add to history (capped list)
        history_entry = {
            "timestamp": now_iso,
            "items_created": items_created,
            "items_updated": items_updated,
            "locations_created": locations_created,
            "locations_updated": locations_updated,
            "stock_updated": stock_updated,
            "total": total,
            "errors": len(errors),
            "error_details": errors[:5] if errors else [],
            "duration_seconds": duration_seconds,
            "success": success,
        }
        pipe.lpush(_INV_HISTORY_KEY, json.dumps(history_entry))
        pipe.ltrim(_INV_HISTORY_KEY, 0, _HISTORY_MAX_SIZE - 1)

        pipe.execute()
        logger.debug(f"Recorded inventory sync stats: {total} items synced")

    except Exception as e:
        logger.warning(f"Failed to record inventory sync stats to Redis: {e}")


def get_last_inventory_sync() -> dict | None:
    """
    Get last inventory sync timestamp and status.

    Returns:
        Dict with timestamp, counts, and success status, or None if no sync recorded
    """
    redis = _get_redis()
    if not redis:
        return None

    try:
        data = cast(str | None, redis.get(_INV_LAST_SYNC_KEY))
        if not data:
            return None
        return json.loads(data)
    except Exception as e:
        logger.warning(f"Failed to get last inventory sync from Redis: {e}")
        return None


def get_inventory_sync_history(limit: int = 10) -> list[dict]:
    """
    Get recent inventory sync history.

    Args:
        limit: Maximum number of history entries to return

    Returns:
        List of sync result dicts, most recent first
    """
    redis = _get_redis()
    if not redis:
        return []

    try:
        entries = cast(list[str], redis.lrange(_INV_HISTORY_KEY, 0, limit - 1))
        return [json.loads(entry) for entry in entries]
    except Exception as e:
        logger.warning(f"Failed to get inventory sync history from Redis: {e}")
        return []


# ============ Shift Sync Stats ============

_SHIFT_STATS_KEY_PREFIX = "erp_sync:shift_stats:"
_SHIFT_LAST_SYNC_KEY = "erp_sync:shift_last_sync"
_SHIFT_HISTORY_KEY = "erp_sync:shift_history"


def record_shift_sync_result(
    shifts_created: int,
    shifts_updated: int,
    time_off_created: int,
    time_off_updated: int,
    technicians_matched: int,
    technicians_skipped: int,
    errors: list[dict],
    duration_seconds: float,
) -> None:
    """
    Record shift sync result to Redis for dashboard display.

    Args:
        shifts_created: Number of new shifts created
        shifts_updated: Number of existing shifts updated
        time_off_created: Number of new time-off blocks created
        time_off_updated: Number of existing time-off blocks updated
        technicians_matched: Number of technicians matched to ERP employees
        technicians_skipped: Number of employees without matching technicians
        errors: List of error dicts
        duration_seconds: Sync duration in seconds
    """
    redis = _get_redis()
    if not redis:
        logger.debug("Redis not available, skipping shift sync stats recording")
        return

    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        today = now.strftime("%Y-%m-%d")
        daily_key = f"{_SHIFT_STATS_KEY_PREFIX}{today}"

        total = shifts_created + shifts_updated + time_off_created + time_off_updated
        success = len(errors) == 0

        pipe = redis.pipeline()

        # Update daily stats (hash)
        pipe.hincrby(daily_key, "shifts_created", shifts_created)
        pipe.hincrby(daily_key, "shifts_updated", shifts_updated)
        pipe.hincrby(daily_key, "time_off_created", time_off_created)
        pipe.hincrby(daily_key, "time_off_updated", time_off_updated)
        pipe.hincrby(daily_key, "technicians_matched", technicians_matched)
        pipe.hincrby(daily_key, "technicians_skipped", technicians_skipped)
        pipe.hincrby(daily_key, "errors", len(errors))
        pipe.hincrby(daily_key, "sync_count", 1)

        # Set expiry for 7 days
        pipe.expire(daily_key, 7 * 24 * 60 * 60)

        # Store last sync info
        last_sync = {
            "timestamp": now_iso,
            "shifts_created": shifts_created,
            "shifts_updated": shifts_updated,
            "time_off_created": time_off_created,
            "time_off_updated": time_off_updated,
            "technicians_matched": technicians_matched,
            "technicians_skipped": technicians_skipped,
            "total": total,
            "errors": len(errors),
            "duration_seconds": duration_seconds,
            "success": success,
        }
        pipe.set(_SHIFT_LAST_SYNC_KEY, json.dumps(last_sync))

        # Add to history (capped list)
        history_entry = {
            "timestamp": now_iso,
            "shifts_created": shifts_created,
            "shifts_updated": shifts_updated,
            "time_off_created": time_off_created,
            "time_off_updated": time_off_updated,
            "technicians_matched": technicians_matched,
            "technicians_skipped": technicians_skipped,
            "total": total,
            "errors": len(errors),
            "error_details": errors[:5] if errors else [],
            "duration_seconds": duration_seconds,
            "success": success,
        }
        pipe.lpush(_SHIFT_HISTORY_KEY, json.dumps(history_entry))
        pipe.ltrim(_SHIFT_HISTORY_KEY, 0, _HISTORY_MAX_SIZE - 1)

        pipe.execute()
        logger.debug(f"Recorded shift sync stats: {total} items synced")

    except Exception as e:
        logger.warning(f"Failed to record shift sync stats to Redis: {e}")


def get_last_shift_sync() -> dict | None:
    """
    Get last shift sync timestamp and status.

    Returns:
        Dict with timestamp, counts, and success status, or None if no sync recorded
    """
    redis = _get_redis()
    if not redis:
        return None

    try:
        data = cast(str | None, redis.get(_SHIFT_LAST_SYNC_KEY))
        if not data:
            return None
        return json.loads(data)
    except Exception as e:
        logger.warning(f"Failed to get last shift sync from Redis: {e}")
        return None


def get_shift_sync_history(limit: int = 10) -> list[dict]:
    """
    Get recent shift sync history.

    Args:
        limit: Maximum number of history entries to return

    Returns:
        List of sync result dicts, most recent first
    """
    redis = _get_redis()
    if not redis:
        return []

    try:
        entries = cast(list[str], redis.lrange(_SHIFT_HISTORY_KEY, 0, limit - 1))
        return [json.loads(entry) for entry in entries]
    except Exception as e:
        logger.warning(f"Failed to get shift sync history from Redis: {e}")
        return []
