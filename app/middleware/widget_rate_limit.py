"""Rate limiting for chat widget endpoints.

Uses Redis for distributed rate limiting across instances.
Falls back to in-memory limiting if Redis is unavailable.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections import defaultdict
from threading import Lock
from typing import TYPE_CHECKING

from app.logging import get_logger

if TYPE_CHECKING:
    from uuid import UUID

logger = get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RATE_LIMIT_PREFIX = "widget_rate:"

# In-memory fallback storage
_memory_store: dict[str, list[float]] = defaultdict(list)
_memory_lock = Lock()


class WidgetRateLimiter:
    """Rate limiter for widget endpoints using Redis or in-memory storage."""

    def __init__(self):
        self._redis = None
        self._redis_available = None

    def _get_redis(self):
        """Get Redis client lazily."""
        if self._redis_available is False:
            return None

        if self._redis is None:
            try:
                import redis

                self._redis = redis.from_url(REDIS_URL, decode_responses=True)
                self._redis.ping()
                self._redis_available = True
                logger.info("widget_rate_limiter_redis_connected")
            except Exception as e:
                logger.warning("widget_rate_limiter_redis_unavailable error=%s", e)
                self._redis_available = False
                self._redis = None

        return self._redis

    def check_session_creation(
        self,
        ip_address: str,
        limit: int = 5,
        window_seconds: int = 300,
    ) -> tuple[bool, int]:
        """
        Check if IP can create a new session.

        Args:
            ip_address: Client IP address
            limit: Maximum sessions per window (default: 5)
            window_seconds: Time window in seconds (default: 5 minutes)

        Returns:
            Tuple of (allowed: bool, remaining: int)
        """
        key = f"session_create:{ip_address}"
        return self._check_rate(key, limit, window_seconds)

    def check_message_send(
        self,
        session_id: UUID | str,
        limit: int = 10,
        window_seconds: int = 60,
    ) -> tuple[bool, int]:
        """
        Check if session can send a message.

        Args:
            session_id: Widget session ID
            limit: Maximum messages per window (default: 10)
            window_seconds: Time window in seconds (default: 1 minute)

        Returns:
            Tuple of (allowed: bool, remaining: int)
        """
        key = f"message_send:{session_id}"
        return self._check_rate(key, limit, window_seconds)

    def check_websocket_connection(
        self,
        session_id: UUID | str,
        limit: int = 3,
        window_seconds: int = 60,
    ) -> tuple[bool, int]:
        """
        Check if session can open a new WebSocket connection.

        Args:
            session_id: Widget session ID
            limit: Maximum connections per window (default: 3)
            window_seconds: Time window in seconds (default: 1 minute)

        Returns:
            Tuple of (allowed: bool, remaining: int)
        """
        key = f"ws_connect:{session_id}"
        return self._check_rate(key, limit, window_seconds)

    def _check_rate(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        """
        Check rate limit using sliding window algorithm.

        Returns (allowed, remaining) tuple.
        """
        redis = self._get_redis()
        if redis:
            return self._check_rate_redis(redis, key, limit, window_seconds)
        else:
            return self._check_rate_memory(key, limit, window_seconds)

    def _check_rate_redis(
        self,
        redis,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        """Check rate limit using Redis sorted set for sliding window."""
        full_key = f"{RATE_LIMIT_PREFIX}{key}"
        now = time.time()
        window_start = now - window_seconds

        try:
            # Use pipeline for atomic operations
            pipe = redis.pipeline()

            # Remove old entries
            pipe.zremrangebyscore(full_key, 0, window_start)

            # Count current entries
            pipe.zcard(full_key)

            # Add new entry if under limit
            # We'll check the count after execution
            pipe.zadd(full_key, {str(now): now})

            # Set expiry
            pipe.expire(full_key, window_seconds + 1)

            results = pipe.execute()
            current_count = results[1]

            if current_count >= limit:
                # Remove the entry we just added since we're over limit
                redis.zrem(full_key, str(now))
                return False, 0

            remaining = limit - current_count - 1
            return True, max(0, remaining)

        except Exception as e:
            logger.warning("widget_rate_limit_redis_error key=%s error=%s", key, e)
            # Fall back to allowing on error
            return True, limit - 1

    def _check_rate_memory(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        """Check rate limit using in-memory storage (fallback)."""
        now = time.time()
        window_start = now - window_seconds

        with _memory_lock:
            # Clean old entries
            _memory_store[key] = [t for t in _memory_store[key] if t > window_start]

            current_count = len(_memory_store[key])

            if current_count >= limit:
                return False, 0

            # Add new entry
            _memory_store[key].append(now)
            remaining = limit - current_count - 1
            return True, max(0, remaining)

    def reset(self, key: str) -> None:
        """Reset rate limit for a key (for testing)."""
        redis = self._get_redis()
        if redis:
            with contextlib.suppress(Exception):
                redis.delete(f"{RATE_LIMIT_PREFIX}{key}")

        with _memory_lock:
            _memory_store.pop(key, None)


# Singleton instance
widget_rate_limiter = WidgetRateLimiter()


def check_session_creation_rate(ip_address: str, limit: int = 5) -> tuple[bool, int]:
    """Convenience function for session creation rate check."""
    return widget_rate_limiter.check_session_creation(ip_address, limit)


def check_message_rate(session_id: str, limit: int = 10) -> tuple[bool, int]:
    """Convenience function for message rate check."""
    return widget_rate_limiter.check_message_send(session_id, limit)


def check_websocket_rate(session_id: str, limit: int = 3) -> tuple[bool, int]:
    """Convenience function for WebSocket connection rate check."""
    return widget_rate_limiter.check_websocket_connection(session_id, limit)
