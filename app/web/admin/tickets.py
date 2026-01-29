"""Admin ticket management web routes."""

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session, selectinload
from typing import Optional

from app.db import SessionLocal
from app.services import tickets as tickets_service
from app.services.subscriber import subscriber as subscriber_service
from app.services.common import coerce_uuid
from app.services import audit as audit_service
from app.services.audit_helpers import extract_changes, format_changes, log_audit_event, model_to_dict, diff_dicts
from app.models.person import Person
from app.models.tickets import Ticket, TicketChannel, TicketPriority, TicketStatus
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import MessageDirection, ChannelType
from app.models.subscriber import Organization, Reseller, Subscriber


from app.services.person import People as people_service

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/support", tags=["web-admin-support"])


def _get_inbound_message_bounds(db: Session, conversation_id):
    last_inbound = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.inbound)
        .order_by(func.coalesce(Message.received_at, Message.created_at).desc())
        .first()
    )
    first_inbound = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.inbound)
        .order_by(func.coalesce(Message.received_at, Message.created_at).asc())
        .first()
    )
    return last_inbound, first_inbound

def _log_activity(
    db: Session,
    request: Request,
    action: str,
    entity_type: str,
    entity_id: str,
    actor_id: str | None,
    metadata: dict | None = None,
) -> None:
    log_audit_event(
        db=db,
        request=request,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        metadata=metadata,
    )


def _format_ticket_activity(event) -> str:
    action = (getattr(event, "action", "") or "").lower()
    metadata = getattr(event, "metadata_", None) or {}
    if action == "create":
        return "Created ticket"
    if action == "comment":
        return "Added a comment"
    if action == "status_change":
        from_status = metadata.get("from")
        to_status = metadata.get("to")
        if from_status and to_status:
            return f"Changed status from {from_status} to {to_status}"
        return "Changed status"
    if action == "priority_change":
        from_priority = metadata.get("from")
        to_priority = metadata.get("to")
        if from_priority and to_priority:
            return f"Changed priority from {from_priority} to {to_priority}"
        return "Changed priority"
    if action == "update":
        return "Updated ticket"
    return action.replace("_", " ").title() or "Activity"


def _build_activity_feed(db: Session, events: list) -> list[dict]:
    actor_ids = {str(event.actor_id) for event in events if getattr(event, "actor_id", None)}
    people = {}
    if actor_ids:
        people = {
            str(person.id): person
            for person in db.query(Person).filter(Person.id.in_(actor_ids)).all()
        }
    activities = []
    for event in events:
        actor = people.get(str(event.actor_id)) if getattr(event, "actor_id", None) else None
        if actor:
            actor_name = f"{actor.first_name} {actor.last_name}"
            actor_url = f"/admin/customers/person/{actor.id}"
        else:
            actor_name = "System"
            actor_url = None
        metadata = getattr(event, "metadata_", None) or {}
        changes = extract_changes(metadata, getattr(event, "action", None))
        change_summary = format_changes(changes, max_items=2)
        activities.append(
            {
                "message": _format_ticket_activity(event),
                "change_summary": change_summary,
                "occurred_at": getattr(event, "occurred_at", None),
                "actor_name": actor_name,
                "actor_url": actor_url,
            }
        )
    return activities


def _build_subscriber_label(subscriber: Subscriber | None) -> str:
    if not subscriber:
        return ""
    label = subscriber.display_name
    if subscriber.subscriber_number:
        return f"{label} ({subscriber.subscriber_number})"
    return label or ""


def _map_channel_to_ticket(channel_type: ChannelType | None) -> str | None:
    if channel_type == ChannelType.email:
        return TicketChannel.email.value
    if channel_type in (ChannelType.whatsapp, ChannelType.facebook_messenger, ChannelType.instagram_dm):
        return TicketChannel.chat.value
    return None


def _select_subscriber_by_id(db: Session, subscriber_id: str) -> Subscriber | None:
    try:
        return subscriber_service.get(db, coerce_uuid(subscriber_id))
    except Exception:
        return None


def _resolve_subscriber_from_contact(db: Session, person: Person | None) -> Subscriber | None:
    if not person:
        return None
    matches = subscriber_service.list(
        db,
        person_id=person.id,
        organization_id=person.organization_id,
        limit=1,
        offset=0,
    )
    return matches[0] if matches else None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/tickets", response_class=HTMLResponse)
def tickets_list(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    channel: Optional[str] = None,
    subscriber: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    """List all tickets with filters."""
    offset = (page - 1) * per_page

    def _filter_tickets(query):
        if status:
            query = query.filter(Ticket.status == _validate_enum(status, TicketStatus, "status"))
        if priority:
            query = query.filter(
                Ticket.priority == _validate_enum(priority, TicketPriority, "priority")
            )
        if channel:
            query = query.filter(
                Ticket.channel == _validate_enum(channel, TicketChannel, "channel")
            )
        if search:
            like_term = f"%{search.strip()}%"
            if like_term != "%%":
                search_filters = [
                    Ticket.title.ilike(like_term),
                    Ticket.description.ilike(like_term),
                    cast(Ticket.id, String).ilike(like_term),
                ]
                ticket_number_attr = getattr(Ticket, "ticket_number", None)
                if ticket_number_attr is not None:
                    search_filters.append(ticket_number_attr.ilike(like_term))
                query = query.filter(or_(*search_filters))
        query = query.filter(Ticket.is_active.is_(True))
        return query.order_by(Ticket.created_at.desc())

    def _validate_enum(value, enum_cls, label):
        if value is None:
            return None
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid {label}") from exc

    subscriber_display = None
    subscriber_url = None

    if subscriber:
        try:
            subscriber_id = coerce_uuid(subscriber)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid subscriber") from exc
        subscriber_obj = subscriber_service.get(db, subscriber_id)
        if subscriber_obj:
            subscriber_display = subscriber_obj.display_name or "Subscriber"
            subscriber_url = f"/admin/subscribers/{subscriber_obj.id}"
            base_query = db.query(Ticket).filter(Ticket.subscriber_id == subscriber_obj.id)
            tickets = _filter_tickets(base_query).limit(per_page).offset(offset).all()
        else:
            tickets = []
    else:
        tickets = tickets_service.tickets.list(
            db=db,
            subscriber_id=None,
            status=status if status else None,
            priority=priority if priority else None,
            channel=channel if channel else None,
            search=search if search else None,
            created_by_person_id=None,
            assigned_to_person_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=per_page,
            offset=offset,
        )

    # Get stats by status
    stats = tickets_service.tickets.status_stats(db)

    from app.web.admin import get_sidebar_stats, get_current_user
    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "admin/tickets/_table.html",
            {
                "request": request,
                "tickets": tickets,
                "search": search,
                "status": status,
                "priority": priority,
                "channel": channel,
                "subscriber": subscriber,
                "subscriber_display": subscriber_display,
                "subscriber_url": subscriber_url,
                "page": page,
                "per_page": per_page,
            },
        )

    return templates.TemplateResponse(
        "admin/tickets/index.html",
        {
            "request": request,
            "tickets": tickets,
            "stats": stats,
            "search": search,
            "status": status,
            "priority": priority,
            "channel": channel,
            "subscriber": subscriber,
            "subscriber_display": subscriber_display,
            "subscriber_url": subscriber_url,
            "page": page,
            "per_page": per_page,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "active_page": "tickets",
        },
    )


@router.get("/tickets/create", response_class=HTMLResponse)
def ticket_create(
    request: Request,
    db: Session = Depends(get_db),
    conversation_id: Optional[str] = Query(None),
    title: Optional[str] = Query(None),
    description: Optional[str] = Query(None),
    subscriber_id: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    subscriber: Optional[str] = Query(None),
    customer: Optional[str] = Query(None),
    modal: Optional[bool] = Query(False),
):
    from app.web.admin import get_sidebar_stats, get_current_user
    # subscriber_service removed
    from app.services import dispatch as dispatch_service

    prefill = {
        "conversation_id": None,
        "title": None,
        "description": None,
        "subscriber_id": None,
        "subscriber_label": "",
        "channel": None,
        "customer": None,
    }

    if conversation_id:
        try:
            conversation = (
                db.query(Conversation)
                .options(selectinload(Conversation.contact))
                .filter(Conversation.id == coerce_uuid(conversation_id))
                .first()
            )
        except Exception:
            conversation = None

        if conversation:
            contact = conversation.contact
            contact_name = (
                (contact.display_name if contact else None)
                or (contact.email if contact else None)
                or (contact.phone if contact else None)
                or "Customer"
            )
            last_inbound, first_inbound = _get_inbound_message_bounds(db, conversation.id)
            subject = conversation.subject
            if last_inbound and last_inbound.subject:
                subject = last_inbound.subject
            elif first_inbound and first_inbound.subject:
                subject = first_inbound.subject
            if not subject:
                snippet_source = None
                if first_inbound and first_inbound.body:
                    snippet_source = first_inbound.body
                elif last_inbound and last_inbound.body:
                    snippet_source = last_inbound.body
                if snippet_source:
                    snippet = snippet_source.strip().splitlines()[0]
                    subject = (snippet[:77] + "...") if len(snippet) > 80 else snippet
            if not subject:
                subject = f"Support request from {contact_name}"
            prefill["conversation_id"] = str(conversation.id)
            prefill["title"] = subject
            prefill["description"] = (
                first_inbound.body.strip() if first_inbound and first_inbound.body else ""
            )
            prefill["customer"] = contact_name
            resolved_subscriber = _resolve_subscriber_from_contact(db, contact)
            if resolved_subscriber:
                prefill["subscriber_id"] = str(resolved_subscriber.id)
                prefill["subscriber_label"] = _build_subscriber_label(resolved_subscriber)
            prefill["channel"] = _map_channel_to_ticket(
                last_inbound.channel_type if last_inbound else None
            )

    if title:
        prefill["title"] = title
    if description:
        prefill["description"] = description
    if customer:
        prefill["customer"] = customer
    if subscriber_id:
        prefill["subscriber_id"] = subscriber_id
        subscriber_obj = _select_subscriber_by_id(db, subscriber_id)
        prefill["subscriber_label"] = _build_subscriber_label(subscriber_obj)
    elif subscriber:
        subscriber_obj = _select_subscriber_by_id(db, subscriber)
        if subscriber_obj:
            prefill["subscriber_id"] = str(subscriber_obj.id)
            prefill["subscriber_label"] = _build_subscriber_label(subscriber_obj)
    if channel and channel in {c.value for c in TicketChannel}:
        prefill["channel"] = channel

    # Subscribers are resolved via typeahead
    accounts: list[dict[str, str]] = []

    # Get technicians for assignment
    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    technicians = sorted(
        technicians,
        key=lambda tech: (
            (tech.person.last_name or "").lower() if tech.person else "",
            (tech.person.first_name or "").lower() if tech.person else "",
        ),
    )

    template_name = "admin/tickets/_form_modal.html" if modal else "admin/tickets/form.html"
    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "ticket": None,
            "accounts": accounts,
            "technicians": technicians,
            "action_url": "/admin/support/tickets",
            "prefill": prefill,
            "active_page": "tickets",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/tickets", response_class=HTMLResponse)
def ticket_create_post(
    request: Request,
    title: str = Form(...),
    description: Optional[str] = Form(None),
    subscriber_id: Optional[str] = Form(None),
    customer: Optional[str] = Form(None),
    assigned_to_person_id: Optional[str] = Form(None),
    priority: str = Form("normal"),
    channel: str = Form("web"),
    status: str = Form("new"),
    due_at: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    attachments: UploadFile | list[UploadFile] | None = File(None),
    conversation_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Create a new ticket."""
    from app.web.admin import get_sidebar_stats, get_current_user
    from app.schemas.tickets import TicketCreate
    from app.models.tickets import TicketPriority, TicketChannel, TicketStatus
    # subscriber_service removed
    from app.services import dispatch as dispatch_service
    from app.services import ticket_attachments as ticket_attachment_service
    from uuid import UUID
    from datetime import datetime

    prepared_attachments: list[dict] = []
    saved_attachments: list[dict] = []
    try:
        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(attachments)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)

        # Parse enums
        priority_map = {
            "low": TicketPriority.low,
            "normal": TicketPriority.normal,
            "high": TicketPriority.high,
            "urgent": TicketPriority.urgent,
        }
        channel_map = {
            "web": TicketChannel.web,
            "email": TicketChannel.email,
            "phone": TicketChannel.phone,
            "chat": TicketChannel.chat,
            "api": TicketChannel.api,
        }
        status_map = {
            "new": TicketStatus.new,
            "open": TicketStatus.open,
            "pending": TicketStatus.pending,
            "on_hold": TicketStatus.on_hold,
            "resolved": TicketStatus.resolved,
            "closed": TicketStatus.closed,
        }

        # Parse tags
        tag_list = None
        if tags:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        # Parse due_at
        due_datetime = None
        if due_at:
            due_datetime = datetime.fromisoformat(due_at)

        metadata: dict[str, object] = {}
        if saved_attachments:
            metadata["attachments"] = saved_attachments
        if customer and customer.strip():
            metadata["customer"] = customer.strip()
        metadata_value: dict[str, object] | None = metadata or None

        current_user = get_current_user(request)
        payload = TicketCreate(
            title=title,
            description=description if description else None,
            subscriber_id=UUID(subscriber_id) if subscriber_id else None,
            assigned_to_person_id=UUID(assigned_to_person_id) if assigned_to_person_id else None,
            created_by_person_id=UUID(current_user["person_id"]) if current_user and current_user.get("person_id") else None,
            priority=priority_map.get(priority, TicketPriority.normal),
            channel=channel_map.get(channel, TicketChannel.web),
            status=status_map.get(status, TicketStatus.new),
            due_at=due_datetime,
            tags=tag_list,
            metadata_=metadata_value,
        )
        ticket = tickets_service.tickets.create(db=db, payload=payload)
        if conversation_id:
            try:
                conversation = db.get(Conversation, coerce_uuid(conversation_id))
                if conversation:
                    conversation.ticket_id = ticket.id
                    db.commit()
            except Exception:
                db.rollback()
        actor_id = str(current_user.get("person_id")) if current_user else None
        _log_activity(
            db=db,
            request=request,
            action="create",
            entity_type="ticket",
            entity_id=str(ticket.id),
            actor_id=actor_id,
            metadata={"title": ticket.title},
        )
        return RedirectResponse(url=f"/admin/support/tickets/{ticket.id}", status_code=303)
    except Exception as e:
        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        # Re-fetch data for form
        accounts: list[dict[str, str]] = []  # subscriber_service removed
        technicians = dispatch_service.technicians.list(
            db=db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        technicians = sorted(
            technicians,
            key=lambda tech: (
                (tech.person.last_name or "").lower() if tech.person else "",
                (tech.person.first_name or "").lower() if tech.person else "",
            ),
        )
        return templates.TemplateResponse(
            "admin/tickets/form.html",
            {
                "request": request,
                "ticket": None,
                "accounts": accounts,
                "technicians": technicians,
                "action_url": "/admin/support/tickets",
                "error": str(e),
                "active_page": "tickets",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )


@router.get("/tickets/{ticket_id}/edit", response_class=HTMLResponse)
def ticket_edit(
    request: Request,
    ticket_id: str,
    db: Session = Depends(get_db),
):
    """Edit support ticket form."""
    from app.web.admin import get_sidebar_stats, get_current_user
    # subscriber_service removed
    from app.services import dispatch as dispatch_service

    try:
        ticket = tickets_service.tickets.get(db=db, ticket_id=ticket_id)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Ticket not found"},
            status_code=404,
        )

    accounts: list[dict[str, str]] = []  # subscriber_service removed
    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    technicians = sorted(
        technicians,
        key=lambda tech: (
            (tech.person.last_name or "").lower() if tech.person else "",
            (tech.person.first_name or "").lower() if tech.person else "",
        ),
    )

    return templates.TemplateResponse(
        "admin/tickets/form.html",
        {
            "request": request,
            "ticket": ticket,
            "accounts": accounts,
            "technicians": technicians,
            "action_url": f"/admin/support/tickets/{ticket_id}/edit",
            "active_page": "tickets",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/tickets/{ticket_id}/edit", response_class=HTMLResponse)
def ticket_edit_post(
    request: Request,
    ticket_id: str,
    title: str = Form(...),
    description: Optional[str] = Form(None),
    subscriber_id: Optional[str] = Form(None),
    customer: Optional[str] = Form(None),
    assigned_to_person_id: Optional[str] = Form(None),
    priority: str = Form("normal"),
    channel: str = Form("web"),
    status: str = Form("new"),
    due_at: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    attachments: UploadFile | list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
):
    """Update a support ticket."""
    from app.schemas.tickets import TicketUpdate
    from app.models.tickets import TicketPriority, TicketChannel, TicketStatus
    # subscriber_service removed
    from app.services import dispatch as dispatch_service
    from app.services import ticket_attachments as ticket_attachment_service
    from app.web.admin import get_current_user, get_sidebar_stats
    from uuid import UUID
    from datetime import datetime, timezone

    prepared_attachments: list[dict] = []
    saved_attachments: list[dict] = []
    try:
        ticket = tickets_service.tickets.get(db=db, ticket_id=ticket_id)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Ticket not found"},
            status_code=404,
        )

    try:
        before_state = model_to_dict(
            ticket,
            include={
                "subscriber_id",
                "created_by_person_id",
                "assigned_to_person_id",
                "title",
                "description",
                "status",
                "priority",
                "channel",
                "tags",
                "due_at",
                "resolved_at",
                "closed_at",
                "is_active",
            },
        )

        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(attachments)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)

        priority_map = {
            "low": TicketPriority.low,
            "normal": TicketPriority.normal,
            "high": TicketPriority.high,
            "urgent": TicketPriority.urgent,
        }
        channel_map = {
            "web": TicketChannel.web,
            "email": TicketChannel.email,
            "phone": TicketChannel.phone,
            "chat": TicketChannel.chat,
            "api": TicketChannel.api,
        }
        status_map = {
            "new": TicketStatus.new,
            "open": TicketStatus.open,
            "pending": TicketStatus.pending,
            "on_hold": TicketStatus.on_hold,
            "resolved": TicketStatus.resolved,
            "closed": TicketStatus.closed,
            "canceled": TicketStatus.canceled,
        }

        tag_list = None
        if tags is not None:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] or None

        due_datetime = None
        if due_at:
            due_datetime = datetime.fromisoformat(due_at)

        metadata_update = None
        metadata_changed = False
        if saved_attachments or customer is not None:
            existing_metadata = (
                dict(ticket.metadata_) if ticket.metadata_ and isinstance(ticket.metadata_, dict) else {}
            )
            new_metadata = dict(existing_metadata)
            if saved_attachments:
                existing_attachments = existing_metadata.get("attachments")
                attachment_list = (
                    list(existing_attachments) if isinstance(existing_attachments, list) else []
                )
                attachment_list.extend(saved_attachments)
                new_metadata["attachments"] = attachment_list
                metadata_changed = True

            customer_value = (customer or "").strip()
            if customer_value:
                if existing_metadata.get("customer") != customer_value:
                    metadata_changed = True
                new_metadata["customer"] = customer_value
            else:
                if "customer" in existing_metadata:
                    metadata_changed = True
                new_metadata.pop("customer", None)

            if metadata_changed:
                metadata_update = new_metadata if new_metadata else None

        new_status = status_map.get(status, ticket.status)
        update_data = {
            "title": title,
            "description": description if description else None,
            "subscriber_id": UUID(subscriber_id) if subscriber_id else None,
            "assigned_to_person_id": UUID(assigned_to_person_id) if assigned_to_person_id else None,
            "priority": priority_map.get(priority, ticket.priority),
            "channel": channel_map.get(channel, ticket.channel),
            "status": new_status,
            "due_at": due_datetime,
            "tags": tag_list,
        }

        if new_status == TicketStatus.resolved and not ticket.resolved_at:
            update_data["resolved_at"] = datetime.now(timezone.utc)
        if new_status == TicketStatus.closed and not ticket.closed_at:
            update_data["closed_at"] = datetime.now(timezone.utc)

        if metadata_changed:
            update_data["metadata_"] = metadata_update

        payload = TicketUpdate(**update_data)
        tickets_service.tickets.update(db=db, ticket_id=ticket_id, payload=payload)
        updated_ticket = tickets_service.tickets.get(db=db, ticket_id=ticket_id)
        after_state = model_to_dict(
            updated_ticket,
            include={
                "subscriber_id",
                "created_by_person_id",
                "assigned_to_person_id",
                "title",
                "description",
                "status",
                "priority",
                "channel",
                "tags",
                "due_at",
                "resolved_at",
                "closed_at",
                "is_active",
            },
        )
        changes = diff_dicts(before_state, after_state)
        metadata_payload = {"changes": changes} if changes else None
        current_user = get_current_user(request)
        _log_activity(
            db=db,
            request=request,
            action="update",
            entity_type="ticket",
            entity_id=str(ticket_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata=metadata_payload,
        )
        return RedirectResponse(url=f"/admin/support/tickets/{ticket_id}", status_code=303)
    except Exception as e:
        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        accounts: list[dict[str, str]] = []  # subscriber_service removed
        technicians = dispatch_service.technicians.list(
            db=db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        technicians = sorted(
            technicians,
            key=lambda tech: (
                (tech.person.last_name or "").lower() if tech.person else "",
                (tech.person.first_name or "").lower() if tech.person else "",
            ),
        )
        return templates.TemplateResponse(
            "admin/tickets/form.html",
            {
                "request": request,
                "ticket": ticket,
                "accounts": accounts,
                "technicians": technicians,
                "action_url": f"/admin/support/tickets/{ticket_id}/edit",
                "error": str(e),
                "active_page": "tickets",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(
    request: Request,
    ticket_id: str,
    db: Session = Depends(get_db),
):
    """View ticket details."""
    try:
        ticket = tickets_service.tickets.get(db=db, ticket_id=ticket_id)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Ticket not found"},
            status_code=404,
        )

    # Get comments for this ticket
    comments = tickets_service.ticket_comments.list(
        db=db,
        ticket_id=ticket_id,
        is_internal=None,  # Show both internal and external comments
        order_by="created_at",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    from app.web.admin import get_sidebar_stats, get_current_user
    audit_events = audit_service.audit_events.list(
        db=db,
        actor_id=None,
        actor_type=None,
        action=None,
        entity_type="ticket",
        entity_id=str(ticket_id),
        request_id=None,
        is_success=None,
        status_code=None,
        is_active=None,
        order_by="occurred_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    activities = _build_activity_feed(db, audit_events)
    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    return templates.TemplateResponse(
        "admin/tickets/detail.html",
        {
            "request": request,
            "ticket": ticket,
            "comments": comments,
            "activities": activities,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "active_page": "tickets",
        },
    )


@router.post("/tickets/{ticket_id}/status", response_class=HTMLResponse)
def update_ticket_status(
    request: Request,
    ticket_id: str,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    """Update ticket status."""
    from app.schemas.tickets import TicketUpdate
    from app.models.tickets import TicketStatus
    from app.web.admin import get_current_user
    from datetime import datetime, timezone

    try:
        status_map = {
            "new": TicketStatus.new,
            "open": TicketStatus.open,
            "pending": TicketStatus.pending,
            "on_hold": TicketStatus.on_hold,
            "resolved": TicketStatus.resolved,
            "closed": TicketStatus.closed,
            "canceled": TicketStatus.canceled,
        }
        new_status = status_map.get(status, TicketStatus.open)
        ticket = tickets_service.tickets.get(db=db, ticket_id=ticket_id)
        old_status = ticket.status.value if ticket.status else None

        resolved_at = datetime.now(timezone.utc) if status == "resolved" else None
        closed_at = datetime.now(timezone.utc) if status == "closed" else None
        payload = TicketUpdate(status=new_status, resolved_at=resolved_at, closed_at=closed_at)
        tickets_service.tickets.update(db=db, ticket_id=ticket_id, payload=payload)
        current_user = get_current_user(request)
        _log_activity(
            db=db,
            request=request,
            action="status_change",
            entity_type="ticket",
            entity_id=str(ticket_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"from": old_status, "to": new_status.value if new_status else None},
        )

        if request.headers.get("HX-Request"):
            return HTMLResponse(content="", headers={"HX-Redirect": f"/admin/support/tickets/{ticket_id}"})
        return RedirectResponse(url=f"/admin/support/tickets/{ticket_id}", status_code=303)
    except Exception as e:
        from app.web.admin import get_sidebar_stats, get_current_user
        sidebar_stats = get_sidebar_stats(db)
        current_user = get_current_user(request)
        return templates.TemplateResponse(
            "admin/errors/500.html",
            {"request": request, "error": str(e), "current_user": current_user, "sidebar_stats": sidebar_stats},
            status_code=500,
        )


@router.post("/tickets/{ticket_id}/priority", response_class=HTMLResponse)
def update_ticket_priority(
    request: Request,
    ticket_id: str,
    priority: str = Form(...),
    db: Session = Depends(get_db),
):
    """Update ticket priority."""
    from app.schemas.tickets import TicketUpdate
    from app.models.tickets import TicketPriority
    from app.web.admin import get_current_user

    try:
        priority_map = {
            "low": TicketPriority.low,
            "normal": TicketPriority.normal,
            "high": TicketPriority.high,
            "urgent": TicketPriority.urgent,
        }
        new_priority = priority_map.get(priority, TicketPriority.normal)
        ticket = tickets_service.tickets.get(db=db, ticket_id=ticket_id)
        old_priority = ticket.priority.value if ticket.priority else None

        payload = TicketUpdate(priority=new_priority)
        tickets_service.tickets.update(db=db, ticket_id=ticket_id, payload=payload)
        current_user = get_current_user(request)
        _log_activity(
            db=db,
            request=request,
            action="priority_change",
            entity_type="ticket",
            entity_id=str(ticket_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"from": old_priority, "to": new_priority.value if new_priority else None},
        )

        if request.headers.get("HX-Request"):
            return HTMLResponse(content="", headers={"HX-Redirect": f"/admin/support/tickets/{ticket_id}"})
        return RedirectResponse(url=f"/admin/support/tickets/{ticket_id}", status_code=303)
    except Exception as e:
        from app.web.admin import get_sidebar_stats, get_current_user
        sidebar_stats = get_sidebar_stats(db)
        current_user = get_current_user(request)
        return templates.TemplateResponse(
            "admin/errors/500.html",
            {"request": request, "error": str(e), "current_user": current_user, "sidebar_stats": sidebar_stats},
            status_code=500,
        )


@router.post("/tickets/{ticket_id}/comments", response_class=HTMLResponse)
def add_ticket_comment(
    request: Request,
    ticket_id: str,
    body: str = Form(...),
    is_internal: Optional[str] = Form(None),
    attachments: UploadFile | list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
):
    """Add a comment to a ticket."""
    from app.schemas.tickets import TicketCommentCreate
    from app.web.admin import get_current_user
    from app.services import ticket_attachments as ticket_attachment_service
    from uuid import UUID

    prepared_attachments: list[dict] = []
    try:
        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(attachments)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
        payload = TicketCommentCreate(
            ticket_id=UUID(ticket_id),
            body=body,
            is_internal=is_internal == "true",
            attachments=saved_attachments or None,
        )
        tickets_service.ticket_comments.create(db=db, payload=payload)
        current_user = get_current_user(request)
        _log_activity(
            db=db,
            request=request,
            action="comment",
            entity_type="ticket",
            entity_id=str(ticket_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"internal": is_internal == "true"},
        )

        if request.headers.get("HX-Request"):
            return HTMLResponse(content="", headers={"HX-Redirect": f"/admin/support/tickets/{ticket_id}"})
        return RedirectResponse(url=f"/admin/support/tickets/{ticket_id}", status_code=303)
    except Exception as e:
        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        from app.web.admin import get_sidebar_stats, get_current_user
        sidebar_stats = get_sidebar_stats(db)
        current_user = get_current_user(request)
        return templates.TemplateResponse(
            "admin/errors/500.html",
            {"request": request, "error": str(e), "current_user": current_user, "sidebar_stats": sidebar_stats},
            status_code=500,
        )
