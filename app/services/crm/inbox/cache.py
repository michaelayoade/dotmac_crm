"""Lightweight in-memory cache for CRM inbox services.

NOTE: This is a per-process cache. Cross-worker invalidation relies on short
TTLs (30s for inbox list).  A Redis pubsub layer could be added later for
instant invalidation across workers, but the short TTLs are sufficient for now.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

INBOX_LIST_TTL_SECONDS = 30
SUMMARY_COUNTS_TTL_SECONDS = 30
COMMENTS_LIST_TTL_SECONDS = 60
COMMENTS_THREAD_TTL_SECONDS = 60


@dataclass
class _CacheEntry:
    value: Any
    expires_at: datetime


_cache: dict[str, _CacheEntry] = {}
_cache_locks: dict[str, Lock] = {}
_cache_locks_guard = Lock()


def _now() -> datetime:
    return datetime.now(UTC)


def _expired(entry: _CacheEntry) -> bool:
    return entry.expires_at <= _now()


def get(key: str) -> Any | None:
    entry = _cache.get(key)
    if not entry:
        return None
    if _expired(entry):
        _cache.pop(key, None)
        return None
    return entry.value


def set(key: str, value: Any, ttl_seconds: int) -> None:
    _cache[key] = _CacheEntry(
        value=value,
        expires_at=_now() + timedelta(seconds=ttl_seconds),
    )


def _lock_for_key(key: str) -> Lock:
    with _cache_locks_guard:
        return _cache_locks.setdefault(key, Lock())


def get_or_set(key: str, ttl_seconds: int, loader) -> Any:
    cached = get(key)
    if cached is not None:
        return cached
    lock = _lock_for_key(key)
    with lock:
        cached = get(key)
        if cached is not None:
            return cached
        value = loader()
        set(key, value, ttl_seconds)
        return value


def invalidate_prefix(prefix: str) -> None:
    keys = [key for key in _cache if key.startswith(prefix)]
    for key in keys:
        _cache.pop(key, None)


def invalidate_inbox_list() -> None:
    invalidate_prefix("inbox_list:")
    invalidate_prefix("summary_counts:")


def invalidate_comments() -> None:
    invalidate_prefix("comments_list:")
    invalidate_prefix("comment_thread:")


def build_inbox_list_key(params: dict[str, Any]) -> str:
    encoded = json.dumps(params, sort_keys=True, default=str)
    return f"inbox_list:{encoded}"


def build_summary_counts_key(params: dict[str, Any]) -> str:
    encoded = json.dumps(params, sort_keys=True, default=str)
    return f"summary_counts:{encoded}"


def build_comments_list_key(params: dict[str, Any]) -> str:
    encoded = json.dumps(params, sort_keys=True, default=str)
    return f"comments_list:{encoded}"


def build_comment_thread_key(comment_id: str) -> str:
    # Versioned to avoid reusing legacy cache entries that may contain ORM objects.
    return f"comment_thread:v2:{comment_id}"
