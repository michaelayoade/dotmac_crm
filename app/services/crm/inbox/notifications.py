from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ConversationStatus, MessageDirection
from app.models.crm.team import CrmAgent
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.services.common import coerce_uuid
from app.services.settings_spec import resolve_value


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


def _coerce_int(value: Any) -> int:
    if isinstance(value, int | str | bytes | bytearray):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return 0


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


def _set_reminder_state(
    db: Session,
    conversation: Conversation,
    message_id: str,
    reminder_at: datetime | None,
) -> None:
    metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
    reminder_state = metadata.get("reply_reminder")
    if not isinstance(reminder_state, dict):
        reminder_state = {}
    reminder_state["last_inbound_message_id"] = str(message_id)
    reminder_state["last_reminder_at"] = reminder_at.isoformat() if reminder_at else None
    metadata["reply_reminder"] = reminder_state
    conversation.metadata_ = dict(metadata)
    db.add(conversation)
    db.commit()


logger = get_logger(__name__)


def notify_assigned_agent_new_reply(db: Session, conversation: Conversation, message: Message) -> None:
    if message.direction != MessageDirection.inbound:
        return
    if conversation.status == ConversationStatus.resolved:
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
    _set_reminder_state(db, conversation, str(message.id), None)


def send_reply_reminders(db: Session) -> int:
    delay_seconds = resolve_value(db, SettingDomain.notification, "crm_inbox_reply_reminder_delay_seconds")
    repeat_enabled = resolve_value(db, SettingDomain.notification, "crm_inbox_reply_reminder_repeat_enabled")
    repeat_interval_seconds = resolve_value(
        db,
        SettingDomain.notification,
        "crm_inbox_reply_reminder_repeat_interval_seconds",
    )

    delay_seconds = _coerce_int(delay_seconds)
    if delay_seconds <= 0:
        return 0

    now = datetime.now(UTC)
    threshold = now - timedelta(seconds=delay_seconds)
    repeat_interval_seconds = _coerce_int(repeat_interval_seconds)

    last_message_ts = func.coalesce(
        Message.received_at,
        Message.sent_at,
        Message.created_at,
    )
    latest_message_subq = db.query(
        Message.conversation_id.label("conv_id"),
        Message.id.label("message_id"),
        Message.body.label("body"),
        Message.subject.label("subject"),
        Message.channel_type.label("channel_type"),
        Message.direction.label("direction"),
        last_message_ts.label("last_message_at"),
        func.row_number()
        .over(
            partition_by=Message.conversation_id,
            order_by=last_message_ts.desc(),
        )
        .label("rnk"),
    ).subquery()

    latest_assignment_subq = (
        db.query(
            ConversationAssignment.conversation_id.label("conv_id"),
            ConversationAssignment.agent_id.label("agent_id"),
            func.row_number()
            .over(
                partition_by=ConversationAssignment.conversation_id,
                order_by=(
                    ConversationAssignment.assigned_at.desc().nullslast(),
                    ConversationAssignment.created_at.desc(),
                ),
            )
            .label("rnk"),
        )
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.agent_id.isnot(None))
        .subquery()
    )

    rows = (
        db.query(
            Conversation,
            latest_message_subq.c.message_id,
            latest_message_subq.c.body,
            latest_message_subq.c.subject,
            latest_message_subq.c.channel_type,
            latest_message_subq.c.last_message_at,
            CrmAgent.person_id.label("agent_person_id"),
        )
        .join(
            latest_message_subq,
            and_(
                latest_message_subq.c.conv_id == Conversation.id,
                latest_message_subq.c.rnk == 1,
            ),
        )
        .join(
            latest_assignment_subq,
            and_(
                latest_assignment_subq.c.conv_id == Conversation.id,
                latest_assignment_subq.c.rnk == 1,
            ),
        )
        .join(CrmAgent, CrmAgent.id == latest_assignment_subq.c.agent_id)
        .filter(CrmAgent.is_active.is_(True))
        .filter(CrmAgent.person_id.isnot(None))
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.status != ConversationStatus.resolved)
        .filter(latest_message_subq.c.direction == MessageDirection.inbound)
        .filter(latest_message_subq.c.last_message_at <= threshold)
        .all()
    )

    sent = 0
    for (
        conversation,
        message_id,
        body,
        subject,
        channel_type,
        last_message_at,
        agent_person_id,
    ) in rows:
        if not agent_person_id:
            continue
        current_assignee = _active_agent_person_id(db, str(conversation.id))
        if not current_assignee or current_assignee != str(agent_person_id):
            continue
        metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
        reminder_state = metadata.get("reply_reminder")
        if not isinstance(reminder_state, dict):
            reminder_state = {}
        last_inbound_id = reminder_state.get("last_inbound_message_id")
        last_reminder_raw = reminder_state.get("last_reminder_at")
        last_reminder_at = None
        if isinstance(last_reminder_raw, str) and last_reminder_raw.strip():
            try:
                last_reminder_at = datetime.fromisoformat(last_reminder_raw.strip())
            except ValueError:
                last_reminder_at = None

        should_send = False
        if last_inbound_id != str(message_id):
            # New inbound message since last reminder: always send first reminder.
            should_send = True
        elif not last_reminder_at:
            # No reminder sent yet for this inbound message.
            should_send = True
        elif repeat_enabled:
            should_send = (
                repeat_interval_seconds > 0 and (now - last_reminder_at).total_seconds() >= repeat_interval_seconds
            )
        if not should_send:
            continue

        person = db.get(Person, conversation.person_id)
        contact_name = _contact_name(person)
        preview_text = (body or subject or "").strip()
        if len(preview_text) > 140:
            preview_text = preview_text[:137].rstrip() + "..."

        payload = {
            "kind": "reminder",
            "title": "Awaiting your reply",
            "subtitle": contact_name,
            "preview": preview_text or None,
            "conversation_id": str(conversation.id),
            "contact_id": str(conversation.person_id),
            "message_id": str(message_id),
            "channel": channel_type.value if channel_type else None,
            "last_message_at": (last_message_at.isoformat() if last_message_at else None),
        }
        from app.websocket.broadcaster import broadcast_agent_notification

        logger.info(
            "crm_inbox_reply_reminder_send conversation_id=%s agent_person_id=%s message_id=%s",
            conversation.id,
            agent_person_id,
            message_id,
        )
        broadcast_agent_notification(str(agent_person_id), payload)
        _set_reminder_state(db, conversation, str(message_id), now)
        sent += 1

    return sent
