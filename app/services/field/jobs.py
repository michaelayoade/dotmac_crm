"""Technician-scoped job views for the field app.

Row scoping lives here, not in routes: a technician sees a work order only
when they are the assigned technician or an assignment-table member. Cost and
rate data never leaves this layer — schemas in app/schemas/field.py expose no
hourly_rate or cost fields.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.dispatch import TechnicianProfile
from app.models.field import FieldAttachment
from app.models.inventory import WorkOrderMaterial
from app.models.person import Person
from app.models.subscriber import Subscriber
from app.models.timecost import WorkLog
from app.models.workforce import WorkOrder, WorkOrderAssignment, WorkOrderStatus
from app.services.common import apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin

_OPEN_STATUSES = (
    WorkOrderStatus.scheduled,
    WorkOrderStatus.dispatched,
    WorkOrderStatus.in_progress,
)


def _assignment_member_subquery(db: Session, person_id: UUID):
    return (
        db.query(WorkOrderAssignment.work_order_id)
        .filter(WorkOrderAssignment.person_id == person_id)
        .subquery()
    )


def _scoped_query(db: Session, person_id: UUID):
    member_ids = _assignment_member_subquery(db, person_id)
    return (
        db.query(WorkOrder)
        .filter(WorkOrder.is_active.is_(True))
        .filter(
            or_(
                WorkOrder.assigned_to_person_id == person_id,
                WorkOrder.id.in_(member_ids.select()),
            )
        )
    )


def caller_can_access(db: Session, person_id: str | UUID, work_order: WorkOrder) -> bool:
    person_uuid = coerce_uuid(str(person_id))
    if work_order.assigned_to_person_id == person_uuid:
        return True
    return (
        db.query(WorkOrderAssignment)
        .filter(WorkOrderAssignment.work_order_id == work_order.id)
        .filter(WorkOrderAssignment.person_id == person_uuid)
        .first()
        is not None
    )


def get_scoped_work_order(db: Session, person_id: str | UUID, work_order_id: str) -> WorkOrder:
    """Fetch a work order the caller is assigned to; 404 otherwise.

    Unassigned callers get the same 404 as a missing id so job existence
    does not leak across technicians.
    """
    work_order = db.get(WorkOrder, coerce_uuid(work_order_id))
    if not work_order or not work_order.is_active or not caller_can_access(db, person_id, work_order):
        raise HTTPException(status_code=404, detail="Job not found")
    return work_order


def _customer_payload(work_order: WorkOrder) -> dict | None:
    subscriber = work_order.subscriber
    if not subscriber:
        return None
    person: Person | None = subscriber.person
    address_parts = [
        subscriber.service_address_line1,
        subscriber.service_address_line2,
        subscriber.service_city,
        subscriber.service_region,
        subscriber.service_postal_code,
    ]
    address_text = ", ".join(part for part in address_parts if part) or None
    return {
        "subscriber_id": subscriber.id,
        "name": (person.display_name or f"{person.first_name} {person.last_name}".strip()) if person else None,
        "phone": person.phone if person else None,
        "email": person.email if person else None,
        "address_text": address_text,
        "service_plan": subscriber.service_plan,
        "account_number": subscriber.account_number,
        "status": subscriber.status.value if getattr(subscriber, "status", None) else None,
    }


class FieldJobs(ListResponseMixin):
    @staticmethod
    def me(db: Session, person_id: str) -> dict:
        person_uuid = coerce_uuid(person_id)
        person = db.get(Person, person_uuid)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        profile = (
            db.query(TechnicianProfile)
            .filter(TechnicianProfile.person_id == person_uuid)
            .filter(TechnicianProfile.is_active.is_(True))
            .first()
        )
        today = datetime.now(UTC).date()
        scoped = _scoped_query(db, person_uuid)
        jobs_today = [
            wo
            for wo in scoped.filter(WorkOrder.status.in_(_OPEN_STATUSES)).all()
            if wo.scheduled_start is None or wo.scheduled_start.date() <= today
        ]
        completed_today = (
            scoped.filter(WorkOrder.status == WorkOrderStatus.completed)
            .filter(WorkOrder.completed_at.isnot(None))
            .all()
        )
        completed_today = [wo for wo in completed_today if wo.completed_at and wo.completed_at.date() == today]
        return {
            "person_id": person.id,
            "name": person.display_name or f"{person.first_name} {person.last_name}".strip(),
            "email": person.email,
            "technician_title": profile.title if profile else None,
            "region": profile.region if profile else None,
            "open_jobs": len(jobs_today),
            "completed_today": len(completed_today),
        }

    @staticmethod
    def list(
        db: Session,
        person_id: str,
        *,
        status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WorkOrder]:
        person_uuid = coerce_uuid(person_id)
        query = _scoped_query(db, person_uuid).options(
            joinedload(WorkOrder.subscriber).joinedload(Subscriber.person),
        )
        if status:
            query = query.filter(WorkOrder.status == validate_enum(status, WorkOrderStatus, "status"))
        if date_from:
            query = query.filter(or_(WorkOrder.scheduled_start.is_(None), WorkOrder.scheduled_start >= date_from))
        if date_to:
            query = query.filter(or_(WorkOrder.scheduled_start.is_(None), WorkOrder.scheduled_start <= date_to))
        query = query.order_by(WorkOrder.scheduled_start.asc().nullslast(), WorkOrder.created_at.asc())
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def get_detail(db: Session, person_id: str, work_order_id: str) -> dict:
        work_order = get_scoped_work_order(db, person_id, work_order_id)
        # Eager-load the bundle relations in bulk (no N+1 in the loops below).
        notes = sorted(work_order.notes, key=lambda n: n.created_at, reverse=True)
        attachments = (
            db.query(FieldAttachment)
            .filter(FieldAttachment.work_order_id == work_order.id)
            .filter(FieldAttachment.is_active.is_(True))
            .order_by(FieldAttachment.created_at.desc())
            .all()
        )
        materials = (
            db.query(WorkOrderMaterial)
            .options(selectinload(WorkOrderMaterial.item))
            .filter(WorkOrderMaterial.work_order_id == work_order.id)
            .all()
        )
        worklogs = (
            db.query(WorkLog)
            .filter(WorkLog.work_order_id == work_order.id)
            .filter(WorkLog.is_active.is_(True))
            .order_by(WorkLog.start_at.desc())
            .all()
        )
        ticket = work_order.ticket
        # Resolve lazily on detail view so assignment flows never block on the
        # geocoder; results are cached on the work order after the first call.
        from app.services.field.location import resolve_job_location

        return {
            "work_order": work_order,
            "customer": _customer_payload(work_order),
            "location": resolve_job_location(db, work_order),
            "ticket_ref": (ticket.number or str(ticket.id)) if ticket else None,
            "project_id": work_order.project_id,
            "notes": notes,
            "attachments": attachments,
            "materials": materials,
            "worklogs": worklogs,
        }


field_jobs = FieldJobs()
