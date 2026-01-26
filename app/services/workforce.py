from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.catalog import Subscription
from app.models.person import Person
from app.models.projects import Project
from app.models.provisioning import ServiceOrder
from app.models.subscriber import Address, SubscriberAccount
from app.models.tickets import Ticket
from app.models.workforce import (
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderNote,
    WorkOrderPriority,
    WorkOrderStatus,
    WorkOrderType,
)
from app.services.common import (
    apply_ordering,
    apply_pagination,
    coerce_uuid,
    ensure_exists,
    validate_enum,
)
from app.services.response import ListResponseMixin
from app.schemas.workforce import (
    WorkOrderAssignmentCreate,
    WorkOrderAssignmentUpdate,
    WorkOrderCreate,
    WorkOrderNoteCreate,
    WorkOrderNoteUpdate,
    WorkOrderUpdate,
)


def _ensure_person(db: Session, person_id: str):
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")


def _ensure_account(db: Session, account_id: str):
    account = db.get(SubscriberAccount, coerce_uuid(account_id))
    if not account:
        raise HTTPException(status_code=404, detail="Subscriber account not found")


def _ensure_subscription(db: Session, subscription_id: str):
    subscription = db.get(Subscription, coerce_uuid(subscription_id))
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")


def _ensure_service_order(db: Session, service_order_id: str):
    order = db.get(ServiceOrder, coerce_uuid(service_order_id))
    if not order:
        raise HTTPException(status_code=404, detail="Service order not found")


def _ensure_ticket(db: Session, ticket_id: str):
    ticket = db.get(Ticket, coerce_uuid(ticket_id))
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")


def _ensure_project(db: Session, project_id: str):
    project = db.get(Project, coerce_uuid(project_id))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")


def _ensure_address(db: Session, address_id: str):
    address = db.get(Address, coerce_uuid(address_id))
    if not address:
        raise HTTPException(status_code=404, detail="Address not found")


class WorkOrders(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: WorkOrderCreate):
        if payload.account_id:
            _ensure_account(db, str(payload.account_id))
        if payload.subscription_id:
            _ensure_subscription(db, str(payload.subscription_id))
        if payload.service_order_id:
            _ensure_service_order(db, str(payload.service_order_id))
        if payload.ticket_id:
            _ensure_ticket(db, str(payload.ticket_id))
        if payload.project_id:
            _ensure_project(db, str(payload.project_id))
        if payload.address_id:
            _ensure_address(db, str(payload.address_id))
        if payload.assigned_to_person_id:
            _ensure_person(db, str(payload.assigned_to_person_id))
        work_order = WorkOrder(**payload.model_dump())
        db.add(work_order)
        db.commit()
        db.refresh(work_order)
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
        account_id: str | None,
        subscription_id: str | None,
        service_order_id: str | None,
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
        query = db.query(WorkOrder)
        if account_id:
            query = query.filter(WorkOrder.account_id == account_id)
        if subscription_id:
            query = query.filter(WorkOrder.subscription_id == subscription_id)
        if service_order_id:
            query = query.filter(WorkOrder.service_order_id == service_order_id)
        if ticket_id:
            query = query.filter(WorkOrder.ticket_id == ticket_id)
        if project_id:
            query = query.filter(WorkOrder.project_id == project_id)
        if assigned_to_person_id:
            query = query.filter(WorkOrder.assigned_to_person_id == assigned_to_person_id)
        if status:
            query = query.filter(
                WorkOrder.status == validate_enum(status, WorkOrderStatus, "status")
            )
        if priority:
            query = query.filter(
                WorkOrder.priority
                == validate_enum(priority, WorkOrderPriority, "priority")
            )
        if work_type:
            query = query.filter(
                WorkOrder.work_type
                == validate_enum(work_type, WorkOrderType, "work_type")
            )
        if is_active is None:
            query = query.filter(WorkOrder.is_active.is_(True))
        else:
            query = query.filter(WorkOrder.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": WorkOrder.created_at, "status": WorkOrder.status},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, work_order_id: str, payload: WorkOrderUpdate):
        work_order = db.get(WorkOrder, work_order_id)
        if not work_order:
            raise HTTPException(status_code=404, detail="Work order not found")
        data = payload.model_dump(exclude_unset=True)
        if "account_id" in data and data["account_id"]:
            _ensure_account(db, str(data["account_id"]))
        if "subscription_id" in data and data["subscription_id"]:
            _ensure_subscription(db, str(data["subscription_id"]))
        if "service_order_id" in data and data["service_order_id"]:
            _ensure_service_order(db, str(data["service_order_id"]))
        if "ticket_id" in data and data["ticket_id"]:
            _ensure_ticket(db, str(data["ticket_id"]))
        if "project_id" in data and data["project_id"]:
            _ensure_project(db, str(data["project_id"]))
        if "address_id" in data and data["address_id"]:
            _ensure_address(db, str(data["address_id"]))
        if "assigned_to_person_id" in data and data["assigned_to_person_id"]:
            _ensure_person(db, str(data["assigned_to_person_id"]))
        for key, value in data.items():
            setattr(work_order, key, value)
        db.commit()
        db.refresh(work_order)
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
        query = db.query(WorkOrderAssignment)
        if work_order_id:
            query = query.filter(WorkOrderAssignment.work_order_id == work_order_id)
        if person_id:
            query = query.filter(WorkOrderAssignment.person_id == person_id)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"assigned_at": WorkOrderAssignment.assigned_at},
        )
        return apply_pagination(query, limit, offset).all()

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
        query = db.query(WorkOrderNote)
        if work_order_id:
            query = query.filter(WorkOrderNote.work_order_id == work_order_id)
        if is_internal is not None:
            query = query.filter(WorkOrderNote.is_internal == is_internal)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": WorkOrderNote.created_at},
        )
        return apply_pagination(query, limit, offset).all()

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
