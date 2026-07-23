"""Scheduled report delivery tasks."""

from __future__ import annotations

import time
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging import get_logger
from app.metrics import observe_job

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.reports.run_weekly_inbound_reporting")
def run_weekly_inbound_reporting() -> dict[str, Any]:
    """Generate, archive, and deliver both validated weekly inbound reports."""
    start = time.monotonic()
    metric_status = "success"
    logger.info("WEEKLY_REPORTING_START")
    try:
        from app.services.weekly_reporting.engine import run_weekly_reporting

        result = run_weekly_reporting()
        if result.get("status") == "failed":
            metric_status = "error"
            logger.error(
                "WEEKLY_REPORTING_COMPLETE status=failed errors=%s log=%s",
                result.get("errors"),
                result.get("execution_log"),
            )
            raise RuntimeError(
                "Weekly Reporting execution failed; "
                f"see {result.get('execution_log') or 'Celery worker logs'} for details."
            )
        else:
            logger.info(
                "WEEKLY_REPORTING_COMPLETE status=%s period=%s conversations=%s sales=%s support=%s email=%s log=%s",
                result.get("status"),
                result.get("reporting_period"),
                result.get("conversations_analysed", 0),
                result.get("sales_conversations_identified", 0),
                result.get("support_conversations_identified", 0),
                result.get("email_delivery_status"),
                result.get("execution_log"),
            )
        return result
    except Exception:
        metric_status = "error"
        logger.exception("WEEKLY_REPORTING_ERROR")
        raise
    finally:
        observe_job("weekly_inbound_reporting", metric_status, time.monotonic() - start)


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
