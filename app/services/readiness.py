"""Readiness probe: verifies the runtime dependencies the web app needs to
serve traffic (database + Redis). Distinct from the shallow ``/health``
liveness probe. Every check is time-bounded so a hung dependency yields a fast
degraded response rather than blocking the probe.

Celery *worker* liveness is intentionally out of scope — workers run in
separate processes with their own health; the web instance is "ready" as long
as it can reach the DB and Redis (cache / broker / rate-limiter backend).
"""

from __future__ import annotations

import contextlib
import logging
import os

from sqlalchemy import text

from app.db import SessionLocal

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 2.0


def _check_db() -> tuple[bool, str]:
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:
        logger.warning("readiness_db_check_failed error=%s", exc)
        return False, type(exc).__name__
    finally:
        with contextlib.suppress(Exception):
            db.close()


def _check_redis() -> tuple[bool, str]:
    try:
        import redis

        # A dedicated short-timeout client — the shared settings client has no
        # socket timeout, so a down Redis would otherwise hang the probe.
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=_PROBE_TIMEOUT_SECONDS,
            socket_timeout=_PROBE_TIMEOUT_SECONDS,
        )
        client.ping()
        return True, "ok"
    except Exception as exc:
        logger.warning("readiness_redis_check_failed error=%s", exc)
        return False, type(exc).__name__


def readiness_report() -> tuple[dict, bool]:
    """Return (payload, ready). ready is False if any critical dependency is down.

    Detail is the exception *type name* only — never a raw message — since this
    endpoint is unauthenticated.
    """
    db_ok, db_detail = _check_db()
    redis_ok, redis_detail = _check_redis()
    ready = db_ok and redis_ok
    payload = {
        "status": "ready" if ready else "degraded",
        "checks": {
            "database": {"ok": db_ok, "detail": db_detail},
            "redis": {"ok": redis_ok, "detail": redis_detail},
        },
    }
    return payload, ready
