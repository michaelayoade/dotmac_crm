"""Technician-scoped material request workflows for the field app."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models.material_request import MaterialRequest, MaterialRequestStatus
from app.models.workforce import WorkOrder, WorkOrderAssignment
from app.schemas.field import FieldMaterialRequestCreate
from app.schemas.material_request import MaterialRequestCreate
from app.services.common import apply_pagination, coerce_uuid, validate_enum
from app.services.field.jobs import caller_can_access, get_scoped_work_order
from app.services.material_requests import material_requests
from app.services.response import ListResponseMixin


def _assigned_work_order_ids(db: Session, person_id: UUID):
    member_ids = (
        db.query(WorkOrderAssignment.work_order_id).filter(WorkOrderAssignment.person_id == person_id).subquery()
    )
    return db.query(WorkOrder.id).filter(
        WorkOrder.is_active.is_(True),
        or_(
            WorkOrder.assigned_to_person_id == person_id,
            WorkOrder.id.in_(member_ids.select()),
        ),
    )


class FieldMaterialRequests(ListResponseMixin):
    @staticmethod
    def list_mine(
        db: Session,
        person_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[MaterialRequest]:
        person_uuid = coerce_uuid(person_id)
        query = (
            db.query(MaterialRequest)
            .options(selectinload(MaterialRequest.items))
            .filter(MaterialRequest.is_active.is_(True))
            .filter(
                or_(
                    MaterialRequest.requested_by_person_id == person_uuid,
                    MaterialRequest.work_order_id.in_(_assigned_work_order_ids(db, person_uuid).subquery().select()),
                )
            )
            .order_by(MaterialRequest.created_at.desc())
        )
        if status:
            query = query.filter(MaterialRequest.status == validate_enum(status, MaterialRequestStatus, "status"))
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def get_mine(db: Session, person_id: str, material_request_id: str) -> MaterialRequest:
        person_uuid = coerce_uuid(person_id)
        mr = material_requests.get(db, material_request_id)
        if mr.requested_by_person_id == person_uuid:
            return mr
        if mr.work_order_id:
            work_order = db.get(WorkOrder, mr.work_order_id)
            if work_order and caller_can_access(db, person_uuid, work_order):
                return mr
        raise HTTPException(status_code=404, detail="Material request not found")

    @staticmethod
    def create(
        db: Session,
        person_id: str,
        payload: FieldMaterialRequestCreate,
    ) -> MaterialRequest:
        work_order_id = payload.work_order_id
        ticket_id = payload.ticket_id
        project_id = payload.project_id

        if work_order_id:
            work_order = get_scoped_work_order(db, person_id, str(work_order_id))
            ticket_id = ticket_id or work_order.ticket_id
            project_id = project_id or work_order.project_id
        elif not (ticket_id or project_id):
            raise HTTPException(status_code=400, detail="Link a job, ticket, or project before requesting materials")

        create_payload = MaterialRequestCreate(
            ticket_id=ticket_id,
            project_id=project_id,
            work_order_id=work_order_id,
            requested_by_person_id=coerce_uuid(person_id),
            priority=payload.priority,
            notes=payload.notes,
            source_location_id=payload.source_location_id,
            destination_location_id=payload.destination_location_id,
            items=payload.items,
        )
        return material_requests.create(db, create_payload)

    @staticmethod
    def submit(db: Session, person_id: str, material_request_id: str) -> MaterialRequest:
        mr = FieldMaterialRequests.get_mine(db, person_id, material_request_id)
        if mr.requested_by_person_id != coerce_uuid(person_id):
            raise HTTPException(status_code=404, detail="Material request not found")
        return material_requests.submit(db, str(mr.id))


field_material_requests = FieldMaterialRequests()
