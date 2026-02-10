"""Formatting helpers for CRM inbox admin UI."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection
from app.models.integration import IntegrationTarget
from app.models.person import Person
from app.models.subscriber import Organization
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.comments_summary import (
    merge_recent_conversations_with_comments,
)
from app.services.crm.inbox.permissions import can_view_private_note

logger = logging.getLogger(__name__)


def get_initials(name: str | None) -> str:
    """Generate initials from a name."""
    if not name:
        return "?"
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[0:2].upper() if len(name) >= 2 else name[0].upper()


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
    return parsed.scheme == ""


def _sanitize_message_html(value: str) -> str:
    if not value:
        return ""
    sanitizer = _MessageHTMLSanitizer()
    sanitizer.feed(value)
    sanitizer.close()
    return sanitizer.get_html()


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
            latest_message.get("channel_type")
            if isinstance(latest_message, dict)
            else latest_message.channel_type
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
                        full_name = person.display_name or " ".join(
                            part
                            for part in [person.first_name, person.last_name]
                            if part
                        ).strip()
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
            metadata = (
                latest_message.metadata_ if isinstance(latest_message.metadata_, dict) else None
            )
            message_type = metadata.get("type") if metadata else None
            has_attachments = bool(getattr(latest_message, "attachments", None))

        body_text = body.strip() if isinstance(body, str) else ""
        if body_text in {"[reaction message]", "[location message]"}:
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
        latest_message_at = (
            latest_message.received_at
            or latest_message.sent_at
            or latest_message.created_at
        )

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
        if channel in ("whatsapp", "sms", "phone"):
            contact_name = contact.display_name or phone_value or contact.email or "Unknown"
        else:
            contact_name = contact.display_name or contact.email or phone_value or "Unknown"
        contact_initials = get_initials(contact_name)
    else:
        contact_name = "Unknown"
        contact_initials = "?"
        phone_value = None

    splynx_id = None
    if contact and contact.metadata_:
        splynx_id = contact.metadata_.get("splynx_id")

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
        "subject": conv.subject,
        "preview": preview,
        "unread_count": unread_count or 0,
        "last_message_at": conv.last_message_at or latest_message_at or conv.updated_at,
        "assigned_to": assigned_to,
        "assigned_team": assigned_team,
        "assigned_agent_id": assigned_agent_id,
        "assigned_agent_name": assigned_agent_name,
        "inbox": {
            "id": str(channel_target_id) if channel_target_id else None,
            "label": inbox_label,
        },
    }


def format_message_for_template(msg: Message, db: Session) -> dict:
    """Transform a Message model into template-friendly dict."""
    sender_name = "Unknown"
    sender_initials = "?"

    if msg.direction == MessageDirection.internal:
        if msg.author_id:
            person = db.get(Person, msg.author_id)
            if person:
                full_name = person.display_name or " ".join(
                    part for part in [person.first_name, person.last_name] if part
                ).strip()
                sender_name = full_name or "Internal Note"
                sender_initials = get_initials(sender_name)
            else:
                sender_name = "Internal Note"
                sender_initials = "IN"
        else:
            sender_name = "Internal Note"
            sender_initials = "IN"
    elif msg.direction == MessageDirection.outbound:
        if msg.author_id:
            person = db.get(Person, msg.author_id)
            if person:
                full_name = person.display_name or " ".join(
                    part for part in [person.first_name, person.last_name] if part
                ).strip()
                sender_name = full_name or "Agent"
                sender_initials = get_initials(sender_name)
        else:
            sender_name = "Agent"
            sender_initials = "AG"
    else:
        conv = msg.conversation
        if conv and conv.contact:
            sender_name = conv.contact.display_name or conv.contact.email or "Contact"
            sender_initials = get_initials(sender_name)

    attachments = []
    for attachment in msg.attachments or []:
        metadata = attachment.metadata_ or {}
        content_base64 = metadata.get("content_base64")
        content_id = metadata.get("content_id")
        url = attachment.external_url
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
                "is_image": bool(
                    attachment.mime_type and attachment.mime_type.startswith("image/")
                ),
            }
        )

    metadata = msg.metadata_ or {}
    meta_attachments = metadata.get("attachments") if isinstance(metadata, dict) else None
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
            attachment_id = (
                payload.get("attachment_id")
                or payload.get("id")
                or meta_attachment.get("id")
            )
            url = payload.get("url") or meta_attachment.get("url")
            attachment_type = (
                meta_attachment.get("type")
                or payload.get("content_type")
                or payload.get("mime_type")
                or ""
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
    if content.strip() in {"[reaction message]", "[location message]"}:
        content = ""

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
        elif meta_type_value == "location":
            loc_label = (
                metadata.get("label")
                or metadata.get("name")
                or metadata.get("address")
            )
            if not loc_label:
                loc = metadata.get("location")
                if isinstance(loc, dict):
                    loc_label = (
                        loc.get("label")
                        or loc.get("name")
                        or loc.get("address")
                    )
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
    is_private_note = (
        msg.direction == MessageDirection.internal
        or msg.channel_type == ChannelType.note
        or note_type == "private_note"
    )

    html_body = metadata.get("html_body") if isinstance(metadata, dict) else None
    if html_body:
        html_body = _replace_cid_images(html_body, attachments)
    html_source = html_body or content
    if not html_body and "&lt;" in content and "&gt;" in content:
        html_source = html.unescape(content)

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
                if reply_msg.author_id:
                    reply_person = db.get(Person, reply_msg.author_id)
                    if reply_person:
                        reply_author = (
                            reply_person.display_name
                            or " ".join(
                                part
                                for part in [reply_person.first_name, reply_person.last_name]
                                if part
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

    return {
        "id": str(msg.id),
        "conversation_id": str(msg.conversation_id),
        "direction": msg.direction.value,
        "content": content,
        "content_html": _sanitize_message_html(html_source),
        "html_body": html_body,
        "timestamp": msg.received_at or msg.sent_at or msg.created_at,
        "status": msg.status.value if msg.status else "received",
        "read_at": msg.read_at,
        "attachments": attachments,
        "sender": {
            "name": sender_name,
            "initials": sender_initials,
        },
        "channel_type": msg.channel_type.value if msg.channel_type else "email",
        "visibility": visibility,
        "is_private_note": is_private_note,
        "author_id": str(msg.author_id) if msg.author_id else None,
        "reply_to": reply_to,
        "reply_to_message_id": str(msg.reply_to_message_id) if msg.reply_to_message_id else None,
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

    recent_tickets = contact_service.get_contact_recent_tickets(
        db, resolved_person_id, subscriber_ids=None, limit=3
    )
    recent_projects = contact_service.get_contact_recent_projects(
        db, resolved_person_id, subscriber_ids=None, limit=3
    )
    recent_tasks = contact_service.get_contact_recent_tasks(
        db, resolved_person_id, subscriber_ids=None, limit=3
    )
    conversations_summary = contact_service.get_contact_conversations_summary(
        db, resolved_person_id, limit=5
    )

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
                "subject": conv_payload.get("subject")
                or f"Conversation {str(conv.id)[:8]}",
                "status": conv_payload.get("status")
                or (conv.status.value if conv.status else "open"),
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
