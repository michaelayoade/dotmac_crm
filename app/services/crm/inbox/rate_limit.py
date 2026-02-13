"""Rate limiting for outbound CRM inbox messages."""

from __future__ import annotations

import time

from app.logging import get_logger
from app.services.settings_cache import get_settings_redis

logger = get_logger(__name__)

RATE_LIMIT_PREFIX = "inbox_rate:"
WINDOW_SECONDS = 60


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__("Rate limit exceeded")
        self.retry_after = retry_after


def _redis_client():
    try:
        return get_settings_redis()
    except Exception:
        return None


def _in_memory_store():
    if not hasattr(_in_memory_store, "_store"):
        _in_memory_store._store = {}
    return _in_memory_store._store


def check_rate_limit(key: str, limit: int) -> None:
    if limit <= 0:
        return
    now = time.time()
    window_start = now - WINDOW_SECONDS
    redis = _redis_client()
    full_key = f"{RATE_LIMIT_PREFIX}{key}"
    if redis:
        try:
            pipe = redis.pipeline()
            pipe.zremrangebyscore(full_key, 0, window_start)
            pipe.zadd(full_key, {str(now): now})
            pipe.zrange(full_key, 0, 0, withscores=True)
            pipe.zcard(full_key)
            pipe.expire(full_key, WINDOW_SECONDS + 5)
            _, _, oldest_entries, count, _ = pipe.execute()
            if count and int(count) > limit:
                oldest_ts = oldest_entries[0][1] if oldest_entries else now
                retry_after = int(oldest_ts + WINDOW_SECONDS - now)
                raise RateLimitExceeded(max(retry_after, 1))
            return
        except RateLimitExceeded:
            raise
        except Exception as exc:
            logger.warning("inbox_rate_limit_redis_error key=%s error=%s", full_key, exc)

    store: dict[str, list[float]] = _in_memory_store()
    bucket = store.get(full_key, [])
    bucket = [ts for ts in bucket if ts > window_start]
    bucket.append(now)
    store[full_key] = bucket
    if len(bucket) > limit:
        oldest_ts = bucket[0] if bucket else now
        retry_after = int(oldest_ts + WINDOW_SECONDS - now)
        raise RateLimitExceeded(max(retry_after, 1))


def build_rate_limit_key(channel: str, target_id: str | None) -> str:
    return f"{channel}:{target_id or 'default'}"
