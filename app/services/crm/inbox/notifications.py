from __future__ import annotations

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ConversationStatus, MessageDirection
from app.models.crm.team import CrmAgent
from app.models.person import Person
from app.services.common import coerce_uuid
from app.services.crm.inbox.agents import resolve_mentioned_person_ids_for_inbox


def _contact_name(person: Person | None) -> str | None:
    if not person:
        return None
    if person.display_name:
        return person.display_name
    name = f"{person.first_name} {person.last_name}".strip()
    return name or None


def _message_preview(message: Message, limit: int = 140) -> str | None:
    text = (message.body or message.subject or "").strip()
    if not text:
        return None
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _active_agent_person_id(db: Session, conversation_id: str) -> str | None:
    assignment = (
        db.query(ConversationAssignment, CrmAgent)
        .join(CrmAgent, CrmAgent.id == ConversationAssignment.agent_id)
        .filter(ConversationAssignment.conversation_id == coerce_uuid(conversation_id))
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.agent_id.isnot(None))
        .filter(CrmAgent.is_active.is_(True))
        .order_by(
            ConversationAssignment.assigned_at.desc().nullslast(),
            ConversationAssignment.created_at.desc(),
        )
        .first()
    )
    if not assignment:
        return None
    agent = assignment[1]
    return str(agent.person_id) if agent and agent.person_id else None


logger = get_logger(__name__)


def notify_agents_mentioned(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
    mentioned_agent_ids: list[str] | None,
    actor_person_id: str | None = None,
) -> None:
    """Notify one or more agents that they were @mentioned.

    This uses websocket `AGENT_NOTIFICATION` events (same surface as inbox reply/reminder).
    """
    if not mentioned_agent_ids:
        return
    # Only agent-authored messages should generate mentions.
    if message.direction == MessageDirection.inbound:
        return

    actor_uuid = None
    if actor_person_id:
        try:
            actor_uuid = str(coerce_uuid(actor_person_id))
        except Exception:
            actor_uuid = None

    recipient_person_ids = []
    seen = set()
    for pid in resolve_mentioned_person_ids_for_inbox(db, mentioned_agent_ids):
        if actor_uuid and pid == actor_uuid:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        recipient_person_ids.append(pid)
    if not recipient_person_ids:
        return

    contact = db.get(Person, conversation.person_id) if conversation.person_id else None
    payload = {
        "kind": "mention",
        "title": "Mentioned",
        "subtitle": _contact_name(contact),
        "preview": _message_preview(message),
        "conversation_id": str(conversation.id),
        "contact_id": str(conversation.person_id) if conversation.person_id else None,
        "message_id": str(message.id),
        "channel": message.channel_type.value if message.channel_type else None,
        "last_message_at": (
            (message.received_at or message.created_at).isoformat()
            if message.received_at or message.created_at
            else None
        ),
    }
    from app.websocket.broadcaster import broadcast_agent_notification

    for person_id in recipient_person_ids:
        broadcast_agent_notification(person_id, payload)
    try:
        from app.services.agent_mentions import queue_mention_email_notifications

        queue_mention_email_notifications(db, recipient_person_ids=recipient_person_ids, payload=payload)
    except Exception:  # nosec B110 — email mention notifications are best-effort
        logger.debug("mention_email_notification_failed")


def notify_assigned_agent_new_reply(db: Session, conversation: Conversation, message: Message) -> None:
    if message.direction != MessageDirection.inbound:
        return
    if conversation.status in {ConversationStatus.resolved, ConversationStatus.resolved_to_ticket}:
        return
    if getattr(conversation, "is_muted", False):
        return
    agent_person_id = _active_agent_person_id(db, str(conversation.id))
    if not agent_person_id:
        return
    person = db.get(Person, conversation.person_id)
    contact_name = _contact_name(person)
    payload = {
        "kind": "reply",
        "title": "New reply",
        "subtitle": contact_name,
        "preview": _message_preview(message),
        "conversation_id": str(conversation.id),
        "contact_id": str(conversation.person_id),
        "message_id": str(message.id),
        "channel": message.channel_type.value if message.channel_type else None,
        "last_message_at": (
            (message.received_at or message.created_at).isoformat()
            if message.received_at or message.created_at
            else None
        ),
    }
    from app.websocket.broadcaster import broadcast_agent_notification

    broadcast_agent_notification(agent_person_id, payload)


def send_reply_reminders(db: Session) -> int:
    """Compatibility wrapper for callers of the retired message-table scan."""
    from app.services.crm.inbox.response_obligations import process_due_response_obligations

    return process_due_response_obligations(db)["notified"]
