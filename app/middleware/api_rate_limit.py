"""Global API rate limiting middleware.

Provides configurable rate limiting for API endpoints using Redis
with fallback to in-memory storage.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from collections.abc import Callable
from threading import Lock
from typing import TYPE_CHECKING, ClassVar

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.logging import get_logger

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

logger = get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RATE_LIMIT_PREFIX = "api_rate:"

# Default rate limits (can be overridden via environment)
DEFAULT_LIMIT = int(os.getenv("API_RATE_LIMIT", "100"))  # requests per window
DEFAULT_WINDOW = int(os.getenv("API_RATE_WINDOW", "60"))  # seconds

# In-memory fallback storage
_memory_store: dict[str, list[float]] = defaultdict(list)
_memory_lock = Lock()


class APIRateLimitMiddleware:
    """
    Rate limiting middleware for API endpoints.

    Uses sliding window algorithm with Redis for distributed rate limiting.
    Falls back to in-memory storage if Redis is unavailable.

    Rate limit headers are added to responses:
    - X-RateLimit-Limit: Maximum requests per window
    - X-RateLimit-Remaining: Requests remaining in current window
    - X-RateLimit-Reset: Seconds until window resets
    """

    # Paths to skip rate limiting
    EXEMPT_PATHS: ClassVar[set[str]] = {
        "/health",
        "/ready",
        "/metrics",
        "/static",
        "/favicon.ico",
    }

    # Path prefixes to skip
    EXEMPT_PREFIXES: ClassVar[tuple[str, ...]] = (
        "/static/",
        "/docs",
        "/openapi",
        "/redoc",
        # Public inbound webhooks must not be throttled by generic API limits.
        "/webhooks/",
    )

    def __init__(
        self,
        app: ASGIApp,
        limit: int = DEFAULT_LIMIT,
        window_seconds: int = DEFAULT_WINDOW,
        key_func: Callable[[Request], str] | None = None,
    ):
        self.app = app
        self.limit = limit
        self.window_seconds = window_seconds
        self.key_func = key_func or self._default_key_func
        self._redis = None
        self._redis_available = None

    def _default_key_func(self, request: Request) -> str:
        """Generate rate limit key from request.

        Uses authenticated user ID if available, otherwise client IP.
        """
        # Try to get user from request state (set by auth middleware)
        user_id = getattr(getattr(request, "state", None), "user_id", None)
        if user_id:
            return f"user:{user_id}"

        # Fall back to IP address
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"
        return f"ip:{ip}"

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
                logger.info("api_rate_limiter_redis_connected")
            except Exception as e:
                logger.warning("api_rate_limiter_redis_unavailable error=%s", e)
                self._redis_available = False
                self._redis = None

        return self._redis

    def _should_skip(self, path: str) -> bool:
        """Check if path should skip rate limiting."""
        if path in self.EXEMPT_PATHS:
            return True
        return any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        # Skip rate limiting for exempt paths
        if self._should_skip(path):
            await self.app(scope, receive, send)
            return

        # Get rate limit key
        key = self.key_func(request)

        # Check rate limit
        allowed, remaining, reset_in = self._check_rate(key)

        if not allowed:
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please try again later.",
                    "retry_after": reset_in,
                },
                headers={
                    "X-RateLimit-Limit": str(self.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_in),
                    "Retry-After": str(reset_in),
                },
            )
            await response(scope, receive, send)
            return

        # Add rate limit headers to response
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"X-RateLimit-Limit", str(self.limit).encode()),
                        (b"X-RateLimit-Remaining", str(remaining).encode()),
                        (b"X-RateLimit-Reset", str(reset_in).encode()),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def _check_rate(self, key: str) -> tuple[bool, int, int]:
        """
        Check rate limit using sliding window algorithm.

        Returns:
            Tuple of (allowed, remaining, reset_in_seconds)
        """
        redis = self._get_redis()
        if redis:
            return self._check_rate_redis(redis, key)
        else:
            return self._check_rate_memory(key)

    def _check_rate_redis(self, redis, key: str) -> tuple[bool, int, int]:
        """Check rate limit using Redis sorted set for sliding window."""
        full_key = f"{RATE_LIMIT_PREFIX}{key}"
        now = time.time()
        window_start = now - self.window_seconds

        try:
            pipe = redis.pipeline()
            pipe.zremrangebyscore(full_key, 0, window_start)
            pipe.zcard(full_key)
            pipe.zadd(full_key, {str(now): now})
            pipe.expire(full_key, self.window_seconds + 1)
            results = pipe.execute()

            current_count = results[1]

            if current_count >= self.limit:
                redis.zrem(full_key, str(now))
                # Calculate reset time
                oldest = redis.zrange(full_key, 0, 0, withscores=True)
                if oldest:
                    reset_in = int(oldest[0][1] + self.window_seconds - now) + 1
                else:
                    reset_in = self.window_seconds
                return False, 0, reset_in

            remaining = self.limit - current_count - 1
            return True, max(0, remaining), self.window_seconds

        except Exception as e:
            logger.warning("api_rate_limit_redis_error key=%s error=%s", key, e)
            return True, self.limit - 1, self.window_seconds

    def _check_rate_memory(self, key: str) -> tuple[bool, int, int]:
        """Check rate limit using in-memory storage (fallback)."""
        now = time.time()
        window_start = now - self.window_seconds

        with _memory_lock:
            _memory_store[key] = [t for t in _memory_store[key] if t > window_start]
            current_count = len(_memory_store[key])

            if current_count >= self.limit:
                if _memory_store[key]:
                    reset_in = int(_memory_store[key][0] + self.window_seconds - now) + 1
                else:
                    reset_in = self.window_seconds
                return False, 0, reset_in

            _memory_store[key].append(now)
            remaining = self.limit - current_count - 1
            return True, max(0, remaining), self.window_seconds
