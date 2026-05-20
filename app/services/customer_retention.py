"""Customer retention engagement services."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer_retention import CustomerRetentionEngagement
from app.models.person import Person
from app.models.subscriber import Subscriber, SubscriberStatus
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)

RETENTION_SPLYNX_DEACTIVATION_OUTCOMES = frozenset({"Lost", "Churning"})


def parse_follow_up_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def create_retention_engagement_and_sync(
    db: Session,
    *,
    customer_id: str,
    outcome: str,
    customer_name: str | None = None,
    note: str | None = None,
    follow_up: object = None,
    rep_person_id: str | None = None,
    rep: str | None = None,
    created_by_person_id: str | None = None,
    enqueue_sync: bool = True,
) -> CustomerRetentionEngagement:
    """
    Create a retention engagement and enqueue Splynx deactivation after commit.

    Explicit Lost outcomes and Churning outcomes that map to the Lost pipeline
    stage trigger the Splynx flow. Do Not Reach Out remains excluded because it
    is a contact preference, not necessarily a disconnect decision.
    """
    normalized_customer_id = str(customer_id or "").strip()
    normalized_outcome = str(outcome or "").strip()
    if not normalized_customer_id or not normalized_outcome:
        raise ValueError("Customer and outcome are required")

    rep_person_uuid = _optional_uuid(rep_person_id)
    rep_label = str(rep or "").strip() or None
    if rep_person_uuid is not None:
        rep_person = db.get(Person, rep_person_uuid)
        if rep_person is not None:
            rep_label = (
                str(
                    rep_person.display_name
                    or f"{rep_person.first_name or ''} {rep_person.last_name or ''}".strip()
                    or rep_person.email
                    or ""
                ).strip()
                or rep_label
            )

    engagement = CustomerRetentionEngagement(
        customer_external_id=normalized_customer_id,
        customer_name=str(customer_name or "").strip() or None,
        outcome=normalized_outcome,
        note=str(note or "").strip() or None,
        follow_up_date=parse_follow_up_date(follow_up),
        rep_person_id=rep_person_uuid,
        rep_label=rep_label,
        created_by_person_id=_optional_uuid(created_by_person_id),
        is_active=True,
    )
    db.add(engagement)
    db.commit()
    db.refresh(engagement)

    if enqueue_sync and should_enqueue_splynx_deactivation(normalized_outcome):
        _enqueue_splynx_deactivation(db, engagement)
    return engagement


def should_enqueue_splynx_deactivation(outcome: str) -> bool:
    """Return true when a retention outcome should enter the Splynx disable flow."""
    return str(outcome or "").strip() in RETENTION_SPLYNX_DEACTIVATION_OUTCOMES


def enqueue_existing_churning_deactivations(
    db: Session,
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Reconcile existing latest-Churning customers into the Splynx disable flow.

    This is the backfill counterpart to the create-time gate. It only considers
    the latest active engagement per customer, and it leaves the final Splynx
    safety check to ``deactivate_customer_if_blocked``.
    """
    latest_ranked = (
        select(
            CustomerRetentionEngagement.id.label("engagement_id"),
            CustomerRetentionEngagement.customer_external_id.label("customer_external_id"),
            CustomerRetentionEngagement.outcome.label("outcome"),
            func.row_number()
            .over(
                partition_by=CustomerRetentionEngagement.customer_external_id,
                order_by=CustomerRetentionEngagement.created_at.desc(),
            )
            .label("rn"),
        )
        .where(CustomerRetentionEngagement.is_active.is_(True))
        .subquery()
    )
    stmt = (
        select(
            latest_ranked.c.engagement_id,
            latest_ranked.c.customer_external_id,
            Subscriber.id.label("subscriber_id"),
            Subscriber.status.label("subscriber_status"),
            Subscriber.sync_metadata.label("sync_metadata"),
        )
        .join(
            Subscriber,
            (Subscriber.external_system == "splynx") & (Subscriber.external_id == latest_ranked.c.customer_external_id),
        )
        .where(latest_ranked.c.rn == 1)
        .where(latest_ranked.c.outcome == "Churning")
        .order_by(latest_ranked.c.customer_external_id)
    )
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))

    rows = db.execute(stmt).mappings().all()
    result: dict[str, Any] = {
        "evaluated": len(rows),
        "eligible": 0,
        "enqueued": 0,
        "skipped": 0,
        "dry_run": dry_run,
        "items": [],
    }
    for row in rows:
        sync_metadata = row["sync_metadata"] if isinstance(row["sync_metadata"], dict) else {}
        marker_value = sync_metadata.get("retention_splynx_deactivation")
        marker = marker_value if isinstance(marker_value, dict) else {}
        marker_status = str(marker.get("status") or "").strip()
        item = {
            "customer_id": str(row["customer_external_id"] or "").strip(),
            "subscriber_id": str(row["subscriber_id"] or "").strip(),
            "engagement_id": str(row["engagement_id"] or "").strip(),
            "status": "eligible",
        }
        subscriber_status = row["subscriber_status"]
        subscriber_status_value = (
            subscriber_status.value if isinstance(subscriber_status, SubscriberStatus) else str(subscriber_status or "")
        )
        if subscriber_status_value == SubscriberStatus.terminated.value:
            item.update({"status": "skipped", "reason": "subscriber_already_terminated"})
            result["skipped"] += 1
            result["items"].append(item)
            continue
        if marker_status:
            item.update({"status": "skipped", "reason": f"deactivation_already_{marker_status}"})
            result["skipped"] += 1
            result["items"].append(item)
            continue

        result["eligible"] += 1
        if not dry_run:
            engagement = db.get(CustomerRetentionEngagement, row["engagement_id"])
            if engagement is not None:
                _enqueue_splynx_deactivation(db, engagement)
                item["status"] = "enqueued"
                result["enqueued"] += 1
        result["items"].append(item)
    return result


def _enqueue_splynx_deactivation(db: Session, engagement: CustomerRetentionEngagement) -> None:
    splynx_id = str(engagement.customer_external_id or "").strip()
    subscriber = _subscriber_for_splynx_id(db, splynx_id)
    subscriber_id = str(subscriber.id) if subscriber is not None else None
    audit_context = {
        "customer_id": splynx_id,
        "subscriber_id": subscriber_id,
        "splynx_id": splynx_id,
        "engagement_id": str(engagement.id),
    }

    if subscriber is not None:
        if subscriber.status == SubscriberStatus.terminated:
            logger.info(
                "retention_splynx_deactivation_enqueue_skip customer_id=%s subscriber_id=%s splynx_id=%s "
                "engagement_id=%s reason=subscriber_already_terminated",
                audit_context["customer_id"],
                audit_context["subscriber_id"],
                audit_context["splynx_id"],
                audit_context["engagement_id"],
            )
            return
        marker = _retention_deactivation_marker(subscriber)
        marker_status = str(marker.get("status") or "").strip()
        if marker_status:
            logger.info(
                "retention_splynx_deactivation_enqueue_skip customer_id=%s subscriber_id=%s splynx_id=%s "
                "engagement_id=%s reason=deactivation_already_%s existing_engagement_id=%s",
                audit_context["customer_id"],
                audit_context["subscriber_id"],
                audit_context["splynx_id"],
                audit_context["engagement_id"],
                marker_status,
                marker.get("engagement_id"),
            )
            return
        _set_retention_deactivation_marker(
            db,
            subscriber,
            {
                "status": "queued",
                "engagement_id": str(engagement.id),
                "splynx_id": splynx_id,
                "queued_at": datetime.now(UTC).isoformat(),
            },
        )

    try:
        from app.tasks.customer_retention import sync_lost_retention_customer_to_splynx

        sync_lost_retention_customer_to_splynx.delay(
            splynx_id,
            str(engagement.id),
            subscriber_id=subscriber_id,
        )
        logger.info(
            "retention_splynx_deactivation_enqueued customer_id=%s subscriber_id=%s splynx_id=%s engagement_id=%s",
            audit_context["customer_id"],
            audit_context["subscriber_id"],
            audit_context["splynx_id"],
            audit_context["engagement_id"],
        )
    except Exception as exc:
        if subscriber is not None:
            _set_retention_deactivation_marker(
                db,
                subscriber,
                {
                    "status": "enqueue_failed",
                    "engagement_id": str(engagement.id),
                    "splynx_id": splynx_id,
                    "error": str(exc)[:500],
                },
            )
        logger.exception(
            "retention_splynx_deactivation_enqueue_failed customer_id=%s subscriber_id=%s splynx_id=%s "
            "engagement_id=%s error=%s",
            audit_context["customer_id"],
            audit_context["subscriber_id"],
            audit_context["splynx_id"],
            audit_context["engagement_id"],
            str(exc),
        )


def _subscriber_for_splynx_id(db: Session, splynx_id: str) -> Subscriber | None:
    if not splynx_id:
        return None
    return (
        db.query(Subscriber)
        .filter(Subscriber.external_system == "splynx")
        .filter(Subscriber.external_id == splynx_id)
        .first()
    )


def _retention_deactivation_marker(subscriber: Subscriber) -> dict[str, Any]:
    metadata = subscriber.sync_metadata if isinstance(subscriber.sync_metadata, dict) else {}
    marker = metadata.get("retention_splynx_deactivation")
    return marker if isinstance(marker, dict) else {}


def _set_retention_deactivation_marker(db: Session, subscriber: Subscriber, marker: dict[str, Any]) -> None:
    metadata = dict(subscriber.sync_metadata or {})
    metadata["retention_splynx_deactivation"] = marker
    subscriber.sync_metadata = metadata
    db.add(subscriber)
    db.commit()


def _optional_uuid(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return coerce_uuid(text)
    except ValueError:
        return None
