"""Formatting helpers for CRM inbox admin UI."""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import settings
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection
from app.models.integration import IntegrationTarget
from app.models.person import Person
from app.models.subscriber import Organization
from app.models.tickets import Ticket
from app.services import time_preferences
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.comments_summary import (
    merge_recent_conversations_with_comments,
)
from app.services.crm.inbox.permissions import can_view_private_note
from app.services.person_identity import preferred_meta_display_name

logger = logging.getLogger(__name__)
_URL_RE = re.compile(r"(https?://[^\s<]+)", flags=re.IGNORECASE)


def _localize_inbox_datetime(value: datetime | None, db: Session) -> tuple[datetime | None, str, str]:
    """Convert timestamps to the configured company timezone for inbox rendering."""
    timezone, date_format, time_format, _ = time_preferences.resolve_company_time_prefs(db)
    if value is None:
        return None, date_format, time_format
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(timezone)), date_format, time_format


def _format_inbox_time_label(value: datetime | None, db: Session) -> str:
    local_value, _date_format, time_format = _localize_inbox_datetime(value, db)
    if local_value is None:
        return ""
    return local_value.strftime(time_format)


def _format_inbox_datetime_label(value: datetime | None, db: Session) -> str:
    local_value, date_format, time_format = _localize_inbox_datetime(value, db)
    if local_value is None:
        return ""
    return local_value.strftime(f"{date_format} {time_format}")


def _derive_failure_reason_label(
    metadata: dict | None,
    *,
    status: str | None,
    channel_type: ChannelType | None,
) -> str | None:
    """Build a user-facing failed-send reason from message metadata."""
    if status != "failed" or not isinstance(metadata, dict):
        return None

    send_error = metadata.get("send_error")
    if not isinstance(send_error, dict):
        return "Message not sent"

    error_bits: list[str] = []
    for key in ("error", "response_text", "meta_error"):
        value = send_error.get(key)
        if isinstance(value, str) and value.strip():
            error_bits.append(value.strip())
    error_text = " ".join(error_bits).lower()
    if not error_text:
        return "Message not sent"

    if "over 1,000 characters" in error_text or "over 1000 characters" in error_text:
        if channel_type == ChannelType.instagram_dm:
            return "Not sent: Instagram message exceeded 1,000 characters"
        return "Not sent: Message exceeded the channel character limit"
    if "meta reply window expired" in error_text or "24-hour" in error_text or "24 hour" in error_text:
        return "Not sent: Meta reply window expired"
    return "Message not sent"


def get_initials(name: str | None) -> str:
    """Generate initials from a name."""
    if not name:
        return "?"
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[0:2].upper() if len(name) >= 2 else name[0].upper()


def format_conversation_ticket(conv: Conversation, db: Session) -> dict | None:
    """Return linked ticket details for inbox templates when available."""
    if not getattr(conv, "ticket_id", None):
        return None
    ticket = db.get(Ticket, conv.ticket_id)
    if not ticket:
        ticket_ref = str(conv.ticket_id)
        return {
            "id": ticket_ref,
            "reference": ticket_ref,
            "href": f"/admin/support/tickets/{ticket_ref}",
        }
    ticket_ref = ticket.number or str(ticket.id)
    return {
        "id": str(ticket.id),
        "reference": ticket_ref,
        "href": f"/admin/support/tickets/{ticket_ref}",
    }


def _safe_message_metadata(message: Message) -> dict:
    return message.metadata_ if isinstance(message.metadata_, dict) else {}


def _resolve_call_accepting_agent(
    db: Session,
    msg: Message,
    call_id: str | None,
    current_name: str | None,
    current_person_id: str | None,
) -> tuple[str | None, str | None]:
    if current_name or current_person_id:
        return current_name, current_person_id

    normalized_call_id = (call_id or "").strip()
    if not normalized_call_id:
        return current_name, current_person_id

    related_messages = (
        db.query(Message)
        .filter(Message.conversation_id == msg.conversation_id)
        .filter(Message.channel_type == ChannelType.whatsapp)
        .filter((Message.external_id == normalized_call_id) | (Message.external_id.like(f"{normalized_call_id}::%")))
        .order_by(Message.created_at.desc())
        .limit(10)
        .all()
    )
    for related in related_messages:
        metadata = _safe_message_metadata(related)
        raw_related_call = metadata.get("call")
        related_call: dict[str, object] = raw_related_call if isinstance(raw_related_call, dict) else {}
        accepted_by_name = metadata.get("accepted_by_name") or related_call.get("accepted_by_name")
        accepted_by_person_id = metadata.get("accepted_by_person_id") or related_call.get("accepted_by_person_id")
        if isinstance(accepted_by_name, str):
            accepted_by_name = accepted_by_name.strip() or None
        else:
            accepted_by_name = None
        if isinstance(accepted_by_person_id, str):
            accepted_by_person_id = accepted_by_person_id.strip() or None
        else:
            accepted_by_person_id = None
        if accepted_by_name or accepted_by_person_id:
            return accepted_by_name, accepted_by_person_id

    return current_name, current_person_id


class _MessageHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._allowed_tags = {
            "p",
            "br",
            "strong",
            "b",
            "em",
            "i",
            "u",
            "ul",
            "ol",
            "li",
            "table",
            "thead",
            "tbody",
            "tr",
            "td",
            "th",
            "span",
            "div",
            "a",
        }
        self._self_closing = {"br"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self._allowed_tags:
            return
        safe_attrs: list[str] = []
        if tag == "a":
            href = None
            target = None
            for key, value in attrs:
                if key == "href" and value:
                    href = value
                if key == "target" and value:
                    target = value
            if href and _is_safe_url(href):
                safe_attrs.append(f'href="{html.escape(href, quote=True)}"')
            if target:
                safe_attrs.append(f'target="{html.escape(target, quote=True)}"')
            safe_attrs.append('rel="noreferrer noopener"')
        if tag in {"td", "th"}:
            for key, value in attrs:
                if key in {"colspan", "rowspan"} and value:
                    safe_attrs.append(f'{key}="{html.escape(value, quote=True)}"')
        attr_text = f" {' '.join(safe_attrs)}" if safe_attrs else ""
        if tag in self._self_closing:
            self._parts.append(f"<{tag}{attr_text} />")
        else:
            self._parts.append(f"<{tag}{attr_text}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._allowed_tags and tag not in self._self_closing:
            self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not data:
            return
        escaped = html.escape(data)
        if "\n" in escaped:
            escaped = "<br />".join(escaped.splitlines())
        self._parts.append(escaped)

    def handle_entityref(self, name: str) -> None:
        self._parts.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self._parts.append(html.unescape(f"&#{name};"))

    def get_html(self) -> str:
        return "".join(self._parts).strip()


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme in {"http", "https", "mailto", "tel"}:
        return True
    if parsed.scheme in {"javascript", "data", "vbscript"}:
        return False
    return parsed.scheme == ""


def _sanitize_message_html(value: str) -> str:
    if not value:
        return ""
    sanitizer = _MessageHTMLSanitizer()
    sanitizer.feed(value)
    sanitizer.close()
    return sanitizer.get_html()


def _linkify_plain_text(value: str) -> str:
    """Escape plain text and convert safe URLs to clickable links."""
    if not value:
        return ""
    escaped = html.escape(value)

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(1)
        trailing = ""
        while raw and raw[-1] in ".,!?;:)]":
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        if not raw or not _is_safe_url(raw):
            return match.group(0)
        href = html.escape(raw, quote=True)
        label = html.escape(raw)
        return (
            f'<a href="{href}" target="_blank" rel="noreferrer noopener" '
            f'style="text-decoration:underline;cursor:pointer;pointer-events:auto;">{label}</a>{trailing}'
        )

    return _URL_RE.sub(_replace, escaped)


def _normalize_storage_attachment_url(url: str | None) -> str | None:
    if not url or settings.storage_backend != "s3":
        return url
    if url.startswith("/admin/storage/"):
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    path = (parsed.path or url).lstrip("/")
    bucket_prefix = f"{settings.s3_bucket}/"
    if path.startswith(bucket_prefix):
        key = path[len(bucket_prefix) :]
    elif path.startswith("uploads/"):
        key = path
    else:
        return url
    if not key.startswith("uploads/"):
        return url
    return f"/admin/storage/{settings.s3_bucket}/{key}"


def _normalize_cid(value: str | None) -> str | None:
    if not value:
        return None
    cid = value.strip()
    if cid.startswith("cid:"):
        cid = cid[4:]
    if cid.startswith("<") and cid.endswith(">"):
        cid = cid[1:-1]
    return cid.strip().lower() or None


def _replace_cid_images(html_body: str, attachments: list[dict]) -> str:
    if not html_body:
        return html_body
    cid_map: dict[str, str] = {}
    for att in attachments:
        cid = _normalize_cid(att.get("content_id"))
        url = att.get("url")
        if not cid or not url:
            continue
        mime_type = att.get("mime_type") or ""
        if not str(mime_type).startswith("image/"):
            continue
        cid_map[cid] = url
    if not cid_map:
        return html_body

    def _swap(match: re.Match) -> str:
        raw = match.group(1) or ""
        key = _normalize_cid(raw)
        if not key or key not in cid_map:
            return match.group(0)
        return cid_map[key]

    return re.sub(r"cid:([^\"'\\s>]+)", _swap, html_body, flags=re.IGNORECASE)


def filter_messages_for_user(
    messages: list[dict],
    current_user_id: str | None,
    current_roles: list[str] | None,
) -> list[dict]:
    if not messages:
        return []
    user_id = current_user_id or ""
    roles = set(current_roles or [])
    filtered: list[dict] = []
    for msg in messages:
        if not msg.get("is_private_note"):
            filtered.append(msg)
            continue
        if not can_view_private_note(
            visibility=msg.get("visibility"),
            author_id=msg.get("author_id"),
            actor_id=user_id or None,
            roles=roles,
            scopes=None,
        ):
            continue
        filtered.append(msg)
    return filtered


def format_conversation_for_template(
    conv: Conversation,
    db: Session,
    latest_message: dict | Message | None = None,
    unread_count: int | None = None,
    include_inbox_label: bool = False,
) -> dict:
    """Transform a Conversation model into template-friendly dict."""
    contact = conv.contact

    if latest_message is None:
        latest_message = conversation_service.get_latest_message(db, str(conv.id))

    if unread_count is None:
        unread_count = conversation_service.get_unread_count(db, str(conv.id))

    channel = "email"
    channel_target_id = None
    if latest_message:
        channel_type = (
            latest_message.get("channel_type") if isinstance(latest_message, dict) else latest_message.channel_type
        )
        if channel_type:
            channel = channel_type.value
        if isinstance(latest_message, dict):
            channel_target_id = latest_message.get("channel_target_id")
        else:
            channel_target_id = getattr(latest_message, "channel_target_id", None)
    elif contact and contact.channels:
        channel = contact.channels[0].channel_type.value

    assigned_to = None
    assigned_team = None
    assigned_agent_id = None
    assigned_agent_name = None
    if conv.assignments:
        active_assignment = next((a for a in conv.assignments if a.is_active), None)
        if active_assignment:
            if active_assignment.agent_id:
                assigned_agent_id = str(active_assignment.agent_id)
                assigned_agent_name = "another agent"
            if active_assignment.agent:
                agent = active_assignment.agent
                if agent.person_id:
                    person = db.get(Person, agent.person_id)
                    if person:
                        full_name = (
                            person.display_name
                            or " ".join(part for part in [person.first_name, person.last_name] if part).strip()
                        )
                        assigned_to = {
                            "name": full_name or "Agent",
                            "initials": get_initials(full_name or "Agent"),
                        }
                        assigned_agent_id = str(agent.id)
                        assigned_agent_name = full_name or "Agent"
            if not assigned_to and active_assignment.team:
                team = active_assignment.team
                team_name = team.name or "Team"
                assigned_team = {
                    "name": team_name,
                    "initials": get_initials(team_name),
                }

    company = None
    if contact and contact.organization_id:
        org = db.get(Organization, contact.organization_id)
        if org:
            company = org.name

    preview = "No messages yet"
    if latest_message:
        if isinstance(latest_message, dict):
            body = latest_message.get("body")
            metadata = latest_message.get("metadata")
            message_type = latest_message.get("message_type")
            has_attachments = bool(latest_message.get("has_attachments"))
        else:
            body = latest_message.body
            metadata = latest_message.metadata_ if isinstance(latest_message.metadata_, dict) else None
            message_type = metadata.get("type") if metadata else None
            has_attachments = bool(getattr(latest_message, "attachments", None))

        body_text = body.strip() if isinstance(body, str) else ""
        is_document_placeholder = body_text == "[document message]"
        attachment_name = None
        if isinstance(metadata, dict):
            meta_attachments = metadata.get("attachments")
            if isinstance(meta_attachments, list):
                for meta_attachment in meta_attachments:
                    if not isinstance(meta_attachment, dict):
                        continue
                    payload_value = meta_attachment.get("payload")
                    payload = payload_value if isinstance(payload_value, dict) else {}
                    file_name = (
                        meta_attachment.get("file_name")
                        or payload.get("file_name")
                        or payload.get("filename")
                        or payload.get("name")
                    )
                    if isinstance(file_name, str) and file_name.strip():
                        attachment_name = file_name.strip()
                        break

        if body_text in {"[reaction message]", "[location message]", "[document message]"}:
            body_text = ""
        message_type_value = message_type.lower() if isinstance(message_type, str) else None

        if message_type_value == "location":
            location_label = None
            if isinstance(metadata, dict):
                for key in ("address", "name", "label"):
                    value = metadata.get(key)
                    if isinstance(value, str) and value.strip():
                        location_label = value.strip()
                        break
                if not location_label:
                    loc = metadata.get("location")
                    if not loc:
                        raw = metadata.get("raw")
                        if isinstance(raw, dict):
                            raw_messages = raw.get("messages")
                            if isinstance(raw_messages, list) and raw_messages:
                                first_msg = raw_messages[0]
                                if isinstance(first_msg, dict):
                                    loc = first_msg.get("location")
                    if isinstance(loc, dict):
                        for key in ("address", "name", "label"):
                            value = loc.get(key)
                            if isinstance(value, str) and value.strip():
                                location_label = value.strip()
                                break
                if not location_label:
                    lat = metadata.get("latitude") or metadata.get("lat")
                    lng = metadata.get("longitude") or metadata.get("lng") or metadata.get("lon")
                    if lat is not None and lng is not None:
                        location_label = f"({lat}, {lng})"
                if not location_label and isinstance(loc, dict):
                    lat = loc.get("latitude") or loc.get("lat")
                    lng = loc.get("longitude") or loc.get("lng") or loc.get("lon")
                    if lat is not None and lng is not None:
                        location_label = f"({lat}, {lng})"
            if location_label:
                preview = f"📍 Location: {location_label}"
            else:
                preview = "📍 Location shared"
            if len(preview) > 100:
                preview = preview[:97] + "..."
        elif message_type_value == "reaction":
            reaction_emoji = None
            if isinstance(metadata, dict):
                reaction_emoji = metadata.get("emoji")
                if not reaction_emoji:
                    raw = metadata.get("raw")
                    if isinstance(raw, dict):
                        raw_messages = raw.get("messages")
                        if isinstance(raw_messages, list) and raw_messages:
                            first_msg = raw_messages[0]
                            if isinstance(first_msg, dict):
                                reaction = first_msg.get("reaction")
                                if isinstance(reaction, dict):
                                    reaction_emoji = reaction.get("emoji")
            if isinstance(reaction_emoji, str) and reaction_emoji.strip():
                preview = f"Reaction {reaction_emoji.strip()}"
            else:
                preview = "Reaction received"
        elif message_type_value == "call":
            call_status = None
            call_direction = None
            call_type = None
            if isinstance(metadata, dict):
                call_status = metadata.get("call_status")
                call_direction = metadata.get("call_direction")
                call_type = metadata.get("call_type")
                if not call_status:
                    call = metadata.get("call")
                    if isinstance(call, dict):
                        call_status = call.get("call_status") or call.get("status")
            if call_status and not isinstance(call_status, str):
                call_status = None
            if call_direction and not isinstance(call_direction, str):
                call_direction = None
            if call_type and not isinstance(call_type, str):
                call_type = None

            if call_status and isinstance(call_status, str):
                status_label = call_status.replace("_", " ").replace("-", " ")
            else:
                status_label = None
            direction_label = call_direction.strip().title() if isinstance(call_direction, str) else None
            type_label = call_type.strip().title() if isinstance(call_type, str) else "Call"
            if direction_label and status_label:
                preview = f"☎️ {direction_label} {type_label} ({status_label})"
            elif status_label:
                preview = f"☎️ {type_label} ({status_label})"
            elif direction_label:
                preview = f"☎️ {direction_label} {type_label}"
            else:
                preview = f"☎️ {type_label} event"
        elif is_document_placeholder:
            preview = f"Document: {attachment_name}" if attachment_name else "Document attached"
        elif has_attachments:
            preview = "Attachment (Image/File)"
        elif body_text:
            preview = body_text[:100] + "..." if len(body_text) > 100 else body_text
        else:
            preview = "New message received"
    latest_message_at = None
    if isinstance(latest_message, dict):
        latest_message_at = latest_message.get("last_message_at")
    elif latest_message:
        latest_message_at = latest_message.received_at or latest_message.sent_at or latest_message.created_at

    inbox_label = None
    if include_inbox_label and channel_target_id:
        if isinstance(latest_message, dict):
            inbox_label = latest_message.get("channel_target_name")
        if not inbox_label:
            try:
                target = db.get(IntegrationTarget, coerce_uuid(channel_target_id))
            except Exception:
                target = None
            if target and target.connector_config:
                inbox_label = target.name or target.connector_config.name
    if include_inbox_label and not inbox_label:
        channel_labels = {
            "email": "Email Inbox",
            "whatsapp": "WhatsApp Inbox",
            "facebook_messenger": "Facebook Inbox",
            "instagram_dm": "Instagram Inbox",
            "sms": "SMS Inbox",
            "telegram": "Telegram Inbox",
            "webchat": "Webchat Inbox",
            "phone": "Phone Inbox",
        }
        inbox_label = channel_labels.get(channel, "Inbox")

    if contact:
        phone_value = contact.phone
        if phone_value and channel in ("whatsapp", "sms", "phone") and not phone_value.startswith("+"):
            phone_value = f"+{phone_value}"
        resolved_meta_name = preferred_meta_display_name(contact, channel)
        if channel in ("whatsapp", "sms", "phone"):
            contact_name = contact.display_name or phone_value or contact.email or "Unknown"
        else:
            contact_name = resolved_meta_name or contact.email or phone_value or "Unknown"
        contact_initials = get_initials(contact_name)
    else:
        contact_name = "Unknown"
        contact_initials = "?"
        phone_value = None

    splynx_id = None
    if contact and contact.metadata_:
        splynx_id = contact.metadata_.get("splynx_id")

    snooze = None
    if isinstance(conv.metadata_, dict):
        raw_snooze = conv.metadata_.get("snooze")
        if isinstance(raw_snooze, dict):
            mode = str(raw_snooze.get("mode") or "").strip().lower() or None
            until_at = raw_snooze.get("until_at")
            snooze = {
                "mode": mode,
                "until_at": str(until_at) if until_at else None,
            }

    rendered_last_message_at = conv.last_message_at or latest_message_at or conv.updated_at
    resolution = None
    if isinstance(conv.metadata_, dict):
        raw_resolution = conv.metadata_.get("resolution")
        if isinstance(raw_resolution, dict):
            resolution = {
                "mode": str(raw_resolution.get("mode") or "").strip() or None,
                "label": str(raw_resolution.get("label") or "").strip() or None,
                "ticket_reference": str(raw_resolution.get("ticket_reference") or "").strip() or None,
            }
    ticket = format_conversation_ticket(conv, db)

    return {
        "id": str(conv.id),
        "contact": {
            "id": str(contact.id) if contact else "",
            "name": contact_name,
            "email": contact.email if contact else "",
            "phone": phone_value if contact else "",
            "avatar_initials": contact_initials,
            "company": company,
            "splynx_id": splynx_id,
        },
        "channel": channel,
        "status": conv.status.value if conv.status else "open",
        "priority": conv.priority.value if conv.priority else "none",
        "is_muted": bool(getattr(conv, "is_muted", False)),
        "snooze": snooze,
        "subject": conv.subject,
        "preview": preview,
        "unread_count": unread_count or 0,
        "last_message_at": rendered_last_message_at,
        "last_message_at_label": _format_inbox_time_label(rendered_last_message_at, db),
        "assigned_to": assigned_to,
        "assigned_team": assigned_team,
        "assigned_agent_id": assigned_agent_id,
        "assigned_agent_name": assigned_agent_name,
        "tags": sorted({tag.tag for tag in (conv.tags or []) if tag.tag})[:5],
        "ticket": ticket,
        "resolution": resolution,
        "inbox": {
            "id": str(channel_target_id) if channel_target_id else None,
            "label": inbox_label,
        },
    }


def format_message_for_template(msg: Message, db: Session) -> dict:
    """Transform a Message model into template-friendly dict."""
    sender_name = "Unknown"
    sender_initials = "?"
    sender_is_ai = False
    message_metadata = msg.metadata_ if isinstance(msg.metadata_, dict) else {}
    is_ai_generated = bool(message_metadata.get("ai_intake_generated"))

    if msg.direction == MessageDirection.internal:
        if msg.author_id:
            person = db.get(Person, msg.author_id)
            if person:
                full_name = (
                    person.display_name
                    or " ".join(part for part in [person.first_name, person.last_name] if part).strip()
                )
                sender_name = full_name or "Internal Note"
                sender_initials = get_initials(sender_name)
            else:
                sender_name = "Internal Note"
                sender_initials = "IN"
        else:
            sender_name = "Internal Note"
            sender_initials = "IN"
    elif msg.direction == MessageDirection.outbound:
        if is_ai_generated:
            sender_name = "AI"
            sender_initials = "AI"
            sender_is_ai = True
        elif msg.author_id:
            person = db.get(Person, msg.author_id)
            if person:
                full_name = (
                    person.display_name
                    or " ".join(part for part in [person.first_name, person.last_name] if part).strip()
                )
                sender_name = full_name or "Agent"
                sender_initials = get_initials(sender_name)
        else:
            sender_name = "Agent"
            sender_initials = "AG"
    else:
        conv = msg.conversation
        if conv and conv.contact:
            resolved_meta_name = preferred_meta_display_name(conv.contact, msg.channel_type)
            sender_name = resolved_meta_name or conv.contact.email or "Contact"
            sender_initials = get_initials(sender_name)

    attachments = []
    for attachment in msg.attachments or []:
        metadata = attachment.metadata_ or {}
        content_base64 = metadata.get("content_base64")
        content_id = metadata.get("content_id")
        url = _normalize_storage_attachment_url(attachment.external_url)
        if not url and content_base64 and attachment.mime_type:
            url = f"data:{attachment.mime_type};base64,{content_base64}"
        attachments.append(
            {
                "id": str(attachment.id),
                "file_name": attachment.file_name or "attachment",
                "mime_type": attachment.mime_type or "",
                "file_size": attachment.file_size,
                "url": url,
                "content_id": content_id,
                "is_image": bool(attachment.mime_type and attachment.mime_type.startswith("image/")),
            }
        )

    metadata = msg.metadata_ or {}
    meta_attachments = metadata.get("attachments") if isinstance(metadata, dict) else None
    attachment_name = None
    if isinstance(meta_attachments, list):
        for meta_attachment in meta_attachments:
            if not isinstance(meta_attachment, dict):
                continue
            payload_value = meta_attachment.get("payload")
            payload_data = payload_value if isinstance(payload_value, dict) else {}
            file_name = (
                meta_attachment.get("file_name")
                or payload_data.get("file_name")
                or payload_data.get("filename")
                or payload_data.get("name")
            )
            if isinstance(file_name, str) and file_name.strip():
                attachment_name = file_name.strip()
                break
    attachment_caption = None
    if isinstance(meta_attachments, list):
        for idx, meta_attachment in enumerate(meta_attachments):
            if not isinstance(meta_attachment, dict):
                continue
            payload_value = meta_attachment.get("payload")
            payload: dict = payload_value if isinstance(payload_value, dict) else {}
            if attachment_caption is None:
                caption = payload.get("caption")
                if isinstance(caption, str) and caption.strip():
                    attachment_caption = caption.strip()
            attachment_id = payload.get("attachment_id") or payload.get("id") or meta_attachment.get("id")
            url = _normalize_storage_attachment_url(payload.get("url") or meta_attachment.get("url"))
            attachment_type = (
                meta_attachment.get("type") or payload.get("content_type") or payload.get("mime_type") or ""
            )
            is_image = attachment_type == "image" or str(attachment_type).startswith("image/")
            if not url and attachment_id:
                url = f"/admin/crm/inbox/attachment/{msg.id}/{idx}"
            file_name = meta_attachment.get("file_name") or f"attachment-{idx + 1}"
            attachments.append(
                {
                    "id": f"meta-{idx + 1}",
                    "file_name": file_name,
                    "mime_type": attachment_type,
                    "file_size": None,
                    "url": url,
                    "content_id": None,
                    "attachment_id": attachment_id,
                    "is_image": is_image,
                }
            )

    content = msg.body or ""
    if content.startswith("[Attachment:") and (attachments or meta_attachments):
        content = ""
    if (
        msg.channel_type == ChannelType.whatsapp
        and (attachments or meta_attachments)
        and content.strip().startswith("[")
        and content.strip().endswith("message]")
    ):
        content = attachment_caption or ""
    content_is_document_placeholder = content.strip() == "[document message]"
    if content.strip() in {"[reaction message]", "[location message]", "[document message]"}:
        content = ""

    call_metadata = metadata if isinstance(metadata, dict) else {}
    call_payload = call_metadata.get("call") if isinstance(call_metadata.get("call"), dict) else {}
    if isinstance(call_payload, dict):
        call_payload = call_payload
    meta_type = call_metadata.get("type")
    meta_type_value = None
    is_call = False
    call_id = None
    call_status = None
    call_type = None
    call_direction = None
    call_to = None
    call_from = None
    call_accepted_by_name = None
    call_accepted_by_person_id = None
    phone_number_id = call_metadata.get("phone_number_id")
    display_phone_number = call_metadata.get("display_phone_number")

    if isinstance(meta_type, str):
        meta_type_value = meta_type.lower()
    if meta_type_value == "call":
        is_call = True

    if not isinstance(phone_number_id, str):
        phone_number_id = None
    if not isinstance(display_phone_number, str):
        display_phone_number = None

    if isinstance(call_payload, dict):
        if call_status is None:
            call_status = call_payload.get("call_status")
        if call_type is None:
            call_type = call_payload.get("type")
        if call_direction is None:
            call_direction = call_payload.get("call_direction") or call_payload.get("direction")
        if call_to is None:
            call_to = call_payload.get("to")
        if call_from is None:
            call_from = call_payload.get("from")
        if call_accepted_by_name is None:
            call_accepted_by_name = call_payload.get("accepted_by_name")
        if call_accepted_by_person_id is None:
            call_accepted_by_person_id = call_payload.get("accepted_by_person_id")

    if isinstance(call_metadata, dict):
        if call_id is None:
            call_id = call_metadata.get("call_id")
        if call_status is None:
            call_status = call_metadata.get("call_status")
        if call_type is None:
            call_type = call_metadata.get("call_type")
        if call_direction is None:
            call_direction = call_metadata.get("call_direction")
        if call_to is None:
            call_to = call_metadata.get("to")
        if call_from is None:
            call_from = call_metadata.get("from")
        if call_accepted_by_name is None:
            call_accepted_by_name = call_metadata.get("accepted_by_name")
        if call_accepted_by_person_id is None:
            call_accepted_by_person_id = call_metadata.get("accepted_by_person_id")

    if isinstance(call_id, str):
        call_id = call_id.strip() or None
    if isinstance(call_status, str):
        call_status = call_status.strip() or None
    if isinstance(call_type, str):
        call_type = call_type.strip() or None
    if isinstance(call_direction, str):
        call_direction = call_direction.strip() or None
    if isinstance(call_to, str):
        call_to = call_to.strip() or None
    if isinstance(call_from, str):
        call_from = call_from.strip() or None
    if isinstance(call_accepted_by_name, str):
        call_accepted_by_name = call_accepted_by_name.strip() or None
    if isinstance(call_accepted_by_person_id, str):
        call_accepted_by_person_id = call_accepted_by_person_id.strip() or None
    call_accepted_by_name, call_accepted_by_person_id = _resolve_call_accepting_agent(
        db,
        msg,
        call_id,
        call_accepted_by_name,
        call_accepted_by_person_id,
    )

    if not content.strip() and content_is_document_placeholder and not (attachments or meta_attachments):
        content = f"Document: {attachment_name}" if attachment_name else "Document attached"

    if not content.strip() and isinstance(metadata, dict):
        meta_type = metadata.get("type")
        if isinstance(meta_type, str):
            meta_type_value = meta_type.lower()
        else:
            meta_type_value = None

        if meta_type_value == "reaction":
            emoji = metadata.get("emoji")
            if isinstance(emoji, str) and emoji.strip():
                content = f"Reaction {emoji.strip()}"
            else:
                content = "Reaction received"
        elif meta_type_value == "call":
            call_status = metadata.get("call_status")
            call_direction = metadata.get("call_direction")
            call_type = metadata.get("call_type")
            if call_status and not isinstance(call_status, str):
                call_status = None
            if call_direction and not isinstance(call_direction, str):
                call_direction = None
            if call_type and not isinstance(call_type, str):
                call_type = None

            if call_status and isinstance(call_status, str):
                status_label = call_status.replace("_", " ").replace("-", " ").strip()
            else:
                status_label = None
            direction_label = call_direction.strip().title() if isinstance(call_direction, str) else None
            type_label = call_type.strip().title() if isinstance(call_type, str) else "Call"
            if direction_label and status_label:
                content = f"☎️ {direction_label} {type_label} ({status_label})"
            elif status_label:
                content = f"☎️ {type_label} ({status_label})"
            elif direction_label:
                content = f"☎️ {direction_label} {type_label}"
            else:
                content = "☎️ Call event"
        elif meta_type_value == "location":
            loc_label = metadata.get("label") or metadata.get("name") or metadata.get("address")
            if not loc_label:
                loc = metadata.get("location")
                if isinstance(loc, dict):
                    loc_label = loc.get("label") or loc.get("name") or loc.get("address")
            if loc_label:
                content = f"📍 {loc_label}"
            else:
                lat = metadata.get("latitude")
                lng = metadata.get("longitude")
                if lat is not None and lng is not None:
                    content = f"📍 https://maps.google.com/?q={lat},{lng}"
                else:
                    content = "📍 Location shared"

    if msg.channel_type == ChannelType.instagram_dm:
        meta_count = len(meta_attachments) if isinstance(meta_attachments, list) else 0
        first_url = attachments[0].get("url") if attachments else None
        logger.info(
            "crm_inbox_ig_attachments message_id=%s meta_count=%s rendered_count=%s first_url=%s",
            msg.id,
            meta_count,
            len(attachments),
            first_url,
        )

    visibility = metadata.get("visibility") if isinstance(metadata, dict) else None
    note_type = metadata.get("type") if isinstance(metadata, dict) else None
    # Internal/system messages are not always private notes (e.g. imported activity events).
    # Treat a message as private only when explicit note/private markers are present.
    is_private_note = (
        msg.channel_type == ChannelType.note
        or note_type == "private_note"
        or bool(metadata.get("private") or metadata.get("chatwoot_private"))
    )

    html_body = metadata.get("html_body") if isinstance(metadata, dict) else None
    if html_body:
        html_body = _replace_cid_images(html_body, attachments)
    html_source = html_body or content
    # If we run plain-text content through the HTML sanitizer, strings like
    # "<user@example.com>" (common in DSN bounce messages) get parsed as tags
    # and can disappear in the UI. For non-HTML bodies, escape as plain text.
    if html_body:
        content_html = _sanitize_message_html(html_source)
    else:
        content_html = _linkify_plain_text(content or "")

    reply_to = metadata.get("reply_to") if isinstance(metadata, dict) else None
    if not reply_to and msg.reply_to_message_id:
        reply_msg = db.get(Message, msg.reply_to_message_id)
        if reply_msg and reply_msg.conversation_id == msg.conversation_id:
            timestamp = reply_msg.sent_at or reply_msg.received_at or reply_msg.created_at
            excerpt = (reply_msg.body or "").strip()
            if len(excerpt) > 240:
                excerpt = excerpt[:237].rstrip() + "..."
            reply_author = "Contact"
            if reply_msg.direction == MessageDirection.internal:
                reply_author = "Internal Note"
            elif reply_msg.direction == MessageDirection.outbound:
                reply_metadata = reply_msg.metadata_ if isinstance(reply_msg.metadata_, dict) else {}
                if reply_metadata.get("ai_intake_generated"):
                    reply_author = "AI"
                elif reply_msg.author_id:
                    reply_person = db.get(Person, reply_msg.author_id)
                    if reply_person:
                        reply_author = (
                            reply_person.display_name
                            or " ".join(
                                part for part in [reply_person.first_name, reply_person.last_name] if part
                            ).strip()
                            or "Agent"
                        )
                    else:
                        reply_author = "Agent"
                else:
                    reply_author = "Agent"
            else:
                conv = reply_msg.conversation
                if conv and conv.contact:
                    reply_author = conv.contact.display_name or conv.contact.email or "Contact"
            reply_to = {
                "id": str(reply_msg.id),
                "author": reply_author,
                "excerpt": excerpt or "Attachment",
                "sent_at": timestamp.isoformat() if timestamp else None,
                "direction": reply_msg.direction.value,
                "channel_type": reply_msg.channel_type.value if reply_msg.channel_type else None,
            }

    timestamp = msg.received_at or msg.sent_at or msg.created_at
    status_value = msg.status.value if msg.status else "received"
    failure_reason_label = _derive_failure_reason_label(
        metadata if isinstance(metadata, dict) else None,
        status=status_value,
        channel_type=msg.channel_type,
    )

    return {
        "id": str(msg.id),
        "conversation_id": str(msg.conversation_id),
        "direction": msg.direction.value,
        "meta_type": meta_type_value,
        "is_call": is_call,
        "content": content,
        "content_html": content_html,
        "html_body": html_body,
        "timestamp": timestamp,
        "timestamp_label": _format_inbox_datetime_label(timestamp, db),
        "status": status_value,
        "failure_reason_label": failure_reason_label,
        "read_at": msg.read_at,
        "attachments": attachments,
        "sender": {
            "name": sender_name,
            "initials": sender_initials,
            "is_ai": sender_is_ai,
        },
        "channel_type": msg.channel_type.value if msg.channel_type else "email",
        "visibility": visibility,
        "is_private_note": is_private_note,
        "author_id": str(msg.author_id) if msg.author_id else None,
        "reply_to": reply_to,
        "reply_to_message_id": str(msg.reply_to_message_id) if msg.reply_to_message_id else None,
        "call_id": call_id,
        "call_status": call_status,
        "call_type": call_type,
        "call_direction": call_direction,
        "call_to": call_to,
        "call_from": call_from,
        "call_accepted_by_name": call_accepted_by_name,
        "call_accepted_by_person_id": call_accepted_by_person_id,
        "phone_number_id": phone_number_id,
        "display_phone_number": display_phone_number,
    }


def format_contact_for_template(contact: Person, db: Session) -> dict:
    """Transform a Contact model into detailed template-friendly dict."""
    channels = []
    for ch in contact.channels or []:
        channels.append(
            {
                "type": ch.channel_type.value,
                "address": ch.address,
                "verified": ch.is_verified,
            }
        )

    company = None
    if contact.organization:
        company = contact.organization.name

    resolved_person_id = str(contact.id)

    tags = contact_service.get_contact_tags(db, resolved_person_id)

    recent_tickets = contact_service.get_contact_recent_tickets(db, resolved_person_id, subscriber_ids=None, limit=3)
    recent_projects = contact_service.get_contact_recent_projects(db, resolved_person_id, subscriber_ids=None, limit=3)
    recent_tasks = contact_service.get_contact_recent_tasks(db, resolved_person_id, subscriber_ids=None, limit=3)
    conversations_summary = contact_service.get_contact_conversations_summary(db, resolved_person_id, limit=5)

    recent_conversations = []
    recent_convs = contact_service.get_contact_recent_conversations(db, resolved_person_id, limit=5)
    for conv in recent_convs:
        conv_payload = format_conversation_for_template(conv, db)
        last_message_at = conv_payload.get("last_message_at")
        if isinstance(last_message_at, str):
            try:
                last_message_at = datetime.fromisoformat(last_message_at)
            except ValueError:
                last_message_at = None
        recent_conversations.append(
            {
                "id": str(conv.id),
                "subject": conv_payload.get("subject") or f"Conversation {str(conv.id)[:8]}",
                "status": conv_payload.get("status") or (conv.status.value if conv.status else "open"),
                "updated_at": last_message_at.strftime("%Y-%m-%d %H:%M") if last_message_at else "N/A",
                "preview": conv_payload.get("preview") or "No messages yet",
                "channel": conv_payload.get("channel"),
                "sort_at": last_message_at or conv.updated_at,
                "href": f"/admin/crm/inbox?conversation_id={conv.id}",
            }
        )

    recent_conversations = merge_recent_conversations_with_comments(
        db,
        resolved_person_id,
        recent_conversations,
        limit=5,
    )

    resolved_data = contact_service.get_contact_resolved_conversations(db, resolved_person_id)
    resolved_conversations = []
    for conv_data in resolved_data:
        last_message_at = conv_data.get("last_message_at") or conv_data.get("updated_at")
        resolved_conversations.append(
            {
                "id": conv_data["id"],
                "subject": conv_data["subject"],
                "status": conv_data["status"],
                "updated_at": last_message_at.strftime("%Y-%m-%d %H:%M") if last_message_at else "N/A",
                "preview": "No messages yet",
                "channel": None,
                "sort_at": last_message_at,
                "href": f"/admin/crm/inbox?conversation_id={conv_data['id']}",
            }
        )

    total_conversations = len(contact.conversations) if contact.conversations else 0

    splynx_id = None
    if contact.metadata_:
        splynx_id = contact.metadata_.get("splynx_id")

    address_parts = [
        contact.address_line1,
        contact.address_line2,
        contact.city,
        contact.region,
        contact.postal_code,
        contact.country_code,
    ]
    address_text = ", ".join([part for part in address_parts if part])
    phone_display = contact.phone or ""
    if phone_display and not phone_display.startswith("+"):
        phone_display = f"+{phone_display}"

    return {
        "id": str(contact.id),
        "name": contact.display_name or phone_display or contact.email or "Unknown",
        "email": contact.email,
        "phone": phone_display,
        "company": company,
        "is_active": contact.is_active,
        "avatar_initials": get_initials(contact.display_name or contact.email),
        "channels": channels,
        "tags": list(tags)[:5],
        "subscriber": None,
        "splynx_id": splynx_id,
        "recent_tickets": recent_tickets,
        "recent_projects": recent_projects,
        "recent_tasks": recent_tasks,
        "notes": contact.notes,
        "address": {
            "line1": contact.address_line1,
            "line2": contact.address_line2,
            "city": contact.city,
            "region": contact.region,
            "postal_code": contact.postal_code,
            "country_code": contact.country_code,
            "text": address_text,
        },
        "total_conversations": total_conversations,
        "conversations": conversations_summary,
        "recent_conversations": recent_conversations,
        "resolved_conversations": resolved_conversations,
        "avg_response_time": "N/A",
    }
