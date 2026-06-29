from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.work_lifecycle import (
    WorkEntityType,
    WorkLink,
    WorkLinkRelationship,
    WorkOutcome,
    WorkOutcomeStatus,
    WorkOutcomeType,
)
from app.models.workforce import WorkOrder
from app.services.common import coerce_uuid


def _coerce_entity_type(value: WorkEntityType | str, label: str) -> WorkEntityType:
    if isinstance(value, WorkEntityType):
        return value
    try:
        return WorkEntityType(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}") from exc


def _coerce_relationship(value: WorkLinkRelationship | str) -> WorkLinkRelationship:
    if isinstance(value, WorkLinkRelationship):
        return value
    try:
        return WorkLinkRelationship(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid relationship") from exc


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
        relationship: WorkLinkRelationship | str,
        contract_name: str | None = None,
        created_by_person_id: UUID | str | None = None,
        metadata: dict | None = None,
    ) -> WorkLink:
        source_type_value = _coerce_entity_type(source_type, "source type")
        target_type_value = _coerce_entity_type(target_type, "target type")
        relationship_value = _coerce_relationship(relationship)
        source_uuid = coerce_uuid(source_id)
        target_uuid = coerce_uuid(target_id)

        existing = (
            db.query(WorkLink)
            .filter(WorkLink.source_type == source_type_value)
            .filter(WorkLink.source_id == source_uuid)
            .filter(WorkLink.target_type == target_type_value)
            .filter(WorkLink.target_id == target_uuid)
            .filter(WorkLink.relationship == relationship_value)
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
            relationship=relationship_value,
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
            relationship=WorkLinkRelationship.originated,
            contract_name=contract_name,
            created_by_person_id=created_by_person_id,
            metadata=metadata,
        )

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


work_lifecycle = WorkLifecycle()
