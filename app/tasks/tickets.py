"""Ticket periodic tasks."""

import logging
import time
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.metrics import observe_job

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.tickets.auto_confirm_resolved_tickets")
def auto_confirm_resolved_tickets() -> dict[str, Any]:
    """Close tickets left in pending_confirmation past their grace window.

    Guards against false closures the other way: a resolution the customer never
    confirms still closes after the grace period instead of hanging forever.
    """
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("AUTO_CONFIRM_RESOLVED_TICKETS_START")
    results: dict[str, Any] = {"auto_confirmed": 0}

    try:
        from app.services.tickets import tickets

        results["auto_confirmed"] = tickets.auto_confirm_pending(session)
        session.commit()
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("auto_confirm_resolved_tickets", status, time.monotonic() - start)

    logger.info("AUTO_CONFIRM_RESOLVED_TICKETS_COMPLETE results=%s", results)
    return results
