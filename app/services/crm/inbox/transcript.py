"""Send email transcript of a conversation."""

from __future__ import annotations

import logging
import os

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.services.common import coerce_uuid
from app.services.crm.inbox.formatting import (
    format_conversation_for_template,
    format_message_for_template,
)

logger = logging.getLogger(__name__)


def send_conversation_transcript(
    db: Session,
    conversation_id: str,
    to_email: str,
    actor_id: str | None = None,
) -> tuple[bool, str | None]:
    """Send a conversation transcript to the given email address.

    Returns (success, error_message).
    """
    conv = db.get(Conversation, coerce_uuid(conversation_id))
    if not conv:
        return False, "Conversation not found"

    conversation = format_conversation_for_template(conv, db, include_inbox_label=True)

    messages_raw = sorted(
        conv.messages or [],
        key=lambda m: m.received_at or m.sent_at or m.created_at,
    )
    messages = [format_message_for_template(m, db) for m in messages_raw]

    _templates_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "templates")
    env = Environment(loader=FileSystemLoader(os.path.abspath(_templates_dir)), autoescape=True)
    template = env.get_template("email/conversation_transcript.html")
    html_body = template.render(
        conversation=conversation,
        messages=messages,
    )

    contact_name = conversation.get("contact", {}).get("name", "Unknown")
    subject = f"Conversation Transcript: {contact_name}"
    if conversation.get("subject"):
        subject = f"Transcript: {conversation['subject']}"

    try:
        from app.services.email import send_email

        send_email(
            db,
            to_email=to_email,
            subject=subject,
            body_html=html_body,
        )
    except Exception as exc:
        logger.exception("Failed to send transcript email to %s", to_email)
        return False, str(exc)

    try:
        from app.services.crm.inbox.audit import log_conversation_action

        log_conversation_action(
            db,
            conversation_id=conversation_id,
            action="transcript_sent",
            actor_id=actor_id,
            metadata={"to_email": to_email},
        )
    except Exception:
        logger.exception("Failed to log transcript audit for conversation %s", conversation_id)

    return True, None
