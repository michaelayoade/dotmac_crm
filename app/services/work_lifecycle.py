from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.crm.sales import Lead
from app.models.projects import Project, ProjectTask
from app.models.sales_order import SalesOrder
from app.models.subscriber import Subscriber
from app.models.tickets import Ticket
from app.models.work_lifecycle import (
    WorkEntityType,
    WorkLink,
    WorkLinkType,
    WorkOutcome,
    WorkOutcomeStatus,
    WorkOutcomeType,
)
from app.models.workforce import WorkOrder, WorkOrderType
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)

_ENTITY_MODELS = {
    WorkEntityType.ticket: Ticket,
    WorkEntityType.project: Project,
    WorkEntityType.project_task: ProjectTask,
    WorkEntityType.work_order: WorkOrder,
    WorkEntityType.lead: Lead,
    WorkEntityType.sales_order: SalesOrder,
    WorkEntityType.subscriber: Subscriber,
}

_COMPLETION_OUTCOME_BY_WORK_TYPE = {
    WorkOrderType.install: WorkOutcomeType.activation_requested,
    WorkOrderType.repair: WorkOutcomeType.repair_completed,
    WorkOrderType.disconnect: WorkOutcomeType.disconnect_completed,
}


def _coerce_entity_type(value: WorkEntityType | str, label: str) -> WorkEntityType:
    if isinstance(value, WorkEntityType):
        return value
    try:
        return WorkEntityType(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}") from exc


def _coerce_link_type(value: WorkLinkType | str) -> WorkLinkType:
    if isinstance(value, WorkLinkType):
        return value
    try:
        return WorkLinkType(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid link type") from exc


def _coerce_outcome_type(value: WorkOutcomeType | str) -> WorkOutcomeType:
    if isinstance(value, WorkOutcomeType):
        return value
    try:
        return WorkOutcomeType(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid outcome type") from exc


def _coerce_outcome_status(value: WorkOutcomeStatus | str) -> WorkOutcomeStatus:
    if isinstance(value, WorkOutcomeStatus):
        return value
    try:
        return WorkOutcomeStatus(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid outcome status") from exc


class WorkLifecycle:
    @staticmethod
    def link(
        db: Session,
        *,
        source_type: WorkEntityType | str,
        source_id: UUID | str,
        target_type: WorkEntityType | str,
        target_id: UUID | str,
        link_type: WorkLinkType | str,
        contract_name: str | None = None,
        created_by_person_id: UUID | str | None = None,
        metadata: dict | None = None,
    ) -> WorkLink:
        source_type_value = _coerce_entity_type(source_type, "source type")
        target_type_value = _coerce_entity_type(target_type, "target type")
        link_type_value = _coerce_link_type(link_type)
        source_uuid = coerce_uuid(source_id)
        target_uuid = coerce_uuid(target_id)

        existing = (
            db.query(WorkLink)
            .filter(WorkLink.source_type == source_type_value)
            .filter(WorkLink.source_id == source_uuid)
            .filter(WorkLink.target_type == target_type_value)
            .filter(WorkLink.target_id == target_uuid)
            .filter(WorkLink.link_type == link_type_value)
            .one_or_none()
        )
        if existing:
            if contract_name and not existing.contract_name:
                existing.contract_name = contract_name
            if metadata:
                existing.metadata_ = {**(existing.metadata_ or {}), **metadata}
            return existing

        link = WorkLink(
            source_type=source_type_value,
            source_id=source_uuid,
            target_type=target_type_value,
            target_id=target_uuid,
            link_type=link_type_value,
            contract_name=contract_name,
            created_by_person_id=coerce_uuid(created_by_person_id) if created_by_person_id else None,
            metadata_=metadata,
        )
        db.add(link)
        db.flush()
        return link

    @staticmethod
    def link_work_order_origin(
        db: Session,
        *,
        work_order_id: UUID | str,
        origin_type: WorkEntityType | str,
        origin_id: UUID | str,
        contract_name: str | None = None,
        created_by_person_id: UUID | str | None = None,
        metadata: dict | None = None,
    ) -> WorkLink:
        return WorkLifecycle.link(
            db,
            source_type=origin_type,
            source_id=origin_id,
            target_type=WorkEntityType.work_order,
            target_id=work_order_id,
            link_type=WorkLinkType.originated,
            contract_name=contract_name,
            created_by_person_id=created_by_person_id,
            metadata=metadata,
        )

    @staticmethod
    def work_order_origin_ids(
        db: Session,
        *,
        origin_type: WorkEntityType | str,
        origin_id: UUID | str,
        link_type: WorkLinkType | str = WorkLinkType.originated,
    ) -> list[UUID]:
        origin_type_value = _coerce_entity_type(origin_type, "origin type")
        link_type_value = _coerce_link_type(link_type)
        origin_uuid = coerce_uuid(origin_id)
        rows = (
            db.query(WorkLink.target_id)
            .filter(WorkLink.source_type == origin_type_value)
            .filter(WorkLink.source_id == origin_uuid)
            .filter(WorkLink.target_type == WorkEntityType.work_order)
            .filter(WorkLink.link_type == link_type_value)
            .all()
        )
        return [target_id for (target_id,) in rows]

    @staticmethod
    def work_order_origin_id(
        db: Session,
        *,
        work_order_id: UUID | str,
        origin_type: WorkEntityType | str,
        link_type: WorkLinkType | str = WorkLinkType.originated,
    ) -> UUID | None:
        """Reverse lookup: the origin entity (e.g. ticket) that a work order came from."""
        origin_type_value = _coerce_entity_type(origin_type, "origin type")
        link_type_value = _coerce_link_type(link_type)
        work_order_uuid = coerce_uuid(work_order_id)
        row = (
            db.query(WorkLink.source_id)
            .filter(WorkLink.target_type == WorkEntityType.work_order)
            .filter(WorkLink.target_id == work_order_uuid)
            .filter(WorkLink.source_type == origin_type_value)
            .filter(WorkLink.link_type == link_type_value)
            .order_by(WorkLink.created_at)
            .first()
        )
        return row[0] if row else None

    @staticmethod
    def create_outcome(
        db: Session,
        *,
        work_order_id: UUID | str,
        outcome_type: WorkOutcomeType | str,
        status: WorkOutcomeStatus | str = WorkOutcomeStatus.pending,
        subscriber_id: UUID | str | None = None,
        external_system: str | None = None,
        external_reference: str | None = None,
        idempotency_key: str | None = None,
        payload: dict | None = None,
        error: str | None = None,
    ) -> WorkOutcome:
        work_order_uuid = coerce_uuid(work_order_id)
        if not db.get(WorkOrder, work_order_uuid):
            raise HTTPException(status_code=404, detail="Work order not found")

        if idempotency_key:
            existing = db.query(WorkOutcome).filter(WorkOutcome.idempotency_key == idempotency_key).one_or_none()
            if existing:
                return existing

        outcome = WorkOutcome(
            work_order_id=work_order_uuid,
            outcome_type=_coerce_outcome_type(outcome_type),
            status=_coerce_outcome_status(status),
            subscriber_id=coerce_uuid(subscriber_id) if subscriber_id else None,
            external_system=external_system,
            external_reference=external_reference,
            idempotency_key=idempotency_key,
            payload=payload,
            error=error,
        )
        db.add(outcome)
        db.flush()
        return outcome

    @staticmethod
    def record_work_order_completion(
        db: Session,
        work_order: WorkOrder,
        *,
        selfcare_notified: bool | None = None,
    ) -> WorkOutcome:
        subscriber = work_order.subscriber
        if subscriber is None and work_order.subscriber_id:
            subscriber = db.get(Subscriber, work_order.subscriber_id)

        external_system = None
        external_reference = None
        if subscriber and subscriber.external_system == "selfcare" and subscriber.external_id:
            external_system = "selfcare"
            external_reference = str(subscriber.external_id)

        outcome_type = _COMPLETION_OUTCOME_BY_WORK_TYPE.get(work_order.work_type, WorkOutcomeType.no_billing_change)
        if outcome_type == WorkOutcomeType.activation_requested and not external_reference:
            outcome_type = WorkOutcomeType.no_billing_change

        status = WorkOutcomeStatus.succeeded
        if external_system == "selfcare" and outcome_type != WorkOutcomeType.no_billing_change:
            status = WorkOutcomeStatus.succeeded if selfcare_notified else WorkOutcomeStatus.pending

        idempotency_key = f"work-order:{work_order.id}:completion"
        payload = {
            "work_order_id": str(work_order.id),
            "work_type": work_order.work_type.value if work_order.work_type else None,
            "completed_at": work_order.completed_at.isoformat() if work_order.completed_at else None,
            "selfcare_notified": selfcare_notified,
        }
        existing = db.query(WorkOutcome).filter(WorkOutcome.idempotency_key == idempotency_key).one_or_none()
        if existing:
            existing.outcome_type = outcome_type
            if existing.status != WorkOutcomeStatus.reconciled:
                existing.status = status
            existing.subscriber_id = work_order.subscriber_id
            existing.external_system = external_system
            existing.external_reference = external_reference
            existing.payload = {**(existing.payload or {}), **payload}
            existing.error = None if status == WorkOutcomeStatus.succeeded else existing.error
            return existing

        outcome = WorkOutcome(
            work_order_id=work_order.id,
            outcome_type=outcome_type,
            status=status,
            subscriber_id=work_order.subscriber_id,
            external_system=external_system,
            external_reference=external_reference,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        db.add(outcome)
        db.flush()
        return outcome

    @staticmethod
    def reconcile_pending_outcomes(db: Session, *, limit: int = 100) -> dict[str, int]:
        """Re-drive WorkOutcomes left ``pending`` by a failed dotmac_sub push.

        A completion records its outcome as ``pending`` when the sub notification
        failed at that moment. This re-sends the notification and flips the ones
        that now succeed to ``succeeded`` — idempotent via the completion key, so
        it updates the existing row rather than creating a new one. Commits per
        outcome so one failure doesn't lose the batch.
        """
        from app.services.workforce import _emit_work_order_to_sub

        outcomes = (
            db.query(WorkOutcome)
            .filter(WorkOutcome.status == WorkOutcomeStatus.pending)
            .filter(WorkOutcome.external_system == "selfcare")
            .order_by(WorkOutcome.created_at)
            .limit(limit)
            .all()
        )
        processed = 0
        healed = 0
        for outcome in outcomes:
            work_order = db.get(WorkOrder, outcome.work_order_id)
            if work_order is None:
                continue
            processed += 1
            try:
                notified = _emit_work_order_to_sub(db, work_order, "work_order.completed")
                WorkLifecycle.record_work_order_completion(db, work_order, selfcare_notified=notified)
                db.commit()
                if notified:
                    healed += 1
            except Exception:
                db.rollback()
                logger.exception("work_outcome_reconcile_error outcome_id=%s", outcome.id)
        logger.info("work_outcome_reconcile_done processed=%s healed=%s", processed, healed)
        return {"processed": processed, "healed": healed}

    @staticmethod
    def dangling_links(db: Session, *, limit: int = 100) -> list[dict[str, str]]:
        findings: list[dict[str, str]] = []
        links = db.query(WorkLink).order_by(WorkLink.created_at.desc()).limit(limit).all()
        for link in links:
            for side, entity_type, entity_id in (
                ("source", link.source_type, link.source_id),
                ("target", link.target_type, link.target_id),
            ):
                model = _ENTITY_MODELS.get(entity_type)
                if model is None:
                    continue
                entity = db.get(model, entity_id)
                if entity is None or getattr(entity, "is_active", True) is False:
                    findings.append(
                        {
                            "link_id": str(link.id),
                            "side": side,
                            "entity_type": entity_type.value,
                            "entity_id": str(entity_id),
                            "reason": "missing" if entity is None else "inactive",
                        }
                    )
        return findings


work_lifecycle = WorkLifecycle()
