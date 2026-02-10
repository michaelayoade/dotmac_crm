from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.projects import Project
from app.models.tickets import Ticket
from app.models.workforce import (
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderNote,
    WorkOrderStatus,
)
from app.queries.workforce import (
    WorkOrderAssignmentQuery,
    WorkOrderNoteQuery,
    WorkOrderQuery,
)
from app.schemas.workforce import (
    WorkOrderAssignmentCreate,
    WorkOrderAssignmentUpdate,
    WorkOrderCreate,
    WorkOrderNoteCreate,
    WorkOrderNoteUpdate,
    WorkOrderUpdate,
)
from app.services.common import (
    coerce_uuid,
)
from app.services.events import emit_event
from app.services.events.types import EventType
from app.services.response import ListResponseMixin


def _ensure_person(db: Session, person_id: str):
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")


def _ensure_ticket(db: Session, ticket_id: str):
    ticket = db.get(Ticket, coerce_uuid(ticket_id))
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")


def _ensure_project(db: Session, project_id: str):
    project = db.get(Project, coerce_uuid(project_id))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")


class WorkOrders(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: WorkOrderCreate):
        if payload.ticket_id:
            _ensure_ticket(db, str(payload.ticket_id))
        if payload.project_id:
            _ensure_project(db, str(payload.project_id))
        if payload.assigned_to_person_id:
            _ensure_person(db, str(payload.assigned_to_person_id))
        work_order = WorkOrder(**payload.model_dump())
        db.add(work_order)
        db.commit()
        db.refresh(work_order)

        # Emit work order created event
        emit_event(
            db,
            EventType.work_order_created,
            {
                "work_order_id": str(work_order.id),
                "title": work_order.title,
                "status": work_order.status.value if work_order.status else None,
                "work_type": work_order.work_type.value if work_order.work_type else None,
                "project_id": str(work_order.project_id) if work_order.project_id else None,
                "ticket_id": str(work_order.ticket_id) if work_order.ticket_id else None,
            },
            work_order_id=work_order.id,
            project_id=work_order.project_id,
            ticket_id=work_order.ticket_id,
        )

        return work_order

    @staticmethod
    def get(db: Session, work_order_id: str):
        work_order = db.get(WorkOrder, work_order_id)
        if not work_order:
            raise HTTPException(status_code=404, detail="Work order not found")
        return work_order

    @staticmethod
    def list(
        db: Session,
        subscriber_id: str | None,
        ticket_id: str | None,
        project_id: str | None,
        assigned_to_person_id: str | None,
        status: str | None,
        priority: str | None,
        work_type: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = (
            WorkOrderQuery(db)
            .by_subscriber(subscriber_id)
            .by_ticket(ticket_id)
            .by_project(project_id)
            .by_assigned_to(assigned_to_person_id)
            .by_status(status)
            .by_priority(priority)
            .by_work_type(work_type)
        )
        if is_active is None:
            query = query.active_only()
        elif is_active:
            query = query.active_only(True)
        else:
            query = query.active_only(False)

        return (
            query
            .order_by(order_by, order_dir)
            .paginate(limit, offset)
            .all()
        )

    @staticmethod
    def update(db: Session, work_order_id: str, payload: WorkOrderUpdate):
        work_order = db.get(WorkOrder, work_order_id)
        if not work_order:
            raise HTTPException(status_code=404, detail="Work order not found")
        previous_status = work_order.status
        data = payload.model_dump(exclude_unset=True)
        if data.get("ticket_id"):
            _ensure_ticket(db, str(data["ticket_id"]))
        if data.get("project_id"):
            _ensure_project(db, str(data["project_id"]))
        if data.get("assigned_to_person_id"):
            _ensure_person(db, str(data["assigned_to_person_id"]))
        for key, value in data.items():
            setattr(work_order, key, value)
        db.commit()
        db.refresh(work_order)

        # Emit events based on status changes
        new_status = work_order.status
        event_payload: dict[str, object] = {
            "work_order_id": str(work_order.id),
            "title": work_order.title,
            "from_status": previous_status.value if previous_status else None,
            "to_status": new_status.value if new_status else None,
            "project_id": str(work_order.project_id) if work_order.project_id else None,
            "ticket_id": str(work_order.ticket_id) if work_order.ticket_id else None,
        }

        if new_status == WorkOrderStatus.dispatched and previous_status != WorkOrderStatus.dispatched:
            emit_event(
                db,
                EventType.work_order_dispatched,
                event_payload,
                work_order_id=work_order.id,
                project_id=work_order.project_id,
                ticket_id=work_order.ticket_id,
            )
        elif new_status == WorkOrderStatus.completed and previous_status != WorkOrderStatus.completed:
            emit_event(
                db,
                EventType.work_order_completed,
                event_payload,
                work_order_id=work_order.id,
                project_id=work_order.project_id,
                ticket_id=work_order.ticket_id,
            )
        elif new_status == WorkOrderStatus.canceled and previous_status != WorkOrderStatus.canceled:
            emit_event(
                db,
                EventType.work_order_canceled,
                event_payload,
                work_order_id=work_order.id,
                project_id=work_order.project_id,
                ticket_id=work_order.ticket_id,
            )
        elif previous_status != new_status or len(data) > 1:
            event_payload["changed_fields"] = list(data.keys())
            emit_event(
                db,
                EventType.work_order_updated,
                event_payload,
                work_order_id=work_order.id,
                project_id=work_order.project_id,
                ticket_id=work_order.ticket_id,
            )

        return work_order

    @staticmethod
    def delete(db: Session, work_order_id: str):
        work_order = db.get(WorkOrder, work_order_id)
        if not work_order:
            raise HTTPException(status_code=404, detail="Work order not found")
        work_order.is_active = False
        db.commit()


class WorkOrderAssignments(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: WorkOrderAssignmentCreate):
        work_order = db.get(WorkOrder, payload.work_order_id)
        if not work_order:
            raise HTTPException(status_code=404, detail="Work order not found")
        _ensure_person(db, str(payload.person_id))
        if payload.is_primary:
            db.query(WorkOrderAssignment).filter(
                WorkOrderAssignment.work_order_id == payload.work_order_id,
                WorkOrderAssignment.is_primary.is_(True),
            ).update({"is_primary": False})
        assignment = WorkOrderAssignment(**payload.model_dump())
        db.add(assignment)
        db.commit()
        db.refresh(assignment)
        return assignment

    @staticmethod
    def get(db: Session, assignment_id: str):
        assignment = db.get(WorkOrderAssignment, assignment_id)
        if not assignment:
            raise HTTPException(status_code=404, detail="Work order assignment not found")
        return assignment

    @staticmethod
    def list(
        db: Session,
        work_order_id: str | None,
        person_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        return (
            WorkOrderAssignmentQuery(db)
            .by_work_order(work_order_id)
            .by_person(person_id)
            .order_by(order_by, order_dir)
            .paginate(limit, offset)
            .all()
        )

    @staticmethod
    def update(db: Session, assignment_id: str, payload: WorkOrderAssignmentUpdate):
        assignment = db.get(WorkOrderAssignment, assignment_id)
        if not assignment:
            raise HTTPException(status_code=404, detail="Work order assignment not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("is_primary"):
            db.query(WorkOrderAssignment).filter(
                WorkOrderAssignment.work_order_id == assignment.work_order_id,
                WorkOrderAssignment.id != assignment.id,
                WorkOrderAssignment.is_primary.is_(True),
            ).update({"is_primary": False})
        for key, value in data.items():
            setattr(assignment, key, value)
        db.commit()
        db.refresh(assignment)
        return assignment

    @staticmethod
    def delete(db: Session, assignment_id: str):
        assignment = db.get(WorkOrderAssignment, assignment_id)
        if not assignment:
            raise HTTPException(status_code=404, detail="Work order assignment not found")
        db.delete(assignment)
        db.commit()


class WorkOrderNotes(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: WorkOrderNoteCreate):
        work_order = db.get(WorkOrder, payload.work_order_id)
        if not work_order:
            raise HTTPException(status_code=404, detail="Work order not found")
        if payload.author_person_id:
            _ensure_person(db, str(payload.author_person_id))
        note = WorkOrderNote(**payload.model_dump())
        db.add(note)
        db.commit()
        db.refresh(note)
        return note

    @staticmethod
    def get(db: Session, note_id: str):
        note = db.get(WorkOrderNote, note_id)
        if not note:
            raise HTTPException(status_code=404, detail="Work order note not found")
        return note

    @staticmethod
    def list(
        db: Session,
        work_order_id: str | None,
        is_internal: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        return (
            WorkOrderNoteQuery(db)
            .by_work_order(work_order_id)
            .is_internal(is_internal)
            .order_by(order_by, order_dir)
            .paginate(limit, offset)
            .all()
        )

    @staticmethod
    def update(db: Session, note_id: str, payload: WorkOrderNoteUpdate):
        note = db.get(WorkOrderNote, note_id)
        if not note:
            raise HTTPException(status_code=404, detail="Work order note not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(note, key, value)
        db.commit()
        db.refresh(note)
        return note

    @staticmethod
    def delete(db: Session, note_id: str):
        note = db.get(WorkOrderNote, note_id)
        if not note:
            raise HTTPException(status_code=404, detail="Work order note not found")
        db.delete(note)
        db.commit()


work_orders = WorkOrders()
work_order_assignments = WorkOrderAssignments()
work_order_notes = WorkOrderNotes()
