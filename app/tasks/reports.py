"""Scheduled report delivery tasks."""

from __future__ import annotations

import time
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging import get_logger
from app.metrics import observe_job

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.reports.send_scheduled_ncc_report")
def send_scheduled_ncc_report() -> dict[str, Any]:
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("NCC_REPORT_EMAIL_START")
    try:
        from app.services.ncc_report_email import run_scheduled_ncc_report_email

        result = run_scheduled_ncc_report_email(session)
        logger.info(
            "NCC_REPORT_EMAIL_COMPLETE status=%s reason=%s rows=%s",
            result.get("status"),
            result.get("reason"),
            result.get("rows", 0),
        )
        return result
    except Exception:
        status = "error"
        session.rollback()
        logger.exception("NCC_REPORT_EMAIL_ERROR")
        raise
    finally:
        session.close()
        observe_job("ncc_report_email", status, time.monotonic() - start)
