"""Workflow automation tasks for SLA monitoring and ticket management."""

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models.workflow import SlaClock, SlaClockStatus
from app.schemas.workflow import SlaBreachCreate
from app.services import projects as projects_service
from app.services import sla_violation_daily_report as sla_violation_daily_report_service
from app.services import workflow as workflow_service

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.workflow.detect_sla_breaches")
def detect_sla_breaches() -> dict[str, int]:
    """
    Detect SLA breaches by checking all running SLA clocks.

    Runs every 30 minutes to identify SLA clocks that have exceeded their due_at
    time and creates SlaBreach records for them.

    Returns:
        dict with counts of checked clocks and breaches created
    """
    session = SessionLocal()
    checked = 0
    breached = 0
    errors = 0
    try:
        now = datetime.now(UTC)

        # Find all running SLA clocks that are past due
        overdue_clocks = (
            session.query(SlaClock)
            .filter(SlaClock.status == SlaClockStatus.running)
            .filter(SlaClock.due_at < now)
            .filter(SlaClock.breached_at.is_(None))
            .all()
        )

        checked = len(overdue_clocks)
        logger.info("SLA breach detection: found %d overdue clocks", checked)

        for clock in overdue_clocks:
            try:
                payload = SlaBreachCreate(
                    clock_id=clock.id,
                    breached_at=now,
                    notes=f"Auto-detected SLA breach at {now.isoformat()}",
                )
                workflow_service.sla_breaches.create(session, payload)
                session.refresh(clock)
                projects_service.notify_project_task_sla_breach(session, clock)
                session.commit()
                breached += 1
                logger.debug(
                    "Created SLA breach for clock %s (entity: %s/%s)",
                    clock.id,
                    clock.entity_type.value,
                    clock.entity_id,
                )
            except Exception as exc:
                errors += 1
                logger.exception(
                    "Failed to create SLA breach for clock %s: %s",
                    clock.id,
                    exc,
                )
                session.rollback()
                continue

    except Exception:
        session.rollback()
        logger.exception("SLA breach detection task failed")
        raise
    finally:
        session.close()

    logger.info(
        "SLA breach detection complete: checked=%d, breached=%d, errors=%d",
        checked,
        breached,
        errors,
    )
    return {"checked": checked, "breached": breached, "errors": errors}


@celery_app.task(name="app.tasks.workflow.send_daily_sla_violation_report")
def send_daily_sla_violation_report() -> dict[str, object]:
    session = SessionLocal()
    try:
        tz = ZoneInfo(sla_violation_daily_report_service.REPORT_TIMEZONE)
        now_local = datetime.now(UTC).astimezone(tz)
        business_date = now_local.date().isoformat()

        if now_local.hour < 7:
            return {
                "status": "skipped_before_window",
                "business_date": business_date,
                "timezone": sla_violation_daily_report_service.REPORT_TIMEZONE,
            }

        last_sent = sla_violation_daily_report_service.sla_violation_daily_report_service.get_last_sent_business_date(
            session
        )
        if last_sent == business_date:
            return {
                "status": "already_sent",
                "business_date": business_date,
                "timezone": sla_violation_daily_report_service.REPORT_TIMEZONE,
            }

        recipients = sla_violation_daily_report_service.sla_violation_daily_report_service.list_recipient_emails(
            session
        )
        if not recipients:
            return {
                "status": "no_recipients",
                "business_date": business_date,
                "timezone": sla_violation_daily_report_service.REPORT_TIMEZONE,
            }

        success, debug = sla_violation_daily_report_service.sla_violation_daily_report_service.send_daily_report(
            session,
            report_date=business_date,
        )
        if not success:
            session.rollback()
            return {
                "status": "send_failed",
                "business_date": business_date,
                "timezone": sla_violation_daily_report_service.REPORT_TIMEZONE,
                "error": (debug or {}).get("error") if isinstance(debug, dict) else None,
            }

        sla_violation_daily_report_service.sla_violation_daily_report_service.set_last_sent_business_date(
            session,
            business_date,
        )
        return {
            "status": "sent",
            "business_date": business_date,
            "timezone": sla_violation_daily_report_service.REPORT_TIMEZONE,
            "recipients": len(recipients),
        }
    except Exception:
        session.rollback()
        logger.exception("Daily SLA violation report task failed")
        raise
    finally:
        session.close()
