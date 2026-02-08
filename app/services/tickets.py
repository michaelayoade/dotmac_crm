import builtins

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.crm.sales import Lead
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.tickets import (
    Ticket,
    TicketComment,
    TicketPriority,
    TicketSlaEvent,
    TicketStatus,
)
from app.models.workforce import WorkOrder, WorkOrderPriority, WorkOrderStatus, WorkOrderType
from app.queries.tickets import TicketCommentQuery, TicketQuery, TicketSlaEventQuery
from app.schemas.tickets import (
    TicketCommentBulkCreateRequest,
    TicketCommentCreate,
    TicketCommentUpdate,
    TicketCreate,
    TicketSlaEventCreate,
    TicketSlaEventUpdate,
    TicketUpdate,
)
from app.services.common import (
    coerce_uuid,
)
from app.services.events import emit_event
from app.services.events.types import EventType
from app.services.numbering import generate_number
from app.services.response import ListResponseMixin


def _ensure_person(db: Session, person_id: str):
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")


def _ensure_lead(db: Session, lead_id: str):
    lead = db.get(Lead, coerce_uuid(lead_id))
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")


def _has_field_visit_tag(tags: list | None) -> bool:
    """Check if tags contain 'field_visit'."""
    if not tags:
        return False
    return "field_visit" in tags


def _auto_create_work_order_for_ticket(db: Session, ticket: Ticket) -> WorkOrder | None:
    """Auto-create a work order when a ticket has field_visit tag.

    Returns the existing work order if one already exists, or creates a new one.
    """
    # Check if work order already exists for this ticket
    existing = (
        db.query(WorkOrder).filter(WorkOrder.ticket_id == ticket.id).filter(WorkOrder.is_active.is_(True)).first()
    )
    if existing:
        return existing

    # Map ticket priority to work order priority
    priority_map = {
        TicketPriority.lower: WorkOrderPriority.lower,
        TicketPriority.low: WorkOrderPriority.low,
        TicketPriority.medium: WorkOrderPriority.medium,
        TicketPriority.normal: WorkOrderPriority.normal,
        TicketPriority.high: WorkOrderPriority.high,
        TicketPriority.urgent: WorkOrderPriority.urgent,
    }
    wo_priority = priority_map.get(ticket.priority, WorkOrderPriority.normal)

    # Truncate title if needed
    title_prefix = "Field Visit - "
    max_title_len = 200 - len(title_prefix)
    ticket_title = (ticket.title or "")[:max_title_len]

    work_order = WorkOrder(
        title=f"{title_prefix}{ticket_title}",
        work_type=WorkOrderType.repair,
        status=WorkOrderStatus.draft,
        priority=wo_priority,
        subscriber_id=ticket.subscriber_id,
        ticket_id=ticket.id,
    )
    db.add(work_order)
    return work_order


def _resolve_customer_name(ticket: Ticket, db: Session) -> str | None:
    if ticket.customer:
        return ticket.customer.display_name or ticket.customer.email
    if ticket.subscriber and ticket.subscriber.person:
        person = ticket.subscriber.person
        return person.display_name or person.email
    if ticket.lead_id:
        lead = db.get(Lead, ticket.lead_id)
        if lead and lead.person:
            return lead.person.display_name or lead.person.email
    return None


def _resolve_customer_email(ticket: Ticket, db: Session) -> str | None:
    if ticket.customer and ticket.customer.email:
        email = ticket.customer.email
        if isinstance(email, str) and email.strip():
            return email.strip()
    if ticket.subscriber and ticket.subscriber.person:
        email = ticket.subscriber.person.email
        if isinstance(email, str) and email.strip():
            return email.strip()
    if ticket.lead_id:
        lead = db.get(Lead, ticket.lead_id)
        if lead and lead.person and lead.person.email:
            email = lead.person.email
            if isinstance(email, str) and email.strip():
                return email.strip()
    return None


def _resolve_technician_contact(db: Session, person_id) -> dict | None:
    if not person_id:
        return None
    technician = db.get(Person, person_id)
    if not technician:
        return None
    name = (
        technician.display_name or f"{technician.first_name or ''} {technician.last_name or ''}".strip() or "Technician"
    )
    email: str | None = technician.email if isinstance(technician.email, str) else None
    email = email.strip() if email else None
    return {
        "name": name,
        "email": email,
    }


class Tickets(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: TicketCreate):
        if payload.created_by_person_id:
            _ensure_person(db, str(payload.created_by_person_id))
        if payload.assigned_to_person_id:
            _ensure_person(db, str(payload.assigned_to_person_id))
        if payload.lead_id:
            _ensure_lead(db, str(payload.lead_id))
        if payload.customer_person_id:
            _ensure_person(db, str(payload.customer_person_id))

        from app.services.ticket_validation import validate_ticket_creation

        validate_ticket_creation(db, payload)

        data = payload.model_dump()
        number = generate_number(
            db=db,
            domain=SettingDomain.numbering,
            sequence_key="ticket_number",
            enabled_key="ticket_number_enabled",
            prefix_key="ticket_number_prefix",
            padding_key="ticket_number_padding",
            start_key="ticket_number_start",
        )
        if number:
            data["number"] = number
        ticket = Ticket(**data)
        db.add(ticket)
        db.flush()  # Get ticket.id before creating work order

        # Auto-create work order if field_visit tag is present
        if _has_field_visit_tag(payload.tags):
            _auto_create_work_order_for_ticket(db, ticket)

        db.commit()
        db.refresh(ticket)

        customer_name = _resolve_customer_name(ticket, db)
        customer_email = _resolve_customer_email(ticket, db)

        # Emit ticket.created event
        emit_event(
            db,
            EventType.ticket_created,
            {
                "ticket_id": str(ticket.id),
                "title": ticket.title,
                "subject": ticket.title,
                "status": ticket.status.value if ticket.status else None,
                "priority": ticket.priority.value if ticket.priority else None,
                "channel": ticket.channel.value if ticket.channel else None,
                "customer_name": customer_name,
                "email": customer_email,
                "doc": {
                    "custom_customer_name": customer_name,
                    "name": str(ticket.id),
                    "subject": ticket.title,
                    "status": ticket.status.value if ticket.status else None,
                },
            },
            ticket_id=ticket.id,
            subscriber_id=ticket.subscriber_id,
        )

        technician_contact = _resolve_technician_contact(db, ticket.assigned_to_person_id)
        if technician_contact and technician_contact.get("email"):
            emit_event(
                db,
                EventType.ticket_assigned,
                {
                    "ticket_id": str(ticket.id),
                    "title": ticket.title,
                    "subject": ticket.title,
                    "status": ticket.status.value if ticket.status else None,
                    "priority": ticket.priority.value if ticket.priority else None,
                    "channel": ticket.channel.value if ticket.channel else None,
                    "customer_name": customer_name,
                    "technician_name": technician_contact["name"],
                    "email": technician_contact["email"],
                    "technician_email": technician_contact["email"],
                    "technician_doc": {
                        "custom_customer_name": technician_contact["name"],
                        "name": str(ticket.id),
                        "subject": ticket.title,
                        "status": ticket.status.value if ticket.status else None,
                    },
                },
                ticket_id=ticket.id,
                subscriber_id=ticket.subscriber_id,
            )

        return ticket

    @staticmethod
    def get(db: Session, ticket_id: str):
        ticket = db.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return ticket

    @staticmethod
    def get_by_number(db: Session, number: str):
        if not number:
            raise HTTPException(status_code=404, detail="Ticket not found")
        ticket = db.query(Ticket).filter(Ticket.number == number).first()
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return ticket

    @staticmethod
    def list(
        db: Session,
        subscriber_id: str | None,
        status: str | None,
        priority: str | None,
        channel: str | None,
        search: str | None,
        created_by_person_id: str | None,
        assigned_to_person_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        # Use query builder for cleaner, composable filtering
        query = (
            TicketQuery(db)
            .by_subscriber(subscriber_id)
            .by_status(status)
            .by_priority(priority)
            .by_channel(channel)
            .search(search)
            .by_created_by(created_by_person_id)
            .by_assigned_to(assigned_to_person_id)
        )
        # Apply active filter
        if is_active is None:
            query = query.active_only()
        elif is_active:
            query = query.active_only(True)
        else:
            query = query.active_only(False)

        return (
            query.with_relations()  # Eager load relationships to avoid N+1
            .order_by(order_by, order_dir)
            .paginate(limit, offset)
            .all()
        )

    @staticmethod
    def status_stats(db: Session) -> dict:
        """Get ticket counts by status."""
        from sqlalchemy import func

        rows = (
            db.query(Ticket.status, func.count(Ticket.id))
            .filter(Ticket.is_active.is_(True))
            .group_by(Ticket.status)
            .all()
        )
        counts = {status.value if status else "unknown": count for status, count in rows}
        total = sum(counts.values())
        return {
            "total": total,
            "new": counts.get("new", 0),
            "open": counts.get("open", 0),
            "pending": counts.get("pending", 0),
            "on_hold": counts.get("on_hold", 0),
            "resolved": counts.get("resolved", 0),
            "closed": counts.get("closed", 0),
        }

    @staticmethod
    def update(db: Session, ticket_id: str, payload: TicketUpdate):
        ticket = db.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        previous_status = ticket.status
        previous_priority = ticket.priority
        previous_assigned_to = ticket.assigned_to_person_id
        data = payload.model_dump(exclude_unset=True)
        if data.get("created_by_person_id"):
            _ensure_person(db, str(data["created_by_person_id"]))
        if data.get("assigned_to_person_id"):
            _ensure_person(db, str(data["assigned_to_person_id"]))
        if data.get("lead_id"):
            _ensure_lead(db, str(data["lead_id"]))
        if data.get("customer_person_id"):
            _ensure_person(db, str(data["customer_person_id"]))

        # Check if field_visit tag is being added
        had_field_visit = _has_field_visit_tag(ticket.tags)
        new_tags = data.get("tags")
        will_have_field_visit = _has_field_visit_tag(new_tags) if new_tags is not None else had_field_visit

        for key, value in data.items():
            setattr(ticket, key, value)

        # Auto-create work order if field_visit tag is newly added
        if will_have_field_visit and not had_field_visit:
            _auto_create_work_order_for_ticket(db, ticket)

        db.commit()
        db.refresh(ticket)

        # Emit ticket events based on status transitions
        new_status = ticket.status
        new_priority = ticket.priority
        event_payload: dict[str, object | None] = {
            "ticket_id": str(ticket.id),
            "title": ticket.title,
            "subject": ticket.title,
            "from_status": previous_status.value if previous_status else None,
            "to_status": new_status.value if new_status else None,
            "status": new_status.value if new_status else None,
        }
        context = {
            "ticket_id": ticket.id,
            "subscriber_id": ticket.subscriber_id,
        }

        if previous_status != new_status:
            if new_status == TicketStatus.resolved:
                customer_name = _resolve_customer_name(ticket, db)
                customer_email = _resolve_customer_email(ticket, db)
                event_payload["customer_name"] = customer_name
                event_payload["email"] = customer_email
                event_payload["doc"] = {
                    "custom_customer_name": customer_name,
                    "name": str(ticket.id),
                    "subject": ticket.title,
                    "status": new_status.value if new_status else None,
                }
                technician_contact = _resolve_technician_contact(db, ticket.assigned_to_person_id)
                if technician_contact and technician_contact.get("email"):
                    event_payload["technician_name"] = technician_contact["name"]
                    event_payload["technician_email"] = technician_contact["email"]
                    event_payload["technician_doc"] = {
                        "custom_customer_name": technician_contact["name"],
                        "name": str(ticket.id),
                        "subject": ticket.title,
                        "status": new_status.value if new_status else None,
                    }
                emit_event(
                    db,
                    EventType.ticket_resolved,
                    event_payload,
                    subscriber_id=ticket.subscriber_id,
                    ticket_id=ticket.id,
                )

        if ticket.assigned_to_person_id and ticket.assigned_to_person_id != previous_assigned_to:
            customer_name = _resolve_customer_name(ticket, db)
            technician_contact = _resolve_technician_contact(db, ticket.assigned_to_person_id)
            if technician_contact and technician_contact.get("email"):
                emit_event(
                    db,
                    EventType.ticket_assigned,
                    {
                        "ticket_id": str(ticket.id),
                        "title": ticket.title,
                        "subject": ticket.title,
                        "status": ticket.status.value if ticket.status else None,
                        "priority": ticket.priority.value if ticket.priority else None,
                        "channel": ticket.channel.value if ticket.channel else None,
                        "customer_name": customer_name,
                        "technician_name": technician_contact["name"],
                        "email": technician_contact["email"],
                        "technician_email": technician_contact["email"],
                        "technician_doc": {
                            "custom_customer_name": technician_contact["name"],
                            "name": str(ticket.id),
                            "subject": ticket.title,
                            "status": ticket.status.value if ticket.status else None,
                        },
                    },
                    subscriber_id=ticket.subscriber_id,
                    ticket_id=ticket.id,
                )
        # Emit escalated event if priority increased to critical
        if (
            previous_priority != new_priority
            and new_priority == TicketPriority.urgent
            and previous_priority != TicketPriority.urgent
        ):
            emit_event(
                db,
                EventType.ticket_escalated,
                event_payload,
                subscriber_id=ticket.subscriber_id,
                ticket_id=ticket.id,
            )
        # Emit generic update event for ERP sync (if not already emitting resolved/escalated)
        elif previous_status != new_status or len(data) > 1:
            emit_event(
                db,
                EventType.ticket_updated,
                {
                    **event_payload,
                    "changed_fields": list(data.keys()),
                },
                subscriber_id=ticket.subscriber_id,
                ticket_id=ticket.id,
            )

        return ticket

    @staticmethod
    def bulk_update(db: Session, ticket_ids: builtins.list[str], payload: TicketUpdate) -> int:
        if not ticket_ids:
            raise HTTPException(status_code=400, detail="ticket_ids required")
        data = payload.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(status_code=400, detail="Update payload required")
        if data.get("created_by_person_id"):
            _ensure_person(db, str(data["created_by_person_id"]))
        if data.get("assigned_to_person_id"):
            _ensure_person(db, str(data["assigned_to_person_id"]))
        ids = [coerce_uuid(ticket_id) for ticket_id in ticket_ids]
        tickets = db.query(Ticket).filter(Ticket.id.in_(ids)).all()
        if len(tickets) != len(ids):
            raise HTTPException(status_code=404, detail="One or more tickets not found")
        for ticket in tickets:
            for key, value in data.items():
                setattr(ticket, key, value)
        db.commit()
        return len(tickets)

    @staticmethod
    def bulk_update_response(db: Session, ticket_ids: builtins.list[str], payload: TicketUpdate) -> dict:
        updated = Tickets.bulk_update(db, ticket_ids, payload)
        return {"updated": updated}

    @staticmethod
    def delete(db: Session, ticket_id: str):
        ticket = db.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        ticket.is_active = False
        db.commit()


class TicketComments(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: TicketCommentCreate):
        ticket = db.get(Ticket, payload.ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        if payload.author_person_id:
            _ensure_person(db, str(payload.author_person_id))
        comment = TicketComment(**payload.model_dump())
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    @staticmethod
    def bulk_create(db: Session, payload: TicketCommentBulkCreateRequest) -> list[TicketComment]:
        if not payload.ticket_ids:
            raise HTTPException(status_code=400, detail="ticket_ids required")
        if payload.author_person_id:
            _ensure_person(db, str(payload.author_person_id))
        ids = [coerce_uuid(ticket_id) for ticket_id in payload.ticket_ids]
        tickets = db.query(Ticket).filter(Ticket.id.in_(ids)).all()
        if len(tickets) != len(ids):
            raise HTTPException(status_code=404, detail="One or more tickets not found")
        comments: list[TicketComment] = []
        for ticket in tickets:
            comment = TicketComment(
                ticket_id=ticket.id,
                author_person_id=payload.author_person_id,
                body=payload.body,
                is_internal=payload.is_internal,
                attachments=payload.attachments,
            )
            db.add(comment)
            comments.append(comment)
        db.commit()
        for comment in comments:
            db.refresh(comment)
        return comments

    @staticmethod
    def bulk_create_response(db: Session, payload: TicketCommentBulkCreateRequest) -> dict:
        comments = TicketComments.bulk_create(db, payload)
        return {"created": len(comments), "comment_ids": [comment.id for comment in comments]}

    @staticmethod
    def get(db: Session, comment_id: str):
        comment = db.get(TicketComment, comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Ticket comment not found")
        return comment

    @staticmethod
    def list(
        db: Session,
        ticket_id: str | None,
        is_internal: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        return (
            TicketCommentQuery(db)
            .by_ticket(ticket_id)
            .is_internal(is_internal)
            .with_author()  # Eager load author to avoid N+1
            .order_by(order_by, order_dir)
            .paginate(limit, offset)
            .all()
        )

    @staticmethod
    def update(db: Session, comment_id: str, payload: TicketCommentUpdate):
        comment = db.get(TicketComment, comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Ticket comment not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(comment, key, value)
        db.commit()
        db.refresh(comment)
        return comment

    @staticmethod
    def delete(db: Session, comment_id: str):
        comment = db.get(TicketComment, comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Ticket comment not found")
        db.delete(comment)
        db.commit()


class TicketSlaEvents(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: TicketSlaEventCreate):
        ticket = db.get(Ticket, payload.ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        event = TicketSlaEvent(**payload.model_dump())
        db.add(event)
        db.commit()
        db.refresh(event)
        return event

    @staticmethod
    def get(db: Session, event_id: str):
        event = db.get(TicketSlaEvent, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Ticket SLA event not found")
        return event

    @staticmethod
    def list(
        db: Session,
        ticket_id: str | None,
        event_type: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        return (
            TicketSlaEventQuery(db)
            .by_ticket(ticket_id)
            .by_event_type(event_type)
            .order_by(order_by, order_dir)
            .paginate(limit, offset)
            .all()
        )

    @staticmethod
    def update(db: Session, event_id: str, payload: TicketSlaEventUpdate):
        event = db.get(TicketSlaEvent, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Ticket SLA event not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(event, key, value)
        db.commit()
        db.refresh(event)
        return event

    @staticmethod
    def delete(db: Session, event_id: str):
        event = db.get(TicketSlaEvent, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Ticket SLA event not found")
        db.delete(event)
        db.commit()


tickets = Tickets()
ticket_comments = TicketComments()
ticket_sla_events = TicketSlaEvents()
