"""Shared utilities for inbound handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.models.person import Person
from app.services.crm.ai_intake import make_scope_key, process_pending_intake
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox.context import get_inbox_logger
from app.services.crm.inbox.notifications import notify_assigned_agent_new_reply
from app.services.crm.inbox.routing import apply_routing_rules
from app.websocket.broadcaster import (
    broadcast_agent_notification,
    broadcast_conversation_summary,
    broadcast_inbox_updated,
    broadcast_new_message,
)

logger = get_inbox_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


_CALL_CONNECT_STATES = {"connect", "ringing", "ring", "incoming", "invited", "calling"}
_CALL_ACTIVE_STATES = {"accepted", "connected", "in_progress", "ongoing", "active"}
_CALL_TERMINAL_STATES = {
    "completed",
    "ended",
    "terminated",
    "rejected",
    "failed",
    "missed",
    "busy",
    "no_answer",
    "canceled",
    "cancelled",
    "timeout",
    "terminate",
}


def _extract_call_event_metadata(message: Message) -> dict[str, str] | None:
    if message.channel_type != ChannelType.whatsapp:
        return None
    metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
    raw_call_value = metadata.get("call")
    raw_call: dict[str, Any] = raw_call_value if isinstance(raw_call_value, dict) else {}

    call_id = metadata.get("call_id") or raw_call.get("call_id") or raw_call.get("id")
    call_status = (
        metadata.get("call_status") or raw_call.get("call_status") or raw_call.get("event") or raw_call.get("status")
    )
    phone_number_id = metadata.get("phone_number_id")
    call_to = metadata.get("to") or raw_call.get("to")

    normalized_call_id = str(call_id).strip() if isinstance(call_id, str) and call_id.strip() else None
    normalized_status = (
        str(call_status).strip().lower() if isinstance(call_status, str) and call_status.strip() else None
    )
    normalized_phone_number_id = (
        str(phone_number_id).strip() if isinstance(phone_number_id, str) and phone_number_id.strip() else None
    )
    normalized_call_to = str(call_to).strip() if isinstance(call_to, str) and call_to.strip() else None
    if not normalized_call_id or not normalized_status:
        return None
    return {
        "call_id": normalized_call_id,
        "call_status": normalized_status,
        "phone_number_id": normalized_phone_number_id or "",
        "call_to": normalized_call_to or "",
    }


def _build_call_notification_actions(call_status: str) -> list[str]:
    if call_status in _CALL_CONNECT_STATES:
        return ["accept"]
    if call_status in _CALL_ACTIVE_STATES:
        return ["terminate"]
    if call_status in _CALL_TERMINAL_STATES:
        return []
    return []


def _contact_name(person: Person | None) -> str | None:
    if not person:
        return None
    if person.display_name:
        return person.display_name
    name = f"{person.first_name} {person.last_name}".strip()
    return name or None


def _message_order_timestamp(message: Message) -> datetime:
    """Use receive/send time when present, but never older than DB creation time."""
    base = message.received_at or message.sent_at or message.created_at or _now()
    # created_at may not be populated before flush; use current time as floor.
    created = message.created_at or _now()
    return created if created > base else base


def build_conversation_summary(db: Session, conversation: Conversation, message: Message) -> dict:
    unread_count = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.status == MessageStatus.received)
        .filter(Message.read_at.is_(None))
        .count()
    )
    last_message_at = _message_order_timestamp(message)
    preview = message.body or ""
    if len(preview) > 100:
        preview = preview[:100] + "..."
    return {
        "preview": preview,
        "last_message_at": last_message_at.isoformat() if last_message_at else None,
        "channel": message.channel_type.value if message.channel_type else None,
        "unread_count": unread_count,
    }


def post_process_inbound_message(
    db: Session,
    *,
    conversation_id: str,
    message_id: str,
    channel_target_id: str | None,
) -> None:
    try:
        conversation = db.get(Conversation, conversation_id)
        message = db.get(Message, message_id)
        if not conversation or not message:
            return

        intake_result = None
        if message.direction == MessageDirection.inbound:
            scope_key = make_scope_key(
                channel_type=message.channel_type,
                target_id=str(message.channel_target_id) if message.channel_target_id else None,
                widget_config_id=(
                    str(message.metadata_.get("widget_config_id"))
                    if isinstance(message.metadata_, dict) and message.metadata_.get("widget_config_id")
                    else None
                ),
            )
            intake_result = process_pending_intake(
                db,
                conversation=conversation,
                message=message,
                scope_key=scope_key,
            )
            if not intake_result.handled:
                apply_routing_rules(db, conversation=conversation, message=message)
        broadcast_new_message(message, conversation)
        pending_ai_intake = bool(
            intake_result and intake_result.handled and conversation.status == ConversationStatus.pending
        )
        call_event = _extract_call_event_metadata(message)
        contact = db.get(Person, conversation.person_id) if conversation.person_id else None
        contact_name = _contact_name(contact)
        if not pending_ai_intake and call_event is None:
            notify_assigned_agent_new_reply(db, conversation, message)
        broadcast_conversation_summary(
            str(conversation.id),
            build_conversation_summary(db, conversation, message),
        )

        # Notify agents assigned to this conversation or on the same team
        agent_person_ids: set[str] = set()
        assignments = (
            db.query(ConversationAssignment.agent_id, ConversationAssignment.team_id)
            .filter(ConversationAssignment.conversation_id == conversation.id)
            .filter(ConversationAssignment.is_active.is_(True))
            .all()
        )
        assigned_agent_ids = {a.agent_id for a in assignments if a.agent_id}
        team_ids = {a.team_id for a in assignments if a.team_id}

        # Batch-load team members
        if team_ids:
            team_agent_ids = {
                row[0]
                for row in db.query(CrmAgentTeam.agent_id)
                .filter(CrmAgentTeam.team_id.in_(team_ids))
                .filter(CrmAgentTeam.is_active.is_(True))
                .all()
            }
            assigned_agent_ids |= team_agent_ids

        # Batch-load person_ids for all agents in one query
        if assigned_agent_ids:
            agent_rows = (
                db.query(CrmAgent.person_id)
                .filter(CrmAgent.id.in_(assigned_agent_ids))
                .filter(CrmAgent.person_id.isnot(None))
                .all()
            )
            agent_person_ids = {str(row[0]) for row in agent_rows}
        # Fallback: if no specific agents, notify all active agents unless AI intake is still pending.
        if not agent_person_ids and not pending_ai_intake:
            all_agents = (
                db.query(CrmAgent.person_id)
                .filter(CrmAgent.is_active.is_(True))
                .filter(CrmAgent.person_id.isnot(None))
                .distinct()
                .all()
            )
            agent_person_ids = {str(row[0]) for row in all_agents}
        inbox_payload = {
            "conversation_id": str(conversation.id),
            "message_id": str(message.id),
            "channel_target_id": channel_target_id,
            "last_message_at": _message_order_timestamp(message).isoformat(),
        }
        for person_id in agent_person_ids:
            broadcast_inbox_updated(person_id, inbox_payload)
            if call_event is not None:
                actions = _build_call_notification_actions(call_event["call_status"])
                if not actions and call_event["call_status"] not in _CALL_TERMINAL_STATES:
                    continue
                call_payload = {
                    "kind": "whatsapp_call",
                    "title": "Incoming WhatsApp call",
                    "subtitle": contact_name or "Incoming call",
                    "preview": f"Call status: {call_event['call_status']}",
                    "conversation_id": str(conversation.id),
                    "contact_id": str(conversation.person_id) if conversation.person_id else None,
                    "message_id": str(message.id),
                    "channel": "whatsapp",
                    "last_message_at": _message_order_timestamp(message).isoformat(),
                    "call_id": call_event["call_id"],
                    "call_status": call_event["call_status"],
                    "phone_number_id": call_event["phone_number_id"] or None,
                    "call_to": call_event["call_to"] or None,
                    "call_actions": actions,
                }
                broadcast_agent_notification(person_id, call_payload)

        inbox_cache.invalidate_inbox_list()
    except Exception as exc:
        logger.warning("inbound_post_process_failed error=%s", exc)


def create_message_and_touch_conversation(
    db: Session,
    *,
    conversation_id,
    payload: dict[str, Any],
) -> tuple[Conversation, Message]:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise ValueError("Conversation not found")

    message = Message(**payload)
    if message.direction == MessageDirection.inbound and not message.received_at:
        message.received_at = _now()
    if message.direction == MessageDirection.outbound and not message.sent_at:
        message.sent_at = _now()
    db.add(message)
    timestamp = _message_order_timestamp(message)
    conversation.last_message_at = timestamp
    conversation.updated_at = timestamp
    db.flush()
    from app.services.crm.inbox.summaries import recompute_conversation_summary

    recompute_conversation_summary(db, str(conversation.id))
    return conversation, message
