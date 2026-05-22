"""Subscriber offline outreach automation tasks."""

import time
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging import get_logger
from app.metrics import observe_job

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.subscriber_outreach.run_daily_offline_outreach")
def run_daily_offline_outreach_task() -> dict[str, Any]:
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("SUBSCRIBER_OFFLINE_OUTREACH_START")
    try:
        from app.services.subscriber_offline_outreach import run_daily_offline_outreach

        result = run_daily_offline_outreach(session)
        logger.info(
            "SUBSCRIBER_OFFLINE_OUTREACH_COMPLETE status=%s sent=%s skipped=%s failed=%s evaluated=%s",
            result.get("status"),
            result.get("sent", 0),
            result.get("skipped", 0),
            result.get("failed", 0),
            result.get("evaluated", 0),
        )
        return result
    except Exception:
        status = "error"
        session.rollback()
        logger.exception("SUBSCRIBER_OFFLINE_OUTREACH_ERROR")
        raise
    finally:
        session.close()
        observe_job("subscriber_offline_outreach", status, time.monotonic() - start)


@celery_app.task(name="app.tasks.subscriber_outreach.resolve_stale_offline_outreach_conversations")
def resolve_stale_offline_outreach_conversations_task(
    older_than_hours: int = 25,
    limit: int = 500,
) -> dict[str, Any]:
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("SUBSCRIBER_OFFLINE_OUTREACH_AUTO_RESOLVE_START")
    try:
        from app.services.subscriber_offline_outreach import resolve_stale_offline_outreach_conversations

        result = resolve_stale_offline_outreach_conversations(
            session,
            older_than_hours=older_than_hours,
            limit=limit,
        )
        logger.info(
            "SUBSCRIBER_OFFLINE_OUTREACH_AUTO_RESOLVE_COMPLETE resolved=%s skipped_replied=%s errors=%s",
            result.get("resolved", 0),
            result.get("skipped_replied", 0),
            len(result.get("errors", [])),
        )
        return result
    except Exception:
        status = "error"
        session.rollback()
        logger.exception("SUBSCRIBER_OFFLINE_OUTREACH_AUTO_RESOLVE_ERROR")
        raise
    finally:
        session.close()
        observe_job("subscriber_offline_outreach_auto_resolve", status, time.monotonic() - start)
