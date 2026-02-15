from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, Message
from app.models.person import Person
from app.services.ai.redaction import redact_text
from app.services.common import coerce_uuid


def gather_inbox_context(db: Session, params: dict[str, Any]) -> str:
    conversation_id = params.get("conversation_id")
    if not conversation_id:
        raise ValueError("conversation_id is required")

    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation:
        raise ValueError("Conversation not found")

    max_messages = min(int(params.get("max_messages", 12)), 30)
    max_chars = int(params.get("max_chars_per_message", 600))

    contact: Person | None = None
    if conversation.person_id:
        contact = db.get(Person, conversation.person_id)

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(max(1, max_messages))
        .all()
    )
    messages = list(reversed(messages))

    lines = []
    if contact:
        lines.append(f"Contact: {redact_text(contact.display_name or '', max_chars=120)}")
    lines.append(f"Conversation ID: {str(conversation.id)[:8]}")
    lines.append("Messages:")
    for msg in messages:
        direction = getattr(msg.direction, "value", str(msg.direction))
        role = "customer" if direction == "inbound" else "agent"
        body = redact_text(msg.body or "", max_chars=max_chars)
        if body:
            lines.append(f"  {role}: {body}")

    return "\n".join(lines)
