"""Customer retention background tasks."""

from __future__ import annotations

import time
from contextlib import suppress
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging import get_logger
from app.metrics import observe_job
from app.models.subscriber import Subscriber, SubscriberStatus
from app.services.common import coerce_uuid

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.customer_retention.sync_lost_retention_customer_to_splynx")
def sync_lost_retention_customer_to_splynx(
    customer_id: str,
    engagement_id: str,
    subscriber_id: str | None = None,
) -> dict[str, Any]:
    """Deactivate a Splynx customer after an explicit Lost retention outcome."""
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    try:
        from app.services.splynx import deactivate_customer_if_blocked

        result = deactivate_customer_if_blocked(
            session,
            customer_id=customer_id,
            engagement_id=engagement_id,
            subscriber_id=subscriber_id,
        )
        _mark_deactivation_result(
            session,
            customer_id=customer_id,
            engagement_id=engagement_id,
            subscriber_id=str(result.get("subscriber_id") or subscriber_id or "") or None,
            result=result,
        )
        return result
    except Exception as exc:
        status = "error"
        session.rollback()
        logger.exception(
            "retention_splynx_deactivation_task_error customer_id=%s subscriber_id=%s engagement_id=%s error=%s",
            customer_id,
            subscriber_id,
            engagement_id,
            str(exc),
        )
        _mark_deactivation_result(
            session,
            customer_id=customer_id,
            engagement_id=engagement_id,
            subscriber_id=subscriber_id,
            result={"success": False, "error": str(exc)},
        )
        return {"success": False, "customer_id": customer_id, "engagement_id": engagement_id, "error": str(exc)}
    finally:
        session.close()
        observe_job("retention_splynx_deactivation", status, time.monotonic() - start)


def _mark_deactivation_result(
    session,
    *,
    customer_id: str,
    engagement_id: str,
    subscriber_id: str | None,
    result: dict[str, Any],
) -> None:
    subscriber = None
    if subscriber_id:
        with suppress(ValueError):
            subscriber = session.get(Subscriber, coerce_uuid(subscriber_id))
    if subscriber is None:
        subscriber = (
            session.query(Subscriber)
            .filter(Subscriber.external_system == "splynx")
            .filter(Subscriber.external_id == str(customer_id or "").strip())
            .first()
        )
    if subscriber is None:
        return

    metadata = dict(subscriber.sync_metadata or {})
    marker = dict(metadata.get("retention_splynx_deactivation") or {})
    marker.update(
        {
            "engagement_id": str(engagement_id),
            "splynx_id": str(customer_id or "").strip(),
            "status": "success" if result.get("success") else "failed",
            "skipped": bool(result.get("skipped")),
            "reason": result.get("reason"),
            "error": result.get("error"),
            "previous_status": result.get("previous_status"),
            "new_status": result.get("new_status") or ("disabled" if result.get("success") else None),
        }
    )
    metadata["retention_splynx_deactivation"] = marker
    subscriber.sync_metadata = metadata
    if result.get("success") and not result.get("skipped"):
        subscriber.status = SubscriberStatus.terminated
    if result.get("success"):
        subscriber.sync_error = None
    elif result.get("error"):
        subscriber.sync_error = str(result["error"])[:500]
    session.add(subscriber)
    session.commit()
