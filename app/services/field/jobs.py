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
from app.models.material_request import MaterialRequest
from app.models.person import Person
from app.models.subscriber import Subscriber
from app.models.tickets import Ticket, TicketStatus
from app.models.timecost import WorkLog
from app.models.workforce import WorkOrder, WorkOrderAssignment, WorkOrderStatus
from app.services.common import apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin

_TERMINAL_TICKET_STATUSES = (TicketStatus.closed, TicketStatus.canceled, TicketStatus.merged)

_OPEN_STATUSES = (
    WorkOrderStatus.scheduled,
    WorkOrderStatus.dispatched,
    WorkOrderStatus.in_progress,
)


def _assignment_member_subquery(db: Session, person_id: UUID):
    return db.query(WorkOrderAssignment.work_order_id).filter(WorkOrderAssignment.person_id == person_id).subquery()


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


def _best_phone(person: Person | None) -> str | None:
    """A reachable number for the tech: person.phone, else a phone-type channel."""
    if person is None:
        return None
    if isinstance(person.phone, str) and person.phone.strip():
        return person.phone.strip()
    from app.services.person import PHONE_CHANNEL_TYPES

    for channel in person.channels or []:
        if channel.channel_type in PHONE_CHANNEL_TYPES and isinstance(channel.address, str) and channel.address.strip():
            return channel.address.strip()
    return None


def _site_address(subscriber: Subscriber, person: Person | None) -> str | None:
    """The service/site address, falling back to the person's address so a
    thin/migrated subscriber record never leaves the tech without somewhere to go."""
    service_parts = [
        subscriber.service_address_line1,
        subscriber.service_address_line2,
        subscriber.service_city,
        subscriber.service_region,
        subscriber.service_postal_code,
    ]
    text = ", ".join(part for part in service_parts if part) or None
    if text:
        return text
    if person is not None:
        person_parts = [
            person.address_line1,
            person.address_line2,
            person.city,
            person.region,
            person.postal_code,
        ]
        return ", ".join(part for part in person_parts if part) or None
    return None


def _customer_payload(work_order: WorkOrder) -> dict | None:
    subscriber = work_order.subscriber
    if not subscriber:
        return None
    person: Person | None = subscriber.person
    return {
        "subscriber_id": subscriber.id,
        "name": (person.display_name or f"{person.first_name} {person.last_name}".strip()) if person else None,
        "phone": _best_phone(person),
        "email": person.email if person else None,
        "address_text": _site_address(subscriber, person),
        "service_plan": subscriber.service_plan,
        "account_number": subscriber.account_number,
        "status": subscriber.status.value if getattr(subscriber, "status", None) else None,
    }


def _additional_contacts(subscriber: Subscriber) -> list[dict]:
    """Other people on the same account (org), so the tech has a site/secondary
    contact — not just the primary. Empty for residential (no org)."""
    person: Person | None = subscriber.person
    org = person.organization if person and person.organization_id else None
    if org is None:
        return []
    out: list[dict] = []
    for other in org.people or []:
        if person is not None and other.id == person.id:
            continue
        if not other.is_active:
            continue
        out.append(
            {
                "name": other.display_name or f"{other.first_name} {other.last_name}".strip(),
                "phone": _best_phone(other),
                "email": other.email,
                "relationship": other.party_status.value if getattr(other, "party_status", None) else None,
            }
        )
        if len(out) >= 5:
            break
    return out


def _recent_visits(db: Session, subscriber: Subscriber, exclude_id) -> list[dict]:
    """Prior completed work orders at this account — context to avoid repeat truck-rolls."""
    rows = (
        db.query(WorkOrder)
        .filter(
            WorkOrder.subscriber_id == subscriber.id,
            WorkOrder.status == WorkOrderStatus.completed,
            WorkOrder.id != exclude_id,
        )
        .order_by(WorkOrder.completed_at.desc().nullslast())
        .limit(5)
        .all()
    )
    return [
        {
            "work_order_id": w.id,
            "title": w.title,
            "work_type": w.work_type.value if w.work_type else None,
            "status": w.status.value if w.status else None,
            "completed_at": w.completed_at,
        }
        for w in rows
    ]


def _open_tickets(db: Session, subscriber: Subscriber) -> list[dict]:
    """Other open tickets for this account, so the tech can address them on site."""
    rows = (
        db.query(Ticket)
        .filter(
            Ticket.subscriber_id == subscriber.id,
            Ticket.is_active.is_(True),
            Ticket.status.notin_(_TERMINAL_TICKET_STATUSES),
        )
        .order_by(Ticket.created_at.desc())
        .limit(5)
        .all()
    )
    return [
        {
            "id": t.id,
            "ref": t.number or str(t.id),
            "subject": getattr(t, "title", None),
            "status": t.status.value if t.status else None,
        }
        for t in rows
    ]


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
        material_request_filters = [MaterialRequest.work_order_id == work_order.id]
        if work_order.ticket_id:
            material_request_filters.append(MaterialRequest.ticket_id == work_order.ticket_id)
        if work_order.project_id:
            material_request_filters.append(MaterialRequest.project_id == work_order.project_id)
        material_requests = (
            db.query(MaterialRequest)
            .options(selectinload(MaterialRequest.items))
            .filter(MaterialRequest.is_active.is_(True))
            .filter(or_(*material_request_filters))
            .order_by(MaterialRequest.created_at.desc())
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

        subscriber = work_order.subscriber
        return {
            "work_order": work_order,
            "customer": _customer_payload(work_order),
            "location": resolve_job_location(db, work_order),
            "ticket_ref": (ticket.number or str(ticket.id)) if ticket else None,
            "project_id": work_order.project_id,
            "access_notes": work_order.access_notes,
            "additional_contacts": _additional_contacts(subscriber) if subscriber else [],
            "recent_visits": _recent_visits(db, subscriber, work_order.id) if subscriber else [],
            "open_tickets": _open_tickets(db, subscriber) if subscriber else [],
            "notes": notes,
            "attachments": attachments,
            "materials": materials,
            "material_requests": material_requests,
            "worklogs": worklogs,
        }

    @staticmethod
    def update_location(
        db: Session,
        person_id: str,
        work_order_id: str,
        *,
        latitude: float,
        longitude: float,
    ) -> dict:
        work_order = get_scoped_work_order(db, person_id, work_order_id)
        from app.services.field.location import update_job_location

        return update_job_location(db, work_order, latitude=latitude, longitude=longitude)


field_jobs = FieldJobs()
