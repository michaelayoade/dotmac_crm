"""Work-order scoped customer-technician chat on CRM conversations.

This reuses the existing CRM conversation/message engine, but applies a field
service access layer: technicians can only see/send for assigned work orders,
and field chat conversations are marked in metadata so they do not need to be
routed like general support inbox chats.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.logging import get_logger
from app.models.crm.chat_widget import WidgetVisitorSession
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.person import ChannelType as PersonChannelType
from app.models.person import PersonChannel
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.schemas.crm.conversation import ConversationCreate, MessageCreate
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service
from app.services.field.jobs import get_scoped_work_order

logger = get_logger(__name__)

FIELD_CHAT_SURFACE = "field_service"
FIELD_CHAT_TAG = "field_chat"
_ACTIVE_CHAT_STATUSES = {
    WorkOrderStatus.scheduled,
    WorkOrderStatus.dispatched,
    WorkOrderStatus.in_progress,
    WorkOrderStatus.paused,
}


def _person_label(person) -> str | None:
    if person is None:
        return None
    display_name = getattr(person, "display_name", None)
    if isinstance(display_name, str) and display_name.strip():
        return display_name.strip()
    name = " ".join(
        part
        for part in [
            getattr(person, "first_name", None),
            getattr(person, "last_name", None),
        ]
        if isinstance(part, str) and part.strip()
    )
    return name or None


def _customer_person_id(work_order: WorkOrder) -> UUID | None:
    return work_order.subscriber.person_id if work_order.subscriber else None


def _customer_name(work_order: WorkOrder) -> str | None:
    return _person_label(work_order.subscriber.person) if work_order.subscriber else None


def _can_send(work_order: WorkOrder) -> bool:
    return (
        bool(work_order.subscriber_id and _customer_person_id(work_order))
        and work_order.status in _ACTIVE_CHAT_STATUSES
    )


def _field_metadata(work_order: WorkOrder) -> dict:
    return {
        "surface": FIELD_CHAT_SURFACE,
        "tags": [FIELD_CHAT_TAG],
        "field_work_order_id": str(work_order.id),
        "subscriber_id": str(work_order.subscriber_id) if work_order.subscriber_id else None,
        "project_id": str(work_order.project_id) if work_order.project_id else None,
        "ticket_id": str(work_order.ticket_id) if work_order.ticket_id else None,
    }


def _conversation_query(db: Session, work_order: WorkOrder):
    work_order_id = str(work_order.id)
    customer_person_id = _customer_person_id(work_order)
    query = db.query(Conversation).filter(Conversation.is_active.is_(True))
    if customer_person_id is not None:
        query = query.filter(Conversation.person_id == customer_person_id)
    return query.filter(
        or_(
            Conversation.metadata_["field_work_order_id"].as_string() == work_order_id,
            Conversation.metadata_["work_order_id"].as_string() == work_order_id,
        )
    )


def resolve_field_conversation(
    db: Session,
    work_order: WorkOrder,
    *,
    create: bool,
) -> Conversation | None:
    conversation = _conversation_query(db, work_order).order_by(Conversation.created_at.desc()).first()
    if conversation is not None:
        return conversation
    if not create:
        return None
    if not _can_send(work_order):
        raise HTTPException(status_code=409, detail="Field chat is not active for this job")
    person_id = _customer_person_id(work_order)
    if person_id is None:
        raise HTTPException(status_code=409, detail="Job has no customer contact")

    conversation = conversation_service.Conversations.create(
        db,
        ConversationCreate(
            person_id=person_id,
            ticket_id=work_order.ticket_id,
            status=ConversationStatus.open,
            subject=f"Field chat: {work_order.title}",
            is_active=True,
            metadata_=_field_metadata(work_order),
        ),
    )
    db.refresh(conversation)
    return conversation


def _message_payload(message: Message) -> dict:
    timestamp = message.received_at or message.sent_at or message.created_at or datetime.now(UTC)
    return {
        "id": message.id,
        "body": message.body or "",
        "direction": "customer" if message.direction == MessageDirection.inbound else "staff",
        "author_name": _person_label(message.author),
        "created_at": timestamp,
        "read_at": message.read_at,
    }


def _list_messages(db: Session, conversation_id: UUID | None, *, limit: int) -> list[dict]:
    if conversation_id is None:
        return []
    messages = (
        db.query(Message)
        .options(joinedload(Message.author))
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.channel_type == ChannelType.chat_widget)
        .order_by(Message.created_at.desc())
        .limit(limit)
        .all()
    )
    messages.reverse()
    return [_message_payload(message) for message in messages]


def get_job_chat(
    db: Session,
    person_id: str | UUID,
    work_order_id: str,
    *,
    limit: int = 50,
) -> dict:
    work_order = get_scoped_work_order(db, person_id, work_order_id)
    conversation = resolve_field_conversation(db, work_order, create=False)
    return {
        "available": bool(work_order.subscriber_id and _customer_person_id(work_order)),
        "can_send": _can_send(work_order),
        "conversation_id": conversation.id if conversation else None,
        "customer_name": _customer_name(work_order),
        "messages": _list_messages(db, conversation.id if conversation else None, limit=limit),
    }


def _chat_widget_channel(db: Session, conversation: Conversation) -> PersonChannel | None:
    channel = (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == conversation.person_id)
        .filter(PersonChannel.channel_type == PersonChannelType.chat_widget)
        .first()
    )
    if channel is not None:
        return channel
    session = db.query(WidgetVisitorSession).filter(WidgetVisitorSession.conversation_id == conversation.id).first()
    if session is None:
        return None
    channel = PersonChannel(
        person_id=conversation.person_id,
        channel_type=PersonChannelType.chat_widget,
        address=str(session.id),
        is_primary=False,
    )
    db.add(channel)
    db.flush()
    return channel


def send_job_chat_message(
    db: Session,
    person_id: str | UUID,
    work_order_id: str,
    *,
    body: str,
) -> dict:
    work_order = get_scoped_work_order(db, person_id, work_order_id)
    if not _can_send(work_order):
        raise HTTPException(status_code=409, detail="Field chat is not active for this job")
    conversation = resolve_field_conversation(db, work_order, create=True)
    if conversation is None:
        raise HTTPException(status_code=409, detail="Field chat is not available")

    channel = _chat_widget_channel(db, conversation)
    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=channel.id if channel else None,
            channel_type=ChannelType.chat_widget,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=body.strip(),
            author_id=coerce_uuid(str(person_id)),
            sent_at=datetime.now(UTC),
            metadata_={
                **_field_metadata(work_order),
                "source": "field_app",
            },
        ),
    )

    _broadcast_field_message(db, conversation, message, work_order, preview=body)
    return _message_payload(message)


def _broadcast_field_message(
    db: Session,
    conversation: Conversation,
    message: Message,
    work_order: WorkOrder,
    *,
    preview: str,
) -> None:
    try:
        from app.websocket.broadcaster import (
            broadcast_message_status,
            broadcast_new_message,
            broadcast_to_widget_visitor,
        )

        broadcast_new_message(message, conversation)
        broadcast_message_status(str(message.id), str(conversation.id), message.status.value)
        session = db.query(WidgetVisitorSession).filter(WidgetVisitorSession.conversation_id == conversation.id).first()
        if session is not None:
            broadcast_to_widget_visitor(str(session.id), message)
    except Exception:
        logger.debug("field_chat_broadcast_failed", exc_info=True)
    try:
        from app.services import selfcare

        selfcare.notify_field_chat_message(
            db,
            subscriber_id=str(work_order.subscriber_id),
            work_order_id=str(work_order.id),
            conversation_id=str(conversation.id),
            preview=(preview or "")[:140],
        )
    except Exception:
        logger.debug("field_chat_selfcare_notify_failed", exc_info=True)
