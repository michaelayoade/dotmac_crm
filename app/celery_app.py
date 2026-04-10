import asyncio
import os
import platform
import sys

from celery import Celery

# ---------------------------------------------------------------------------
# Python 3.12 PidfdChildWatcher workaround (see app/main.py for details)
# ---------------------------------------------------------------------------
if sys.version_info >= (3, 12) and platform.system() == "Linux":
    _policy = asyncio.get_event_loop_policy()
    if hasattr(_policy, "set_child_watcher") and hasattr(asyncio, "ThreadedChildWatcher"):
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*child_watcher.*")
            _policy.set_child_watcher(asyncio.ThreadedChildWatcher())

from app.services.scheduler_config import get_celery_config


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _base_celery_config() -> dict[str, object]:
    redis_url = os.getenv("REDIS_URL")
    return {
        "broker_url": os.getenv("CELERY_BROKER_URL") or redis_url or "redis://localhost:6379/0",
        "result_backend": os.getenv("CELERY_RESULT_BACKEND") or redis_url or "redis://localhost:6379/1",
        "timezone": os.getenv("CELERY_TIMEZONE") or "UTC",
        "beat_max_loop_interval": _env_int("CELERY_BEAT_MAX_LOOP_INTERVAL", 5),
        "beat_refresh_seconds": _env_int("CELERY_BEAT_REFRESH_SECONDS", 30),
        "beat_scheduler": "app.celery_scheduler.DbScheduler",
    }


def configure_celery_app(*, use_db: bool = False) -> Celery:
    celery_app.conf.update(_base_celery_config())
    if use_db:
        celery_app.conf.update(get_celery_config())
    return celery_app


celery_app = Celery("dotmac_crm")
configure_celery_app()
celery_app.conf.beat_schedule = {}
celery_app.autodiscover_tasks(["app.tasks"])
