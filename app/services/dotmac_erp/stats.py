"""Stats tracking for DotMac ERP sync operations.

Uses Redis for storing sync statistics and history.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
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

_redis_client: Redis | None = None


def _get_redis() -> Redis | None:
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
    today = datetime.now(UTC).strftime("%Y-%m-%d")
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
        now = datetime.now(UTC)
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
        now = datetime.now(UTC)
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
        now = datetime.now(UTC)
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


# ============ Material Request Sync Stats ============

_MR_STATS_KEY_PREFIX = "erp_sync:mr_stats:"
_MR_LAST_SYNC_KEY = "erp_sync:mr_last_sync"
_MR_HISTORY_KEY = "erp_sync:mr_history"


def record_material_request_sync_result(
    material_request_id: str,
    erp_material_request_id: str | None,
    success: bool,
    error: str | None,
    duration_seconds: float,
) -> None:
    """Record material request sync result to Redis."""
    redis = _get_redis()
    if not redis:
        return

    try:
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        today = now.strftime("%Y-%m-%d")
        daily_key = f"{_MR_STATS_KEY_PREFIX}{today}"

        pipe = redis.pipeline()

        pipe.hincrby(daily_key, "total", 1)
        pipe.hincrby(daily_key, "success" if success else "errors", 1)
        pipe.hincrby(daily_key, "sync_count", 1)
        pipe.expire(daily_key, 7 * 24 * 60 * 60)

        last_sync = {
            "timestamp": now_iso,
            "material_request_id": material_request_id,
            "erp_material_request_id": erp_material_request_id,
            "success": success,
            "error": error,
            "duration_seconds": duration_seconds,
        }
        pipe.set(_MR_LAST_SYNC_KEY, json.dumps(last_sync))

        history_entry = {**last_sync}
        pipe.lpush(_MR_HISTORY_KEY, json.dumps(history_entry))
        pipe.ltrim(_MR_HISTORY_KEY, 0, _HISTORY_MAX_SIZE - 1)

        pipe.execute()
        logger.debug(f"Recorded material request sync stats: mr={material_request_id} success={success}")

    except Exception as e:
        logger.warning(f"Failed to record material request sync stats to Redis: {e}")


def get_last_material_request_sync() -> dict | None:
    """Get last material request sync timestamp and status."""
    redis = _get_redis()
    if not redis:
        return None

    try:
        data = cast(str | None, redis.get(_MR_LAST_SYNC_KEY))
        if not data:
            return None
        return json.loads(data)
    except Exception as e:
        logger.warning(f"Failed to get last material request sync from Redis: {e}")
        return None


def get_material_request_sync_history(limit: int = 10) -> list[dict]:
    """Get recent material request sync history."""
    redis = _get_redis()
    if not redis:
        return []

    try:
        entries = cast(list[str], redis.lrange(_MR_HISTORY_KEY, 0, limit - 1))
        return [json.loads(entry) for entry in entries]
    except Exception as e:
        logger.warning(f"Failed to get material request sync history from Redis: {e}")
        return []


# ============ Contact Sync Stats ============

_CONTACT_LAST_SYNC_KEY = "erp_sync:contact_last_sync"
_CONTACT_HISTORY_KEY = "erp_sync:contact_history"


def record_contact_sync_result(
    orgs_created: int,
    orgs_updated: int,
    contacts_created: int,
    contacts_updated: int,
    contacts_linked: int,
    channels_upserted: int,
    errors: list[dict],
    duration_seconds: float,
) -> None:
    """Record contact sync result to Redis."""
    redis = _get_redis()
    if not redis:
        return

    try:
        now = datetime.now(UTC)
        total = orgs_created + orgs_updated + contacts_created + contacts_updated
        success = len(errors) == 0

        last_sync = {
            "timestamp": now.isoformat(),
            "orgs_created": orgs_created,
            "orgs_updated": orgs_updated,
            "contacts_created": contacts_created,
            "contacts_updated": contacts_updated,
            "contacts_linked": contacts_linked,
            "channels_upserted": channels_upserted,
            "total": total,
            "errors": len(errors),
            "duration_seconds": duration_seconds,
            "success": success,
        }

        pipe = redis.pipeline()
        pipe.set(_CONTACT_LAST_SYNC_KEY, json.dumps(last_sync))
        pipe.lpush(_CONTACT_HISTORY_KEY, json.dumps(last_sync))
        pipe.ltrim(_CONTACT_HISTORY_KEY, 0, _HISTORY_MAX_SIZE - 1)
        pipe.execute()

        logger.debug(f"Recorded contact sync stats: {total} items synced")
    except Exception as e:
        logger.warning(f"Failed to record contact sync stats to Redis: {e}")


def get_last_contact_sync() -> dict | None:
    """Get last contact sync timestamp and status."""
    redis = _get_redis()
    if not redis:
        return None

    try:
        data = cast(str | None, redis.get(_CONTACT_LAST_SYNC_KEY))
        if not data:
            return None
        return json.loads(data)
    except Exception as e:
        logger.warning(f"Failed to get last contact sync from Redis: {e}")
        return None


# ============ Team Sync Stats ============

_TEAM_LAST_SYNC_KEY = "erp_sync:team_last_sync"
_TEAM_HISTORY_KEY = "erp_sync:team_history"


def record_team_sync_result(
    teams_created: int,
    teams_updated: int,
    teams_deactivated: int,
    members_added: int,
    members_updated: int,
    members_deactivated: int,
    persons_matched: int,
    persons_skipped: int,
    errors: list[dict],
    duration_seconds: float,
) -> None:
    """Record team sync result to Redis."""
    redis = _get_redis()
    if not redis:
        return

    try:
        now = datetime.now(UTC)
        total = teams_created + teams_updated + members_added + members_updated
        success = len(errors) == 0

        last_sync = {
            "timestamp": now.isoformat(),
            "teams_created": teams_created,
            "teams_updated": teams_updated,
            "teams_deactivated": teams_deactivated,
            "members_added": members_added,
            "members_updated": members_updated,
            "members_deactivated": members_deactivated,
            "persons_matched": persons_matched,
            "persons_skipped": persons_skipped,
            "total": total,
            "errors": len(errors),
            "duration_seconds": duration_seconds,
            "success": success,
        }

        pipe = redis.pipeline()
        pipe.set(_TEAM_LAST_SYNC_KEY, json.dumps(last_sync))
        pipe.lpush(_TEAM_HISTORY_KEY, json.dumps(last_sync))
        pipe.ltrim(_TEAM_HISTORY_KEY, 0, _HISTORY_MAX_SIZE - 1)
        pipe.execute()

        logger.debug(f"Recorded team sync stats: {total} items synced")
    except Exception as e:
        logger.warning(f"Failed to record team sync stats to Redis: {e}")


def get_last_team_sync() -> dict | None:
    """Get last team sync timestamp and status."""
    redis = _get_redis()
    if not redis:
        return None

    try:
        data = cast(str | None, redis.get(_TEAM_LAST_SYNC_KEY))
        if not data:
            return None
        return json.loads(data)
    except Exception as e:
        logger.warning(f"Failed to get last team sync from Redis: {e}")
        return None
