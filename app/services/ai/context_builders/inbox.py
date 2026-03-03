from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment, ConversationTag, Message
from app.models.person import Person
from app.services.ai.redaction import redact_text
from app.services.branding import get_branding
from app.services.common import coerce_uuid


class _HTMLStripper(HTMLParser):
    """Lightweight HTML-to-text converter."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"style", "script", "head"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"style", "script", "head"}:
            self._skip = False
        if tag in {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        return re.sub(r"\s+", " ", raw).strip()


def _strip_html(text: str) -> str:
    """Strip HTML tags and return plain text. Fast-path for non-HTML."""
    if "<" not in text:
        return text
    try:
        stripper = _HTMLStripper()
        stripper.feed(text)
        return stripper.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", text)


def gather_inbox_context(db: Session, params: dict[str, Any]) -> str:
    conversation_id = params.get("conversation_id")
    if not conversation_id:
        raise ValueError("conversation_id is required")

    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation:
        raise ValueError("Conversation not found")

    max_messages = min(int(params.get("max_messages", 12)), 30)
    max_chars = int(params.get("max_chars_per_message", 600))

    # ── Company identity ──────────────────────────────────────
    branding = get_branding(db)
    company_name = branding.get("company_name") or "Dotmac"

    # ── Contact info ──────────────────────────────────────────
    contact: Person | None = None
    if conversation.person_id:
        contact = db.get(Person, conversation.person_id)

    # ── Channel type (from most recent message) ───────────────
    latest_msg = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(1)
        .first()
    )
    channel = latest_msg.channel_type.value if latest_msg and latest_msg.channel_type else "unknown"

    # ── Conversation messages ─────────────────────────────────
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(max(1, max_messages))
        .all()
    )
    messages = list(reversed(messages))

    # ── Tags ──────────────────────────────────────────────────
    tags = db.query(ConversationTag.tag).filter(ConversationTag.conversation_id == conversation.id).all()
    tag_list = [t[0] for t in tags]

    # ── Linked ticket ─────────────────────────────────────────
    linked_ticket_info = ""
    if conversation.ticket_id:
        from app.models.tickets import Ticket

        ticket = db.get(Ticket, conversation.ticket_id)
        if ticket:
            ticket_ref = ticket.number or str(ticket.id)[:8]
            parts = [f"Linked ticket: #{ticket_ref}"]
            if ticket.title:
                parts.append(f'"{ticket.title}"')
            parts.append(f"status={ticket.status.value}")
            if ticket.ticket_type:
                ticket_type = ticket.ticket_type.value if hasattr(ticket.ticket_type, "value") else str(ticket.ticket_type)
                parts.append(f"type={ticket_type}")
            if ticket.priority:
                parts.append(f"priority={ticket.priority.value}")
            linked_ticket_info = " | ".join(parts)

    # ── Assigned agent ────────────────────────────────────────
    assignment = (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id, ConversationAssignment.is_active.is_(True))
        .first()
    )
    agent_name = ""
    if assignment and assignment.agent_id:
        from app.models.crm.team import CrmAgent

        agent = db.get(CrmAgent, assignment.agent_id)
        if agent and agent.person_id:
            agent_person = db.get(Person, agent.person_id)
            if agent_person:
                agent_name = agent_person.display_name or ""

    # ── Build context ─────────────────────────────────────────
    lines: list[str] = []

    lines.append(f"Company: {company_name}")
    lines.append(f"Channel: {channel}")
    lines.append(f"Conversation status: {conversation.status.value}")

    if conversation.priority and conversation.priority.value != "none":
        lines.append(f"Priority: {conversation.priority.value}")
    if conversation.subject:
        lines.append(f"Subject: {conversation.subject}")

    if contact:
        contact_name = redact_text(contact.display_name or "", max_chars=120)
        lines.append(f"Contact: {contact_name}")
    if agent_name:
        lines.append(f"Assigned agent: {agent_name}")
    if tag_list:
        lines.append(f"Tags: {', '.join(tag_list[:8])}")
    if linked_ticket_info:
        lines.append(linked_ticket_info)

    lines.append("")
    lines.append("Messages:")
    for msg in messages:
        direction = getattr(msg.direction, "value", str(msg.direction))
        role = "customer" if direction == "inbound" else "agent"
        body = _strip_html(msg.body or "")
        body = redact_text(body, max_chars=max_chars)
        if body:
            lines.append(f"  {role}: {body}")

    return "\n".join(lines)
