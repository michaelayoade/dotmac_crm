"""Redis-based cache for DotMac ERP data.

Caches expense totals from ERP to avoid blocking web requests.
Uses a 5-minute TTL to balance freshness with performance.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

import redis

from app.services.settings_cache import get_settings_redis

logger = logging.getLogger(__name__)


@dataclass
class ExpenseTotals:
    """Expense totals from ERP."""
    draft: float = 0.0
    submitted: float = 0.0
    approved: float = 0.0
    paid: float = 0.0
    erp_available: bool = True
    cached: bool = False

    def to_dict(self) -> dict:
        return {
            "draft": self.draft,
            "submitted": self.submitted,
            "approved": self.approved,
            "paid": self.paid,
            "erp_available": self.erp_available,
            "cached": self.cached,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExpenseTotals":
        return cls(
            draft=data.get("draft", 0.0),
            submitted=data.get("submitted", 0.0),
            approved=data.get("approved", 0.0),
            paid=data.get("paid", 0.0),
            erp_available=data.get("erp_available", True),
            cached=True,  # If loading from cache, mark as cached
        )

    @classmethod
    def unavailable(cls) -> "ExpenseTotals":
        """Return an ExpenseTotals indicating ERP is unavailable."""
        return cls(erp_available=False)


class ERPExpenseCache:
    """Redis-based cache for ERP expense totals.

    Caches expense totals to avoid blocking web requests while
    still showing relatively fresh data.
    """

    PREFIX = "erp_expense:"
    TTL = 300  # 5 minutes

    @staticmethod
    def _cache_key(entity_type: str, entity_id: str) -> str:
        """Build the Redis cache key."""
        return f"{ERPExpenseCache.PREFIX}{entity_type}:{entity_id}"

    @staticmethod
    def get(entity_type: str, entity_id: str) -> ExpenseTotals | None:
        """Get cached expense totals.

        Args:
            entity_type: "project", "ticket", or "work_order"
            entity_id: UUID of the entity

        Returns:
            ExpenseTotals if cached, None if not cached
        """
        try:
            r = get_settings_redis()
            cache_key = ERPExpenseCache._cache_key(entity_type, entity_id)
            value = r.get(cache_key)
            if isinstance(value, str):
                data = json.loads(value)
                return ExpenseTotals.from_dict(data)
        except redis.RedisError as exc:
            logger.warning(f"ERP expense cache get failed: {exc}")
        except json.JSONDecodeError as exc:
            logger.warning(f"ERP expense cache JSON decode failed: {exc}")
        return None

    @staticmethod
    def set(entity_type: str, entity_id: str, totals: ExpenseTotals) -> bool:
        """Cache expense totals.

        Args:
            entity_type: "project", "ticket", or "work_order"
            entity_id: UUID of the entity
            totals: The expense totals to cache

        Returns:
            True if cached successfully, False on error
        """
        try:
            r = get_settings_redis()
            cache_key = ERPExpenseCache._cache_key(entity_type, entity_id)
            r.setex(cache_key, ERPExpenseCache.TTL, json.dumps(totals.to_dict()))
            return True
        except redis.RedisError as exc:
            logger.warning(f"ERP expense cache set failed: {exc}")
        except (TypeError, ValueError) as exc:
            logger.warning(f"ERP expense cache JSON encode failed: {exc}")
        return False

    @staticmethod
    def invalidate(entity_type: str, entity_id: str) -> bool:
        """Invalidate cached expense totals.

        Args:
            entity_type: "project", "ticket", or "work_order"
            entity_id: UUID of the entity

        Returns:
            True if invalidated successfully, False on error
        """
        try:
            r = get_settings_redis()
            cache_key = ERPExpenseCache._cache_key(entity_type, entity_id)
            r.delete(cache_key)
            return True
        except redis.RedisError as exc:
            logger.warning(f"ERP expense cache invalidate failed: {exc}")
        return False


def get_cached_expense_totals(
    db,
    entity_type: str,
    entity_id: str,
    timeout: float = 5.0,
) -> ExpenseTotals:
    """Get expense totals with caching.

    First checks Redis cache. On cache miss, fetches from ERP API
    and caches the result. On any error, returns an unavailable result.

    Args:
        db: Database session (for ERP sync service)
        entity_type: "project", "ticket", or "work_order"
        entity_id: UUID of the entity
        timeout: Max seconds to wait for ERP API (default 5s)

    Returns:
        ExpenseTotals with data or unavailable flag
    """
    # Check cache first
    cached = ERPExpenseCache.get(entity_type, entity_id)
    if cached is not None:
        return cached

    # Cache miss - fetch from ERP
    try:
        from app.services.dotmac_erp.sync import dotmac_erp_sync

        erp_sync = dotmac_erp_sync(db)
        try:
            # Get totals based on entity type
            if entity_type == "project":
                totals_map = erp_sync.get_project_expense_totals([entity_id])
            elif entity_type == "ticket":
                totals_map = erp_sync.get_ticket_expense_totals([entity_id])
            elif entity_type == "work_order":
                totals_map = erp_sync.get_work_order_expense_totals([entity_id])
            else:
                return ExpenseTotals.unavailable()

            raw_totals = totals_map.get(entity_id)
            if raw_totals:
                totals = ExpenseTotals(
                    draft=raw_totals.get("draft", 0.0),
                    submitted=raw_totals.get("submitted", 0.0),
                    approved=raw_totals.get("approved", 0.0),
                    paid=raw_totals.get("paid", 0.0),
                    erp_available=True,
                    cached=False,
                )
            else:
                # ERP returned empty - entity might not exist in ERP yet
                totals = ExpenseTotals(erp_available=True, cached=False)

            # Cache the result
            ERPExpenseCache.set(entity_type, entity_id, totals)
            return totals
        finally:
            erp_sync.close()
    except Exception as exc:
        logger.warning(f"Failed to fetch expense totals for {entity_type} {entity_id}: {exc}")
        # Cache the unavailable state briefly (60s) to avoid hammering ERP
        unavailable = ExpenseTotals.unavailable()
        try:
            r = get_settings_redis()
            cache_key = ERPExpenseCache._cache_key(entity_type, entity_id)
            r.setex(cache_key, 60, json.dumps(unavailable.to_dict()))
        except Exception:
            pass
        return unavailable
