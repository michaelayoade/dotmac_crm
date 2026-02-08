"""Lightweight in-memory cache for CRM inbox services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any


INBOX_LIST_TTL_SECONDS = 5
COMMENTS_LIST_TTL_SECONDS = 300
COMMENTS_THREAD_TTL_SECONDS = 300


@dataclass
class _CacheEntry:
    value: Any
    expires_at: datetime


_cache: dict[str, _CacheEntry] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def invalidate_prefix(prefix: str) -> None:
    keys = [key for key in _cache if key.startswith(prefix)]
    for key in keys:
        _cache.pop(key, None)


def invalidate_inbox_list() -> None:
    invalidate_prefix("inbox_list:")


def invalidate_comments() -> None:
    invalidate_prefix("comments_list:")
    invalidate_prefix("comment_thread:")


def build_inbox_list_key(params: dict[str, Any]) -> str:
    encoded = json.dumps(params, sort_keys=True, default=str)
    return f"inbox_list:{encoded}"


def build_comments_list_key(search: str | None) -> str:
    return f"comments_list:{search or ''}"


def build_comment_thread_key(comment_id: str) -> str:
    return f"comment_thread:{comment_id}"
