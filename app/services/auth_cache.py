"""Redis caching for authentication sessions.

Provides optional caching layer to reduce database queries for session
validation. Cache misses fall back to database queries transparently.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, cast

from app.logging import get_logger

if TYPE_CHECKING:
    from redis import Redis

logger = get_logger(__name__)

AUTH_CACHE_PREFIX = "auth:"
SESSION_TTL = 300  # 5 minutes

_redis_client: Redis | None = None


def _get_redis() -> Redis | None:
    """Get Redis client instance, creating if needed.

    Returns None if Redis is unavailable, allowing graceful fallback.
    """
    global _redis_client
    if _redis_client is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        try:
            import redis

            _redis_client = redis.from_url(redis_url, decode_responses=True)
            # Test connection
            _redis_client.ping()
        except Exception as exc:
            logger.debug("auth_cache_redis_unavailable error=%s", exc)
            return None
    return _redis_client


def get_cached_session(session_id: str) -> dict | None:
    """Retrieve cached session data by session ID.

    Args:
        session_id: The session UUID as a string

    Returns:
        Session data dict if cached, None otherwise
    """
    client = _get_redis()
    if not client:
        return None
    try:
        data = cast(str | None, client.get(f"{AUTH_CACHE_PREFIX}session:{session_id}"))
        if data:
            return json.loads(data)
        return None
    except Exception as exc:
        logger.debug("auth_cache_get_error session_id=%s error=%s", session_id, exc)
        return None


def set_cached_session(session_id: str, data: dict) -> None:
    """Cache session data with TTL.

    Args:
        session_id: The session UUID as a string
        data: Session data to cache (person_id, roles, scopes, expires_at)
    """
    client = _get_redis()
    if not client:
        return
    try:
        client.setex(
            f"{AUTH_CACHE_PREFIX}session:{session_id}",
            SESSION_TTL,
            json.dumps(data),
        )
    except Exception as exc:
        logger.debug("auth_cache_set_error session_id=%s error=%s", session_id, exc)


def invalidate_session(session_id: str) -> None:
    """Remove session from cache.

    Call this on logout or session revocation.

    Args:
        session_id: The session UUID as a string
    """
    client = _get_redis()
    if not client:
        return
    try:
        client.delete(f"{AUTH_CACHE_PREFIX}session:{session_id}")
    except Exception as exc:
        logger.debug("auth_cache_invalidate_error session_id=%s error=%s", session_id, exc)
