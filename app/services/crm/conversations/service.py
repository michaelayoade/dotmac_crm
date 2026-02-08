from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models.crm.conversation import (
    Conversation,
    ConversationAssignment,
    ConversationTag,
    Message,
    MessageAttachment,
)
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.person import ChannelType as PersonChannelType, Person, PersonChannel
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin


def _now():
    return datetime.now(timezone.utc)


class Conversations(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        person = db.get(Person, payload.person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        data = payload.model_dump()
        conversation = Conversation(**data)
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        from app.services.events import EventType, emit_event

        emit_event(
            db,
            EventType.conversation_created,
            {
                "conversation_id": str(conversation.id),
                "person_id": str(conversation.person_id),
                "status": conversation.status.value if conversation.status else None,
                "subject": conversation.subject,
                "doc": {
                    "name": str(conversation.id),
                    "status": conversation.status.value if conversation.status else None,
                },
            },
        )
        return conversation

    @staticmethod
    def get(db: Session, conversation_id: str):
        conversation = db.get(
            Conversation,
            coerce_uuid(conversation_id),
            options=[
                selectinload(Conversation.messages),
                selectinload(Conversation.assignments),
                selectinload(Conversation.tags),
            ],
        )
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conversation

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        ticket_id: str | None,
        status: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Conversation)
        if person_id:
            query = query.filter(Conversation.person_id == coerce_uuid(person_id))
        if ticket_id:
            query = query.filter(Conversation.ticket_id == coerce_uuid(ticket_id))
        if status:
            status_value = validate_enum(status, ConversationStatus, "status")
            query = query.filter(Conversation.status == status_value)
        if is_active is None:
            query = query.filter(Conversation.is_active.is_(True))
        else:
            query = query.filter(Conversation.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": Conversation.created_at,
                "last_message_at": Conversation.last_message_at,
                "updated_at": Conversation.updated_at,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, conversation_id: str, payload):
        conversation = db.get(Conversation, coerce_uuid(conversation_id))
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("person_id"):
            person = db.get(Person, data["person_id"])
            if not person:
                raise HTTPException(status_code=404, detail="Person not found")
        for key, value in data.items():
            setattr(conversation, key, value)
        db.commit()
        db.refresh(conversation)
        return conversation

    @staticmethod
    def delete(db: Session, conversation_id: str):
        conversation = db.get(Conversation, coerce_uuid(conversation_id))
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conversation.is_active = False
        db.commit()


class ConversationAssignments(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        conversation = db.get(Conversation, payload.conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if not payload.team_id and not payload.agent_id:
            raise HTTPException(
                status_code=400,
                detail="Conversation assignment requires team_id or agent_id",
            )
        db.query(ConversationAssignment).filter(
            ConversationAssignment.conversation_id == payload.conversation_id,
            ConversationAssignment.is_active.is_(True),
        ).update({"is_active": False, "updated_at": _now()})
        assignment = ConversationAssignment(**payload.model_dump())
        if assignment.assigned_at is None:
            assignment.assigned_at = _now()
        db.add(assignment)
        db.commit()
        db.refresh(assignment)
        return assignment

    @staticmethod
    def list(
        db: Session,
        conversation_id: str | None,
        team_id: str | None,
        agent_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ConversationAssignment)
        if conversation_id:
            query = query.filter(ConversationAssignment.conversation_id == coerce_uuid(conversation_id))
        if team_id:
            query = query.filter(ConversationAssignment.team_id == coerce_uuid(team_id))
        if agent_id:
            query = query.filter(ConversationAssignment.agent_id == coerce_uuid(agent_id))
        if is_active is None:
            query = query.filter(ConversationAssignment.is_active.is_(True))
        else:
            query = query.filter(ConversationAssignment.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": ConversationAssignment.created_at},
        )
        return apply_pagination(query, limit, offset).all()


class ConversationTags(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        conversation = db.get(Conversation, payload.conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        tag = ConversationTag(**payload.model_dump())
        db.add(tag)
        db.commit()
        db.refresh(tag)
        return tag

    @staticmethod
    def list(
        db: Session,
        conversation_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ConversationTag)
        if conversation_id:
            query = query.filter(ConversationTag.conversation_id == coerce_uuid(conversation_id))
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": ConversationTag.created_at, "tag": ConversationTag.tag},
        )
        return apply_pagination(query, limit, offset).all()


class Messages(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        conversation = db.get(Conversation, payload.conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        message = Message(**payload.model_dump(by_alias=False))
        # Ensure timestamps exist so inbox ordering stays accurate.
        if message.direction == MessageDirection.inbound and not message.received_at:
            message.received_at = _now()
        if message.direction == MessageDirection.outbound and not message.sent_at:
            message.sent_at = _now()
        db.add(message)
        timestamp = message.received_at or message.sent_at or _now()
        conversation.last_message_at = timestamp
        conversation.updated_at = timestamp
        db.commit()
        db.refresh(message)
        if message.direction == MessageDirection.inbound:
            from app.services.events import EventType, emit_event

            emit_event(
                db,
                EventType.message_inbound,
                {
                    "message_id": str(message.id),
                    "conversation_id": str(message.conversation_id),
                    "person_id": str(conversation.person_id),
                    "channel_type": message.channel_type.value,
                    "channel_target_id": (
                        str(message.channel_target_id) if message.channel_target_id else None
                    ),
                    "subject": message.subject,
                    "external_id": message.external_id,
                },
            )
        return message

    @staticmethod
    def get(db: Session, message_id: str):
        message = db.get(
            Message,
            coerce_uuid(message_id),
            options=[selectinload(Message.attachments)],
        )
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
        return message

    @staticmethod
    def list(
        db: Session,
        conversation_id: str | None,
        channel_type: str | None,
        direction: str | None,
        status: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Message).options(selectinload(Message.attachments))
        if conversation_id:
            query = query.filter(Message.conversation_id == coerce_uuid(conversation_id))
        if channel_type:
            channel_value = validate_enum(channel_type, ChannelType, "channel_type")
            query = query.filter(Message.channel_type == channel_value)
        if direction:
            direction_value = validate_enum(direction, MessageDirection, "direction")
            query = query.filter(Message.direction == direction_value)
        if status:
            status_value = validate_enum(status, MessageStatus, "status")
            query = query.filter(Message.status == status_value)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": func.coalesce(
                    Message.received_at,
                    Message.sent_at,
                    Message.created_at,
                ),
                "received_at": Message.received_at,
                "sent_at": Message.sent_at,
                "message_time": func.coalesce(
                    Message.received_at,
                    Message.sent_at,
                    Message.created_at,
                ),
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, message_id: str, payload):
        message = db.get(Message, coerce_uuid(message_id))
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(message, key, value)
        db.commit()
        db.refresh(message)
        return message


class MessageAttachments(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        message = db.get(Message, payload.message_id)
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
        attachment = MessageAttachment(**payload.model_dump())
        db.add(attachment)
        db.commit()
        db.refresh(attachment)
        return attachment


def resolve_open_conversation(db: Session, person_id: str) -> Conversation | None:
    return (
        db.query(Conversation)
        .filter(Conversation.person_id == coerce_uuid(person_id))
        .filter(Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]))
        .order_by(Conversation.updated_at.desc())
        .first()
    )


def resolve_open_conversation_for_channel(
    db: Session,
    person_id: str,
    channel_type: ChannelType,
) -> Conversation | None:
    conversations = (
        db.query(Conversation)
        .filter(Conversation.person_id == coerce_uuid(person_id))
        .filter(Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]))
        .order_by(Conversation.updated_at.desc())
        .all()
    )
    for conversation in conversations:
        other_channel = (
            db.query(Message.id)
            .filter(Message.conversation_id == conversation.id)
            .filter(Message.channel_type != channel_type)
            .first()
        )
        if other_channel:
            continue
        last_message = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(
                func.coalesce(
                    Message.received_at,
                    Message.sent_at,
                    Message.created_at,
                ).desc()
            )
            .first()
        )
        if not last_message or last_message.channel_type == channel_type:
            return conversation
    return None


def resolve_person_channel(
    db: Session, person_id: str, channel_type: ChannelType
) -> PersonChannel | None:
    person_channel_type = PersonChannelType(channel_type.value)
    return (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == coerce_uuid(person_id))
        .filter(PersonChannel.channel_type == person_channel_type)
        .order_by(PersonChannel.is_primary.desc())
        .first()
    )


def resolve_conversation_contact(
    db: Session,
    conversation_id: str,
    person_id: str,
    channel_type: ChannelType | None = None,
    address: str | None = None,
) -> Conversation:
    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    resolved_channel_type = channel_type
    resolved_address = address.strip() if address else None
    source_channel_id = None

    if not resolved_channel_type or not resolved_address:
        last_message = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .filter(Message.direction == MessageDirection.inbound)
            .order_by(func.coalesce(Message.received_at, Message.created_at).desc())
            .first()
        )
        if not last_message:
            last_message = (
                db.query(Message)
                .filter(Message.conversation_id == conversation.id)
                .order_by(
                    func.coalesce(
                        Message.received_at,
                        Message.sent_at,
                        Message.created_at,
                    ).desc()
                )
                .first()
            )
        if last_message:
            if not resolved_channel_type:
                resolved_channel_type = last_message.channel_type
            if not resolved_address and last_message.person_channel_id:
                source_channel = db.get(PersonChannel, last_message.person_channel_id)
                if source_channel:
                    resolved_address = source_channel.address
                    source_channel_id = source_channel.id

    new_channel = None
    if resolved_channel_type and resolved_address:
        person_channel_type = PersonChannelType(resolved_channel_type.value)
        new_channel = (
            db.query(PersonChannel)
            .filter(PersonChannel.person_id == person.id)
            .filter(PersonChannel.channel_type == person_channel_type)
            .filter(PersonChannel.address == resolved_address)
            .first()
        )
        if not new_channel:
            has_primary = (
                db.query(PersonChannel)
                .filter(PersonChannel.person_id == person.id)
                .filter(PersonChannel.channel_type == person_channel_type)
                .filter(PersonChannel.is_primary.is_(True))
                .first()
            )
            new_channel = PersonChannel(
                person_id=person.id,
                channel_type=person_channel_type,
                address=resolved_address,
                is_primary=has_primary is None,
            )
            db.add(new_channel)
            db.flush()

    conversation.person_id = person.id

    if new_channel:
        update_query = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .filter(Message.channel_type == resolved_channel_type)
        )
        if source_channel_id:
            update_query = update_query.filter(
                (Message.person_channel_id == source_channel_id)
                | (Message.person_channel_id.is_(None))
            )
        else:
            update_query = update_query.filter(Message.person_channel_id.is_(None))
        update_query.update({"person_channel_id": new_channel.id}, synchronize_session=False)

    db.commit()
    db.refresh(conversation)
    return conversation


def can_mark_conversation_read(
    db: Session,
    conversation_id: str,
    actor_person_id: str | None,
) -> bool:
    """Check if the actor can mark a conversation as read."""
    from app.models.crm.team import CrmAgent, CrmAgentTeam

    if not actor_person_id:
        return False
    assignment = (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == coerce_uuid(conversation_id))
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    if not assignment:
        return True
    if assignment.agent_id:
        from app.models.crm.team import CrmAgent
        agent = db.get(CrmAgent, assignment.agent_id)
        return bool(agent and str(agent.person_id) == str(actor_person_id))
    if assignment.team_id:
        agent = db.query(CrmAgent).filter(CrmAgent.person_id == coerce_uuid(actor_person_id)).first()
        if not agent:
            return False
        membership = (
            db.query(CrmAgentTeam)
            .filter(CrmAgentTeam.agent_id == agent.id)
            .filter(CrmAgentTeam.team_id == assignment.team_id)
            .first()
        )
        return bool(membership)
    return False


def mark_conversation_read(
    db: Session,
    conversation_id: str,
    actor_person_id: str | None,
    last_seen_at: datetime | None,
) -> None:
    """Mark inbound messages as read up to last_seen_at."""
    if not last_seen_at or not can_mark_conversation_read(db, conversation_id, actor_person_id):
        return
    db.query(Message).filter(
        Message.conversation_id == coerce_uuid(conversation_id),
        Message.direction == MessageDirection.inbound,
        Message.status == MessageStatus.received,
        Message.read_at.is_(None),
        func.coalesce(Message.received_at, Message.created_at) <= last_seen_at,
    ).update({"read_at": _now()})
    db.commit()


def get_latest_message(db: Session, conversation_id: str) -> Message | None:
    """Get the latest message in a conversation."""
    return (
        db.query(Message)
        .filter(Message.conversation_id == coerce_uuid(conversation_id))
        .order_by(
            func.coalesce(
                Message.received_at,
                Message.sent_at,
                Message.created_at,
            ).desc()
        )
        .first()
    )


def get_unread_count(db: Session, conversation_id: str) -> int:
    """Get count of unread inbound messages in a conversation."""
    return (
        db.query(Message)
        .filter(Message.conversation_id == coerce_uuid(conversation_id))
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.status == MessageStatus.received)
        .filter(Message.read_at.is_(None))
        .count()
    )


def assign_conversation(
    db: Session,
    conversation_id: str,
    agent_id: str | None = None,
    team_id: str | None = None,
    assigned_by_id: str | None = None,
    update_lead_owner: bool = True,
) -> ConversationAssignment | None:
    """Assign conversation to agent/team, optionally update lead owner.

    Returns the new assignment, or None if unassigned.
    """
    from app.models.crm.sales import Lead
    from app.schemas.crm.conversation import ConversationAssignmentCreate

    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    agent_value = (agent_id or "").strip()
    team_value = (team_id or "").strip()
    agent_uuid = coerce_uuid(agent_value) if agent_value else None
    team_uuid = coerce_uuid(team_value) if team_value else None

    if agent_value or team_value:
        payload = ConversationAssignmentCreate(
            conversation_id=conversation.id,
            agent_id=agent_uuid,
            team_id=team_uuid,
            assigned_by_id=coerce_uuid(assigned_by_id) if assigned_by_id else None,
            assigned_at=_now(),
            is_active=True,
        )
        assignment = ConversationAssignments.create(db, payload)

        if update_lead_owner and agent_uuid:
            db.query(Lead).filter(
                Lead.contact_id == conversation.contact_id,
                Lead.is_active.is_(True),
            ).update({"owner_agent_id": agent_uuid}, synchronize_session=False)
            db.commit()

        return assignment
    else:
        db.query(ConversationAssignment).filter(
            ConversationAssignment.conversation_id == conversation.id,
            ConversationAssignment.is_active.is_(True),
        ).update({"is_active": False, "updated_at": _now()})
        db.commit()
        return None


def get_reply_channel_type(
    db: Session,
    conversation_id: str,
) -> ChannelType | None:
    """Determine channel type for replying to a conversation.

    Returns the channel type of the last inbound message, or None if no inbound messages exist.
    """
    last_inbound = (
        db.query(Message)
        .filter(Message.conversation_id == coerce_uuid(conversation_id))
        .filter(Message.direction == MessageDirection.inbound)
        .order_by(
            func.coalesce(
                Message.received_at,
                Message.sent_at,
                Message.created_at,
            ).desc()
        )
        .first()
    )
    return last_inbound.channel_type if last_inbound else None


def unassign_conversation(
    db: Session,
    conversation_id: str,
) -> None:
    """Remove active assignment from a conversation."""
    db.query(ConversationAssignment).filter(
        ConversationAssignment.conversation_id == coerce_uuid(conversation_id),
        ConversationAssignment.is_active.is_(True),
    ).update({"is_active": False, "updated_at": _now()})
    db.commit()


# Singleton instances
conversations = Conversations()
conversation_assignments = ConversationAssignments()
conversation_tags = ConversationTags()
messages = Messages()
