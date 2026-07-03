"""On-demand backfill of existing CRM sales financials into dotmac_sub."""

import logging
import time
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.metrics import observe_job

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.billing_sync.backfill_sales_financials_to_sub")
def backfill_sales_financials_to_sub(batch_size: int = 500, max_batches: int = 200) -> dict[str, Any]:
    """Sweep all paid/partial sales orders and push each one's installation
    invoice + payment to sub. Idempotent — safe to run repeatedly. On-demand
    (not a beat task); trigger with .delay() during a migration."""
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("BACKFILL_SALES_FINANCIALS_START")
    results: dict[str, Any] = {"processed": 0, "batches": 0}

    try:
        from app.services.billing_sync import backfill_sales_payments_to_sub

        offset = 0
        for _ in range(max_batches):
            batch = backfill_sales_payments_to_sub(session, limit=batch_size, offset=offset)
            session.commit()
            results["processed"] += batch["processed"]
            results["batches"] += 1
            if batch["batch_size"] < batch_size:
                break
            offset += batch_size
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("backfill_sales_financials_to_sub", status, time.monotonic() - start)

    logger.info("BACKFILL_SALES_FINANCIALS_COMPLETE results=%s", results)
    return results
